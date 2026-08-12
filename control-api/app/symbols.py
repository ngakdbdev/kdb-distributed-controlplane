"""
symbols.py - a reference universe of tradable symbols across markets, for the
symbol pickers (shard assignment, query filters).

HONEST COVERAGE NOTE: a complete "every symbol on every exchange" list is a
large, licensed, constantly-changing dataset - the same reason the exchange
feeds are licensed. So this ships a representative seed across the major
markets, extended live: app/symbol_discovery.py's background poll loop asks
every RDB shard what's actually trading and folds anything not already in
the seed into _live_symbols below, so a symbol that starts flowing through
a live feed (a new crypto pair, a newly-enabled connector) shows up in
search()/markets() automatically - not just the seed, and not a manually-
maintained file either.
"""
from __future__ import annotations

import threading

# (symbol, name, market, currency)
_SEED = [
    # US - NASDAQ / NYSE
    ("AAPL", "Apple Inc.", "NASDAQ", "USD"), ("MSFT", "Microsoft Corp.", "NASDAQ", "USD"),
    ("GOOGL", "Alphabet Inc.", "NASDAQ", "USD"), ("AMZN", "Amazon.com Inc.", "NASDAQ", "USD"),
    ("TSLA", "Tesla Inc.", "NASDAQ", "USD"), ("NVDA", "NVIDIA Corp.", "NASDAQ", "USD"),
    ("META", "Meta Platforms Inc.", "NASDAQ", "USD"), ("NFLX", "Netflix Inc.", "NASDAQ", "USD"),
    ("JPM", "JPMorgan Chase & Co.", "NYSE", "USD"), ("BAC", "Bank of America Corp.", "NYSE", "USD"),
    ("XOM", "Exxon Mobil Corp.", "NYSE", "USD"), ("WMT", "Walmart Inc.", "NYSE", "USD"),
    ("V", "Visa Inc.", "NYSE", "USD"), ("KO", "Coca-Cola Co.", "NYSE", "USD"),
    # UK - LSE
    ("VOD", "Vodafone Group plc", "LSE", "GBP"), ("HSBA", "HSBC Holdings plc", "LSE", "GBP"),
    ("BP", "BP plc", "LSE", "GBP"), ("SHEL", "Shell plc", "LSE", "GBP"),
    ("AZN", "AstraZeneca plc", "LSE", "GBP"), ("GLEN", "Glencore plc", "LSE", "GBP"),
    ("BARC", "Barclays plc", "LSE", "GBP"), ("RIO", "Rio Tinto plc", "LSE", "GBP"),
    # India - NSE / BSE
    ("RELIANCE", "Reliance Industries", "NSE", "INR"), ("TCS", "Tata Consultancy Services", "NSE", "INR"),
    ("INFY", "Infosys Ltd.", "NSE", "INR"), ("HDFCBANK", "HDFC Bank Ltd.", "NSE", "INR"),
    ("ICICIBANK", "ICICI Bank Ltd.", "NSE", "INR"), ("SBIN", "State Bank of India", "NSE", "INR"),
    ("BHARTIARTL", "Bharti Airtel Ltd.", "NSE", "INR"), ("ITC", "ITC Ltd.", "BSE", "INR"),
    ("HINDUNILVR", "Hindustan Unilever", "BSE", "INR"), ("WIPRO", "Wipro Ltd.", "BSE", "INR"),
    # Germany - XETRA
    ("SAP", "SAP SE", "XETRA", "EUR"), ("SIE", "Siemens AG", "XETRA", "EUR"),
    ("ALV", "Allianz SE", "XETRA", "EUR"), ("BMW", "Bayerische Motoren Werke AG", "XETRA", "EUR"),
    ("BAS", "BASF SE", "XETRA", "EUR"), ("DTE", "Deutsche Telekom AG", "XETRA", "EUR"),
    # Japan - TSE
    ("7203", "Toyota Motor Corp.", "TSE", "JPY"), ("6758", "Sony Group Corp.", "TSE", "JPY"),
    ("9984", "SoftBank Group Corp.", "TSE", "JPY"), ("6861", "Keyence Corp.", "TSE", "JPY"),
    # Hong Kong - HKEX
    ("0700", "Tencent Holdings Ltd.", "HKEX", "HKD"), ("9988", "Alibaba Group", "HKEX", "HKD"),
    ("0005", "HSBC Holdings plc", "HKEX", "HKD"), ("1299", "AIA Group Ltd.", "HKEX", "HKD"),
    # Australia - ASX
    ("BHP", "BHP Group Ltd.", "ASX", "AUD"), ("CBA", "Commonwealth Bank", "ASX", "AUD"),
    ("CSL", "CSL Ltd.", "ASX", "AUD"),
    # Canada - TSX
    ("RY", "Royal Bank of Canada", "TSX", "CAD"), ("SHOP", "Shopify Inc.", "TSX", "CAD"),
    ("ENB", "Enbridge Inc.", "TSX", "CAD"),
]

SYMBOLS = [{"symbol": s, "name": n, "market": m, "currency": c, "source": "seed"} for s, n, m, c in _SEED]
_SEED_SYMS = {r["symbol"] for r in SYMBOLS}

_live_lock = threading.Lock()
_live_symbols: dict[str, dict] = {}  # symbol -> reference row, source="live"


# Quote currencies recognized in a SEPARATOR-LESS crypto pair (Binance/
# Bybit's own format, e.g. BTCUSDT) - longest first, so USDT matches before
# a false-positive on a shorter suffix.
_CONCAT_QUOTES = ("USDT", "USDC", "BUSD", "USD", "EUR", "GBP", "BTC", "ETH")


def _infer_market(sym: str) -> tuple[str, str]:
    """Best-effort (market, currency) guess for a symbol discovered live, not
    from the curated seed - crypto pairs are self-describing (BASE-QUOTE,
    BASE/QUOTE, or BASEQUOTE with no separator at all); anything else just
    gets tagged LIVE with an unknown currency rather than guessed wrong."""
    for sep in ("-", "/"):
        if sep in sym:
            _base, _, quote = sym.partition(sep)
            return "CRYPTO", quote.upper()
    up = sym.upper()
    for quote in _CONCAT_QUOTES:
        if up.endswith(quote) and len(up) > len(quote):
            return "CRYPTO", quote
    return "LIVE", ""


def merge_live_symbols(live_syms) -> set:
    """Fold newly-seen live symbols (from symbol_discovery.py's periodic
    universe scan) into the reference set. Only ADDS - never removes a seed
    entry, never overwrites one already merged. Returns the symbols that
    were actually new, so the caller can log something useful."""
    added = set()
    with _live_lock:
        for sym in live_syms:
            if sym in _SEED_SYMS or sym in _live_symbols:
                continue
            market, currency = _infer_market(sym)
            _live_symbols[sym] = {"symbol": sym, "name": sym, "market": market,
                                  "currency": currency, "source": "live"}
            added.add(sym)
    return added


def _all_symbols() -> list:
    with _live_lock:
        return SYMBOLS + list(_live_symbols.values())


def live_count() -> int:
    with _live_lock:
        return len(_live_symbols)


def markets() -> list:
    """Distinct markets in the reference (seed + live-discovered), with counts."""
    by_market: dict[str, list] = {}
    for row in _all_symbols():
        by_market.setdefault(row["market"], []).append(row)
    return sorted(({"market": m, "count": len(rows)} for m, rows in by_market.items()),
                  key=lambda x: x["market"])


def search(query: str = "", market: str | None = None, limit: int = 25) -> list:
    """Case-insensitive match on symbol or name, optionally within one
    market, across the seed AND whatever's been discovered live."""
    q = (query or "").strip().upper()
    pool = _all_symbols()
    if market:
        pool = [r for r in pool if r["market"] == market]
    if not q:
        hits = list(pool)
    else:
        hits = [r for r in pool if q in r["symbol"].upper() or q in r["name"].upper()]
    # exact-symbol matches first, then prefix, then the rest
    hits.sort(key=lambda r: (r["symbol"].upper() != q, not r["symbol"].upper().startswith(q),
                             r["symbol"]))
    return hits[:max(1, min(limit, 200))]
