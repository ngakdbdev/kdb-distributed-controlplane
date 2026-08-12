# Market data providers

Pluggable feed adapters that stream a real (or licensed) market-data source
into the sharded tickerplants, behind the same on/off model as the built-in
sims. Each adapter normalizes its vendor's ticks into the canonical `trade`
row and publishes through the real `ShardedPublisher` — routed across shards by
`topology.shard_of`, so a provider "just works" at any shard count.

## Two tiers

| provider | tier | needs |
|---|---|---|
| Finnhub | **live** (ws) | free API key (`FINNHUB_API_KEY`) — real-time US trades |
| Twelve Data | **live** (ws) | API key (`TWELVEDATA_API_KEY`) — global incl. NSE/BSE (paid plan for global) |
| Polygon.io | **live** (ws) | API key (`POLYGON_API_KEY`) — US, real-time on paid tiers |
| Coinbase | **live** (ws) | no key — public feed, real-time crypto spot trades (pairs like `BTC-USD`) |
| Kraken | **live** (ws) | no key — public feed, real-time crypto spot trades (pairs like `BTC/USD`) |
| Yahoo Finance | **live** (poll) | no key — **UNOFFICIAL** endpoint: delayed, rate-limited, ToS-restricted, not for production |
| Alpha Vantage | **live** (poll) | free API key (`ALPHAVANTAGE_API_KEY`) — official; free tier ~25 calls/day |
| NYSE (Pillar) | licensed | NYSE data agreement + entitlements + feed connectivity + Pillar handler |
| LSEG (Refinitiv) | licensed | LSEG credentials/entitlements + LSEG Data Library / Real-Time SDK |
| NSE (India) | licensed | NSE market-data licensing (membership or vendor) + connectivity |
| BSE (India) | licensed | BSE market-data licensing (exchange/vendor) + connectivity |

Websocket adapters (ws) stream trades; polling adapters (poll) fetch delayed
quotes on an interval. Yahoo has no official API — it uses the same unofficial
endpoint yfinance does, so treat it as demo-grade, not production.

**Coinbase/Kraken and the `size` column**: the shared `trade` schema types
`size` as a whole-number long (built for equity share counts). Crypto trade
quantities are fractional (0.01 BTC is an entirely ordinary trade), so
sub-1 quantities round to 0 rather than silently truncating in a way that
misrepresents larger trades - a real, disclosed precision loss until the
schema grows a proper lot-size/decimals-per-symbol concept, not something
a feed adapter should paper over on its own.

**Live** adapters you can run today with a key. **Licensed** adapters are coded
to the real SDK/protocol shape but refuse with a clear "here's what it needs"
message until you plug in your agreement, entitlements, and connectivity — they
never fake a connection. That refusal message is exactly the seam a prospect's
own feed lands on.

## Run it

```bash
pip install -r providers/requirements.txt      # websocket-client, for live feeds
cd data-plane/feeds

# see everything
python -m providers.runner --list

# stream real Finnhub trades into a 2-shard stack
FINNHUB_API_KEY=xxx python -m providers.runner \
    --provider finnhub --symbols AAPL,MSFT,GOOGL --shards 2

# a licensed feed tells you what it needs and exits (no fake connection)
python -m providers.runner --provider lseg --symbols VOD.L
```

The runner reuses the real `ShardedPublisher`, so ticks land in the same
tickerplants the sims and gateway already use — enable the provider, watch the
Metrics tab climb, and the self-healing / load-test stories all apply unchanged.

## Adding another provider

1. Subclass `base.MarketDataProvider`, set the catalog metadata
   (`name/display_name/live/coverage/requires`).
2. Add a pure parser to `normalize.py` (vendor payload → `Tick` list) and a
   test for it — that's the part that has to be exact.
3. Implement `run()` (connect + subscribe + loop → `_publish`), and register the
   class in `providers/__init__.py`.

## Layout & tests

`normalize.py` (pure parsers) and the registry/publish plumbing are unit-tested
with fakes — no sockets, no cluster:

```bash
cd data-plane/feeds && python -m pytest providers/tests
```

The websocket connections and licensed SDKs can only run against the real
vendor/feed, so those aren't exercised in CI — the parsing and routing that CAN
be tested, are.
