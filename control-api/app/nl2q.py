"""
nl2q.py - natural language -> q/kdb+ query generation, grounded in this
TickHouse's actual schema and in q's own (often surprising) syntax rules.

This is deliberately a single, well-grounded prompt rather than a
fine-tuned model: the "kdb knowledge" is a system prompt built from (a) a
q/kdb+ syntax primer covering the idioms this UI's users actually need
(qSQL select/where/by, comparing a column to its own aggregate, last-N
limiting) and the specific traps that have bitten this codebase before,
and (b) the live schema of the target TickHouse, fetched over the same
IPC connection the query workspace already uses - falling back to the
static schema.q definitions when the target isn't reachable (e.g. no
target picked yet, or the LLM path is being smoke-tested independent of
a live cluster).

See llm_provider.py for the pluggable model backend. If no provider is
configured (NL2Q_LLM_PROVIDER=none, the default), generate() raises
NotConfigured and the frontend falls back to its offline regex generator.
"""
from __future__ import annotations

import re
import time

from . import llm_provider

# Ground truth when a live target isn't reachable - kept in sync with
# data-plane/q/schema.q. Column order matches the table definition there.
_STATIC_SCHEMA = {
    "trade": ["time", "sym", "price", "size", "side", "venue", "shard"],
    "risk": ["time", "sym", "riskType", "limit", "exposure", "status", "shard"],
}

_SYSTEM_PROMPT_TEMPLATE = """You translate a trader's plain-English request into a single q/kdb+ qSQL \
expression for a live trading system. Output ONLY the q expression - no markdown code fences, no \
explanation, no leading "q:" label, no trailing semicolon-and-commentary. If the request is ambiguous, \
make the most reasonable trading-analyst assumption rather than asking a clarifying question - this box \
has no way to reply to you.

# Schema (the only tables that exist)
{schema_block}

# q/kdb+ syntax rules that matter for this task
- Basic shape: select [c1[:agg1], c2[:agg2], ...] [by g1, g2, ...] from table [where cond1, cond2, ...]
- Omitting a select list returns every column: `select from trade`
- A where clause with multiple conditions is comma-separated, evaluated left to right: \
  `select from trade where sym=`AAPL, price>100`
- String/symbol literals for these columns are backtick symbols, not quoted strings: `` `AAPL ``, not "AAPL".
- Comparing a column to an aggregate of itself (e.g. "price above the average price") is a WHERE-PHRASE, \
  not a top-level aggregation - it still returns matching rows, not a one-row summary:
    correct:   select from trade where price > avg price
    correct:   select from trade where sym=`MSFT, price > avg price
    WRONG:     select avgPrice: avg price from trade where sym=`MSFT   (this collapses to one row and \
               drops the request's actual intent - filtering to matching rows)
- Grouped aggregation uses `by`: `select vwap:size wavg price by sym from trade`
- Taking the first/last N rows of a result wraps the WHOLE select in parens before applying #:
    last N rows:   -N#(select ... from trade where ...)
    first N rows:   N#(select ... from trade where ...)
  Never drop the last-N/first-N request just because a `by` or aggregate is also present in the sentence - \
  if the user asked for both a comparison/filter AND "last N", keep the filter in the where clause and wrap \
  the whole select in the #() limiter; do not turn it into a top-level aggregate instead.
- Common aggregate functions: avg, sum, wavg (size-weighted: `size wavg price`), max, min, count, first, \
  last, dev (stddev), med.
- q has NO "order by" clause - that is SQL, not q, and using it is a hard parse error. To get the \
  highest/lowest N rows by some column, sort with `col xdesc t` (descending) or `col xasc t` (ascending) - \
  `t` can be a table or a parenthesized select - then take N with #:
    correct:   5#(`price xdesc select from trade where sym=`AAPL)     (5 highest-priced AAPL trades)
    correct:   5#(`price xasc select from trade where sym=`AAPL)      (5 lowest-priced AAPL trades)
    WRONG:     select from trade where sym=`AAPL order by price desc  ('order by' does not exist in q)
- Taking the first N rows of an UNSORTED result (plain top-of-table, no xdesc/xasc involved) is more \
  efficient with a bracket count than with #(): `select[N] from trade where ...` only materializes N rows \
  instead of computing the full where-clause result and then truncating. Prefer this form for plain \
  "first N" requests; keep N#(...) / -N#(...) for slicing a result that had to be fully computed anyway \
  (an xdesc/xasc sort, or "last N" which needs the tail of the whole thing).
    correct:   select[5] from trade where sym=`AAPL         (first 5 AAPL trades, no sort needed)
- Time-bucketing uses `xbar` on a time/timestamp column, almost always combined with `by`: \
  `N xbar time.minute` buckets into N-minute windows, `time.hh` into hourly. \
  `select vwap:size wavg price by 5 xbar time.minute, sym from trade where sym=`AAPL` (5-minute VWAP bars).
- Cross-table requests (trade and risk share sym/time/shard but are otherwise unrelated tables - there is \
  no join key relating a trade row to a risk row 1:1) are answered by filtering one table on symbols \
  selected from the other with `exec distinct sym from ... where ...` used inside an `in`, not a join:
    correct:   select from trade where sym in exec distinct sym from risk where status=`breach
  ("trades in symbols that currently have an open/breach risk record")
- If the user names specific columns they want (not "everything"), select exactly those columns instead \
  of the whole row - it is both more correct (matches what was asked) and cheaper to transfer: \
  `select sym, price from trade where sym=`AAPL` when the user only asked for symbol and price.
- `count i` counts rows in a `select`; plain `count trade` counts the whole table.
- Do not use `delete`, `update`, `set`, `system`, `exec` used for anything other than a read, or any \
  filesystem/IPC primitive - this box only ever answers with SELECT-shaped reads.

# Worked examples
User: last 2 records whose price is greater than avg price for sym MSFT, only last 2 records from result
q: -2#(select from trade where sym=`MSFT, price>avg price)

User: vwap by symbol for AAPL
q: select vwap:size wavg price by sym from trade where sym=`AAPL

User: last 100 trades
q: -100#(select from trade)

User: highest 5 priced AAPL trades
q: 5#(`price xdesc select from trade where sym=`AAPL)

User: count of open risk records by risk type
q: select n:count i by riskType from risk where status=`open

User: trades where size is below the average size, grouped by venue
q: select avgSize:avg size by venue from trade where size < avg size

User: show me everything in the risk table
q: select from risk
"""


class NotConfigured(RuntimeError):
    """No LLM provider is configured; caller should use the offline generator."""


def schema_block(live: dict[str, list[str]] | None) -> str:
    schema = live or _STATIC_SCHEMA
    lines = [f"- {table}: {', '.join(cols)}" for table, cols in schema.items()]
    return "\n".join(lines)


_SCHEMA_CACHE: dict[tuple[str, int], tuple[float, dict[str, list[str]] | None]] = {}
_SCHEMA_CACHE_TTL_SEC = 300  # trade/risk's columns essentially never change mid-run


def live_schema(host: str, port: int) -> dict[str, list[str]] | None:
    """Best-effort: ask the actual target for its tables/columns so the
    prompt reflects reality even if a deployment's schema has drifted from
    schema.q. Cached for _SCHEMA_CACHE_TTL_SEC for two reasons, not just
    speed: (a) it was adding up to a 3s IPC round-trip to EVERY nl2q/codegen/
    analyze call for a value that's essentially static, and (b) confirmed
    empirically that the local model's prompt-prefix caching only kicks in
    when the system prompt is byte-identical across calls - re-fetching on
    every call risked silently varying the prompt (open connection succeeds
    sometimes, times out and falls back to static schema other times) and
    defeating that caching, which is the difference between ~1s and ~20s."""
    now = time.monotonic()
    cached = _SCHEMA_CACHE.get((host, port))
    if cached and (now - cached[0]) < _SCHEMA_CACHE_TTL_SEC:
        return cached[1]

    result = _fetch_live_schema(host, port)
    _SCHEMA_CACHE[(host, port)] = (now, result)
    return result


def _fetch_live_schema(host: str, port: int) -> dict[str, list[str]] | None:
    try:
        from qpython import qconnection
    except ImportError:
        return None
    try:
        conn = qconnection.QConnection(host=host, port=port, pandas=False, timeout=3)
        conn.open()
    except Exception:  # noqa: BLE001
        return None
    try:
        tables = [str(t) for t in (conn("tables[]") or [])]
        out: dict[str, list[str]] = {}
        for t in tables:
            try:
                cols = conn(f"cols {t}")
                out[t] = [str(c) for c in (cols or [])]
            except Exception:  # noqa: BLE001
                continue
        return out or None
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


_FENCE_RE = re.compile(r"^```(?:q|k)?\s*|\s*```$", re.MULTILINE)
_LABEL_RE = re.compile(r"^\s*(?:q|query|answer)\s*:\s*", re.IGNORECASE)
# A model that treats the whole answer as "inline code" wraps it in a single
# matching pair of backticks, e.g. `select from trade where sym=`AAPL` -
# confirmed empirically against the local open-weights model. That's
# indistinguishable from a real q symbol literal by position alone, so only
# strip it when what's inside unambiguously starts with a qSQL keyword/idiom
# a real answer would never open a genuine backtick-symbol with.
_WRAPPED_RE = re.compile(
    r"^`(select\b|exec\b|update\b|delete\b|-?\d+#|\d+\s+sublist\b)(.*)`$", re.IGNORECASE | re.DOTALL
)


def _unwrap_inline_backticks(text: str) -> str:
    m = _WRAPPED_RE.match(text)
    return (m.group(1) + m.group(2)) if m else text


def _clean(text: str) -> str:
    text = _FENCE_RE.sub("", text).strip()
    text = _LABEL_RE.sub("", text).strip()
    # a model that ignores "no explanation" sometimes still answers on
    # multiple lines - the q expression is virtually always the last
    # non-empty line once fences/labels are stripped.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    line = lines[-1].strip() if lines else text
    return _unwrap_inline_backticks(line)


def generate(text: str, host: str | None = None, port: int | None = None) -> str:
    """Translate `text` into a q expression using the configured LLM.
    Raises NotConfigured if no provider is set, or llm_provider.LLMError on
    a call failure - both are handled by the /query/nl2q endpoint, which
    reports them so the frontend can fall back to its offline generator."""
    if not llm_provider.configured():
        raise NotConfigured("no LLM provider configured")

    live = live_schema(host, port) if (host and port) else None
    system = _SYSTEM_PROMPT_TEMPLATE.format(schema_block=schema_block(live))
    raw = llm_provider.complete(system, text.strip(), max_tokens=160)  # a q expression is one line
    return _clean(raw)
