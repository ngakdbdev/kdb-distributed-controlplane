"""
symbols.py - a reference universe of tradable symbols across markets, for the
symbol pickers (shard assignment, query filters).

HONEST COVERAGE NOTE: a complete "every symbol on every exchange" list is a
large, licensed, constantly-changing dataset - the same reason the exchange
feeds are licensed. So this ships a representative seed across the major
markets and is designed to be extended by a live lookup against the configured
market-data providers (Twelve Data / Finnhub expose symbol directories). Treat
the seed as a starting set, not the whole world.
"""
from __future__ import annotations

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

SYMBOLS = [{"symbol": s, "name": n, "market": m, "currency": c} for s, n, m, c in _SEED]
_BY_MARKET = {}
for _row in SYMBOLS:
    _BY_MARKET.setdefault(_row["market"], []).append(_row)


def markets() -> list:
    """Distinct markets in the reference, with counts."""
    return sorted(({"market": m, "count": len(rows)} for m, rows in _BY_MARKET.items()),
                  key=lambda x: x["market"])


def search(query: str = "", market: str | None = None, limit: int = 25) -> list:
    """Case-insensitive match on symbol or name, optionally within one market."""
    q = (query or "").strip().upper()
    pool = _BY_MARKET.get(market, []) if market else SYMBOLS
    if not q:
        hits = list(pool)
    else:
        hits = [r for r in pool if q in r["symbol"].upper() or q in r["name"].upper()]
    # exact-symbol matches first, then prefix, then the rest
    hits.sort(key=lambda r: (r["symbol"].upper() != q, not r["symbol"].upper().startswith(q),
                             r["symbol"]))
    return hits[:max(1, min(limit, 200))]
