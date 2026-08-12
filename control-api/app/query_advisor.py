"""
query_advisor.py - explain / flag issues / suggest an optimized rewrite /
suggest related follow-up queries for a q expression the user already wrote
(by hand or via nl2q.py). Powers POST /query/analyze.

Two very different kinds of "smart" are combined here, deliberately kept
separate rather than both left to the model:

  * Shard-routing advice is DETERMINISTIC - topology.shard_of() is the exact
    mapping the gateway itself uses, so we can prove a narrower shard target
    is correct rather than have the model guess at it (see query_router.py).
    This is always accurate and is prepended to `issues` whether or not an
    LLM is even configured.
  * Everything else (plain-English explanation, style/performance nits,
    "did you mean" rewrites, related queries to try next) genuinely needs a
    model - grounded the same way nl2q.py is, in this TickHouse's live
    schema and the same q-syntax primer, so the advice stays consistent with
    what nl2q.py itself would generate.

If no LLM is configured, analyze() still returns the deterministic routing
tip (if any) with the LLM-only fields left null - there's real value here
even with zero model configured.
"""
from __future__ import annotations

import re

from . import llm_provider, nl2q, query_router, topology

_SYSTEM_PROMPT_TEMPLATE = """You are a q/kdb+ performance and correctness reviewer for a live trading \
system's query workspace. You will be given one qSQL expression a user already wrote (by hand, or from an \
earlier natural-language generation). Analyze it and respond in EXACTLY this format, with all four \
headers present even when a section has nothing to say (write "none" in that case) - no markdown, no \
extra commentary outside these sections:

EXPLANATION: <one or two plain-English sentences describing what this query returns>
ISSUES: <one issue per line, each a plain sentence; or the single word "none">
OPTIMIZED: <a rewritten q expression that returns the same result faster/cleaner, or the single word \
"none" if there is no meaningful improvement - do NOT rewrite just to rewrite>
SUGGESTIONS: <2-3 lines, each "<q expression> :: <short label of what it shows>", related follow-up \
queries a trading analyst would plausibly want next>

# Schema (the only tables that exist)
{schema_block}

# q/kdb+ facts to check the query against
- q has NO "order by" - if you see it, that's a hard error, flag it and put the xdesc/xasc + #() \
  rewrite in OPTIMIZED.
- `N#(select ... from t where ...)` computes the WHOLE where-clause result before truncating; \
  `select[N] from t where ...` (bracket count) only materializes N rows and is strictly cheaper for a \
  plain "first N, no sort needed" request - flag the difference when you see #() used for a plain \
  unsorted top-N and put the select[N] rewrite in OPTIMIZED.
- Comparing a column to `avg`/`sum`/etc. of itself in a `where` clause is correct q (a per-row filter \
  against the aggregate), not a bug - do not flag it.
- `select from t` with no `where` returns the entire table - flag this only as an informational note \
  (large result), not as an error.
- If specific columns are selected (`select c1,c2 from t`) that's normal column pruning, not an issue.

Only put something in OPTIMIZED if it is a real, checkable improvement (a genuine correctness fix, a \
documented cheaper idiom above, or removing clearly dead/redundant clauses) - never rewrite purely for style.
"""


class NotConfigured(RuntimeError):
    """No LLM provider is configured - deterministic-only analysis still runs."""


_SECTION_RE = re.compile(
    r"^(EXPLANATION|ISSUES|OPTIMIZED|SUGGESTIONS)\s*:\s*(.*?)(?=^\s*(?:EXPLANATION|ISSUES|OPTIMIZED|SUGGESTIONS)\s*:|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def _parse(raw: str) -> dict:
    sections = {m.group(1).upper(): m.group(2).strip() for m in _SECTION_RE.finditer(raw)}

    explanation = sections.get("EXPLANATION", "").strip() or None

    issues_raw = sections.get("ISSUES", "").strip()
    issues = [] if not issues_raw or issues_raw.lower() == "none" else [
        ln.strip(" -\t") for ln in issues_raw.splitlines() if ln.strip()
    ]

    optimized_raw = sections.get("OPTIMIZED", "").strip()
    optimized = None if not optimized_raw or optimized_raw.lower() == "none" else optimized_raw

    suggestions_raw = sections.get("SUGGESTIONS", "").strip()
    suggestions = []
    if suggestions_raw and suggestions_raw.lower() != "none":
        for ln in suggestions_raw.splitlines():
            ln = ln.strip(" -\t")
            if not ln:
                continue
            if "::" in ln:
                q, label = ln.split("::", 1)
                suggestions.append({"q": q.strip(), "label": label.strip().strip("\"'")})
            else:
                suggestions.append({"q": ln, "label": ""})

    return {"explanation": explanation, "issues": issues, "optimized_q": optimized,
            "suggestions": suggestions}


_SELECT_OR_EXEC_RE = re.compile(r"^\s*(select|exec)\b", re.IGNORECASE)
_WHERE_OR_BY_RE = re.compile(r"\b(where|by)\b", re.IGNORECASE)
_BY_RE = re.compile(r"\bby\b", re.IGNORECASE)
_AGG_FUNC_RE = re.compile(
    r"\b(avg|sum|count|min|max|first|last|wavg|wsum|med|dev|sdev|var|svar|distinct)\b",
    re.IGNORECASE,
)


def _scan_risk_tip(query: str) -> str | None:
    """The other piece of advice we can PROVE rather than guess: a select/exec
    with a `where` or `by` clause that doesn't narrow by symbol - so
    query_router.extract_syms() finds nothing to route on - has to scan the
    ENTIRE table on every shard it reaches before it can filter or group,
    with cost proportional to total row count regardless of how few rows the
    query ultimately returns. Confirmed live against this deployment: a
    non-aggregated `by` over ~500K rows took ~7s; the same shape hit the
    16-29M rows this demo's RDBs briefly grew to during a tick-rate
    experiment and took 30s+, past every client/server timeout (see
    query_service._cap_result_rows - that fix bounds what a PLAIN select
    returns, but structurally can't help a where/by clause, which has to
    scan before it can filter or group in the first place).
    """
    q = query.strip()
    if not _SELECT_OR_EXEC_RE.match(q) or not _WHERE_OR_BY_RE.search(q):
        return None
    if query_router.extract_syms(q):
        return None  # narrowed by symbol - bounded to what that shard actually owns

    by_match = _BY_RE.search(q)
    if by_match:
        if _AGG_FUNC_RE.search(q[:by_match.start()]):
            return None  # aggregated - one row per group, stays fast at any table size
        return (
            "this `by` groups every row into per-symbol lists with no aggregate function - that "
            "has to scan and copy the WHOLE table regardless of table size, and gets proportionally "
            "slower as it grows; an aggregate (count i, avg price, ...) by the same group produces "
            "one row per symbol instead and stays fast at any table size"
        )
    return (
        "no symbol filter here, so this scans the entire table on every shard it reaches - cost "
        "scales with total row count, not with what the query returns; add `where sym in (...)` "
        "(or target a specific shard) to keep this bounded as the table grows"
    )


def _routing_tip(query: str, shard_count: int) -> str | None:
    """The one piece of advice we can PROVE rather than ask a model to guess:
    if this query's sym filter maps to a strict subset of shards, targeting
    that shard directly (or letting the gateway's own intelligent routing -
    see query_router.py - do it automatically) skips the shards that would
    only ever return zero rows for it."""
    routed = query_router.route_shards(query, shard_count)
    if not routed:
        return None
    all_shards = [s.id for s in topology.shards(shard_count)]
    if len(routed) >= len(all_shards):
        return None
    if len(routed) == 1:
        return (
            f"this query only touches symbols owned by shard {routed[0]} - the gateway already "
            f"narrows its fan-out to just that shard automatically; targeting rdb-{routed[0]} "
            f"directly instead of gateway skips that extra hop too"
        )
    label = ", ".join(routed)
    return (
        f"this query only touches symbols owned by shards {label} - the gateway already narrows "
        f"its fan-out to just those shards automatically, skipping the other {len(all_shards) - len(routed)}"
    )


def analyze(query: str, shard_count: int, host: str | None = None, port: int | None = None) -> dict:
    """Deterministic routing + scan-risk tips + (if configured) LLM
    explanation/issues/optimized-rewrite/suggestions. Never raises for "no
    LLM" - callers get the deterministic half regardless; llm_error is set
    (not raised) if a configured provider's call itself fails."""
    deterministic = [_routing_tip(query, shard_count), _scan_risk_tip(query)]
    result = {
        "explanation": None, "issues": [t for t in deterministic if t], "optimized_q": None,
        "suggestions": [], "provider": None, "llm_error": None,
    }

    if not llm_provider.configured():
        result["llm_error"] = "no LLM provider configured"
        return result

    live = nl2q.live_schema(host, port) if (host and port) else None
    system = _SYSTEM_PROMPT_TEMPLATE.format(schema_block=nl2q.schema_block(live))
    try:
        raw = llm_provider.complete(system, query.strip(), max_tokens=700)  # explanation+issues+optimized+suggestions
    except llm_provider.LLMError as exc:
        result["llm_error"] = str(exc)
        return result

    parsed = _parse(raw)
    result["explanation"] = parsed["explanation"]
    result["issues"] = result["issues"] + parsed["issues"]
    result["optimized_q"] = parsed["optimized_q"]
    result["suggestions"] = parsed["suggestions"]
    from . import llm_runtime_config
    result["provider"] = llm_runtime_config.get().provider
    return result
