"""
query_router.py - intelligent shard routing for the gateway's query fan-out.

The problem: `_run_one`'s gateway path (routers/query.py) always scatters a
market-table query to EVERY rdb shard and merges the results, because a
plain-English/qSQL query doesn't say which shard actually owns the symbols
it cares about. For a query filtered to one or a few symbols, every shard
that doesn't own those symbols returns zero rows - a wasted round trip.

Why narrowing is safe (not just faster): topology.shard_of() partitions the
symbol space by contiguous first-letter ranges - it's the SAME mapping the
gateway itself uses to route. So a shard that doesn't own any symbol
mentioned in the query's `sym` filter is *provably* going to return nothing
for it, whether it's queried or not, aggregate or not. Narrowing the fan-out
to just the owning shard(s) returns identical results, faster - it's not a
heuristic that trades correctness for speed.

The narrowing itself IS a heuristic in the sense that it's a best-effort
regex extraction, not a q parser: if it can't confidently find every symbol
the query touches, it returns None and the caller falls back to the
original full fan-out. It only ever narrows when confident, never guesses.
"""
from __future__ import annotations

import re

from . import topology

# `sym=`AAPL` or `sym in `AAPL`MSFT` or `sym in (`AAPL;`MSFT)` - grab whatever
# comes after `sym =`/`sym in` up to the next comma/`by`/end of string, then
# pull every symbol literal out of that span: either a bare backtick token
# (`AAPL) or a `$"..."` string-cast (`$"ETH-USD" - the safe form for a symbol
# containing characters (hyphen, slash) a bare token can't hold - see
# tradingCore.js's qSym for why the cast form exists at all.
_SYM_CLAUSE_RE = re.compile(
    r"\bsym\s*(?:=|in)\s*([^,]*?)(?=,|\bby\b|$)", re.IGNORECASE
)
_SYM_TOKEN_RE = re.compile(r"`([A-Za-z0-9_.]+)")
_SYM_CAST_RE = re.compile(r'`\$"([^"]+)"')


def extract_syms(query: str) -> list[str] | None:
    """Best-effort: every symbol literal referenced in a `sym=`/`sym in`
    clause. None if the query doesn't filter on sym at all, or filters on it
    in a shape this doesn't recognize (e.g. a computed/joined sym list) -
    callers must treat None as "can't narrow, fan out to everything"."""
    syms: list[str] = []
    for clause in _SYM_CLAUSE_RE.findall(query):
        syms.extend(_SYM_TOKEN_RE.findall(clause))
        syms.extend(_SYM_CAST_RE.findall(clause))
    return sorted(set(syms)) if syms else None


def route_shards(query: str, shard_count: int) -> list[str] | None:
    """Shard ids (e.g. ["s0"]) that could possibly hold data for `query`'s
    symbol filter, or None if no filter was found (caller must fan out to
    every shard)."""
    syms = extract_syms(query)
    if not syms:
        return None
    return sorted({topology.shard_of(s, shard_count) for s in syms})


# --------------------------------------------------------------------------- #
# tier routing (rdb "today" vs idb/hdb "history")
# --------------------------------------------------------------------------- #
# `date = ...` / `date within ...` / `date < ...` etc - same clause-then-token
# shape as the sym matcher above. Captures the operator too, since (unlike sym
# narrowing) the operator changes what "confidently today-only" means: `date =
# .z.d` is exactly today; `date < .z.d` is everything BUT today - very
# different tier needs from the same literal.
_DATE_CLAUSE_RE = re.compile(
    r"\bdate\s*(=|>=|<=|>|<|within|in)\s*([^,]*?)(?=,|\bby\b|$)", re.IGNORECASE
)
_DATE_LITERAL_RE = re.compile(r"\d{4}\.\d{2}\.\d{2}")
_TODAY_ONLY_RE = re.compile(r"^\s*\.z\.[dD]\s*$")


def route_tiers(query: str) -> list[str]:
    """Which of rdb ("today", in-memory) / hdb ("full history", on-disk,
    date-partitioned) tiers a market-table query needs.

    Default - no `date` clause at all, or a clause provably equal to exactly
    today (`date = .z.d`) - is rdb-only, UNCHANGED from before hdb was
    reachable as a query target at all: this keeps the cost/latency profile
    of every existing filtered-by-symbol-only query exactly as it was.

    Anything else (an explicit date literal, a `within`/`in` range, or a
    </<=/>/>= bound against anything) can't be confidently proven "today
    only", so this routes to hdb instead - NOT "rdb + hdb", and NOT idb.
    Confirmed live against this schema: neither rdb's nor idb's `trade`/`risk`
    tables carry a `date` column at all (both are flat, undated buffers -
    rdb is implicitly "today", idb is implicitly "whatever's cached in the
    last N days", with no per-row date to filter on). Only hdb is a real
    kdb+ date-partitioned table, where `date` is a genuine (virtual) column.
    Sending a `date`-filtered query to rdb/idb doesn't just waste a round
    trip the way an over-wide symbol fan-out would - it FAILS outright
    ('date' / column-not-found), for any date value, not just historical
    ones. So unlike route_shards' "widen when unsure" pattern, this is a
    hard tier SWITCH, not a widen: a query either needs today's live buffer
    (rdb) or history (hdb), never usefully both from the identical query
    text, because rdb structurally cannot answer a dated predicate. A caller
    that genuinely wants both today's and historical data for one symbol
    federates two separate queries (one dateless against rdb, one
    date-scoped against hdb) rather than one query against both tiers."""
    clauses = _DATE_CLAUSE_RE.findall(query)
    if not clauses:
        return ["rdb"]
    if len(clauses) == 1:
        op, rhs = clauses[0]
        if op == "=" and _TODAY_ONLY_RE.match(rhs) and not _DATE_LITERAL_RE.search(rhs):
            return ["rdb"]
    return ["hdb"]
