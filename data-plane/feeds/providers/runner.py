"""
runner.py - run a market-data provider into the sharded tickerplants.

  # list every provider and whether it's live or licensed
  python -m providers.runner --list

  # stream real Finnhub trades into a 2-shard stack
  FINNHUB_API_KEY=xxx python -m providers.runner \
      --provider finnhub --symbols AAPL,MSFT,GOOGL --shards 2

  # subscribe to EVERY currently-tradable symbol the venue lists (crypto
  # exchanges only - see fetch_all_symbols() on each provider)
  python -m providers.runner --provider binance --symbols all --shards 2

Live providers (finnhub/twelvedata/polygon) need their API key via --api-key or
the matching *_API_KEY env var. Licensed providers (nyse/lseg/nse/bse) will
explain what they need and exit - they don't fake a connection.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

# make the feeds tree importable (feed_common, topology) - same trick the repo's
# gen_topology.py uses, so we reuse the real publisher rather than a copy.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from providers import catalog, get_provider   # noqa: E402
from providers.base import ProviderNotConfigured, ProviderError  # noqa: E402

_ENV_KEYS = {
    "finnhub": "FINNHUB_API_KEY",
    "twelvedata": "TWELVEDATA_API_KEY",
    "polygon": "POLYGON_API_KEY",
    "alphavantage": "ALPHAVANTAGE_API_KEY",
    "alpaca": "ALPACA_API_KEY_ID",
    # yahoo/coinbase/kraken need no key - yahoo's an unofficial endpoint;
    # coinbase/kraken's market-data (not trading) feeds are fully public
}


def _print_catalog() -> None:
    print("\n  provider      live?   coverage")
    print("  " + "-" * 62)
    for p in catalog():
        print(f"  {p['name']:<13} {'live' if p['live'] else 'lic '}   {p['coverage']}")
    print("\n  live = usable with a public API key; lic = licensed feed (see --provider X, it prints what it needs)\n")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m providers.runner")
    p.add_argument("--list", action="store_true", help="list providers and exit")
    p.add_argument("--provider")
    p.add_argument("--symbols", default=os.environ.get("PROVIDER_SYMBOLS", "AAPL,MSFT,GOOGL,AMZN,TSLA"))
    p.add_argument("--symbols-file", default=os.environ.get("PROVIDER_SYMBOLS_FILE", ""),
                   help="path to a file of symbols (comma or newline separated); overrides --symbols")
    p.add_argument("--shards", type=int, default=int(os.environ.get("SHARD_COUNT", "2")))
    p.add_argument("--api-key", default=None)
    p.add_argument("--tp-host-pattern", default=os.environ.get("TP_HOST_PATTERN", "tp-{shard}"))
    p.add_argument("--tp-port", type=int, default=int(os.environ.get("TP_PORT", "5010")))
    args = p.parse_args(argv)

    if args.list or not args.provider:
        _print_catalog()
        return 0

    try:
        cls = get_provider(args.provider)
    except KeyError as exc:
        print(f"  {exc}")
        return 2

    if args.symbols.strip().lower() == "all":
        fetch_all = getattr(cls, "fetch_all_symbols", None)
        if fetch_all is None:
            print(f"\n  {cls.display_name} doesn't support --symbols all "
                 f"(no fetch_all_symbols - it has no venue-wide instrument-list endpoint)\n")
            return 2
        try:
            symbols = fetch_all()
        except Exception as exc:  # noqa: BLE001 - a network/parse failure here should exit cleanly
            print(f"\n  couldn't fetch {cls.display_name}'s full symbol universe: {exc}\n")
            return 2
        print(f"\n  {cls.display_name}: subscribing to all {len(symbols)} currently-tradable symbols\n")
    elif args.symbols_file and os.path.exists(args.symbols_file):
        with open(args.symbols_file) as _fh:
            symbols = [s.strip() for s in _fh.read().replace("\n", ",").split(",") if s.strip()]
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    api_key = args.api_key or os.environ.get(_ENV_KEYS.get(args.provider, ""), None)

    from feed_common import ShardedPublisher  # noqa: E402
    publisher = ShardedPublisher("providers", shard_count=args.shards,
                                 host_pattern=args.tp_host_pattern, port=args.tp_port)
    # NB: don't connect() eagerly - ShardedPublisher connects each shard lazily
    # on first publish. That way a licensed provider that refuses in run()
    # doesn't hang forever trying to reach tickerplants it will never use.

    # alpaca needs a SECOND credential (secret key, not just the key ID every
    # other provider uses) plus which data feed tier to subscribe to - both
    # unique to it, so kept as an explicit extra-kwargs case rather than
    # widening every other provider's constructor for one adapter's shape.
    extra_kwargs = {}
    if args.provider == "alpaca":
        extra_kwargs["api_secret"] = os.environ.get("ALPACA_API_SECRET_KEY", "")
        extra_kwargs["data_feed"] = os.environ.get("ALPACA_DATA_FEED", "iex")
    elif args.provider == "ibkr":
        # no API key at all - auth lives entirely in the already-running
        # Client Portal Gateway this points at (see ibkr.py's docstring)
        extra_kwargs["gateway_base_url"] = os.environ.get("IBKR_GATEWAY_BASE_URL", "")
        extra_kwargs["verify_ssl"] = os.environ.get("IBKR_GATEWAY_VERIFY_SSL", "false").lower() == "true"

    provider = cls(symbols, publisher, args.shards, api_key=api_key, **extra_kwargs)
    print(f"\n  starting {provider.display_name} -> {args.shards} shards, {len(symbols)} symbols\n")
    try:
        provider.run()
    except ProviderNotConfigured as exc:
        print(f"\n  {provider.display_name} is a licensed feed and isn't configured:\n    {exc}\n")
        return 3
    except ProviderError as exc:
        print(f"\n  {exc}\n")
        return 2
    except KeyboardInterrupt:
        print("\n  stopped.")
    finally:
        publisher.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
