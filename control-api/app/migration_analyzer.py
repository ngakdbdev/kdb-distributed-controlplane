"""
migration_analyzer.py - static analysis of an existing kdb+ codebase, for a
client trying to size the effort of moving off (or onto) this platform.

Pure text/regex heuristics over the q source the client pastes/uploads - no q
interpreter involved, nothing executed. That's a deliberate constraint: this
runs against a prospective client's actual production scripts, so it must
never eval them. Every signal here is mechanically checkable in the source
text; the effort estimate is an explicit, inspectable heuristic (not a model),
so a client can see exactly why a script scored the way it did and argue with
it - that's more useful for a sales conversation than a black-box score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# .z.* handlers that indicate bespoke infra logic (custom IPC/timer/session
# handling) baked into the script itself, rather than using a framework's
# equivalent - the single biggest driver of migration effort, since it means
# behavior is implicit in one team's q code rather than documented/portable.
_RISKY_Z_HANDLERS = {
    ".z.pg": "custom sync-message handler (bespoke request routing)",
    ".z.ps": "custom async-message handler (bespoke publish routing)",
    ".z.ts": "custom timer handler (bespoke scheduling)",
    ".z.wo": "custom open-connection handler (bespoke session setup)",
    ".z.wc": "custom close-connection handler (bespoke session teardown)",
    ".z.pc": "custom peer-close handler (bespoke reconnect logic)",
}

_TABLE_DEF_RE = re.compile(r"^\s*(\.?[a-zA-Z][\w.]*)\s*:\s*\(\s*\[[^\]]*\]", re.MULTILINE)
_FUNC_DEF_RE = re.compile(r"^\s*(\.?[a-zA-Z][\w.]*)\s*:\s*\{", re.MULTILINE)
_NAMESPACE_RE = re.compile(r"(?<![\w.])\.([a-zA-Z][\w]*)\.")
_HOPEN_RE = re.compile(r"\bhopen\b")
_HARDCODED_HOST_RE = re.compile(r"`:(?:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|[a-zA-Z0-9_-]+):\d+")
_LOAD_RE = re.compile(r"^\s*\\l\s+(\S+)", re.MULTILINE)
_SELECT_BY_RE = re.compile(r"\bselect\b[^;\n]*\bby\b", re.IGNORECASE)
_ASYNC_HANDLE_RE = re.compile(r"\(neg\s+\w+\)|neg\[")


@dataclass
class ScriptAnalysis:
    name: str
    loc: int
    tables: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    namespaces: list = field(default_factory=list)
    risky_handlers: list = field(default_factory=list)      # [(handler, why)]
    hardcoded_hosts: int = 0
    loads: list = field(default_factory=list)                # \l dependencies
    aggregation_queries: int = 0
    async_fanout: int = 0


def _strip_comments(text: str) -> str:
    """Drop /-to-end-of-line comments and /.../\\ block comments well enough
    for counting purposes - this is a heuristic scanner, not a q parser, so
    it doesn't need to be byte-perfect on pathological quoting."""
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("/"):
            continue
        # inline comment: a space then / not inside an obvious string literal
        if " /" in line and '"' not in line.split(" /", 1)[1][:1]:
            line = line.split(" /", 1)[0]
        lines.append(line)
    return "\n".join(lines)


def analyze_script(name: str, content: str) -> ScriptAnalysis:
    code = _strip_comments(content)
    loc = len([ln for ln in code.split("\n") if ln.strip()])

    tables = sorted(set(_TABLE_DEF_RE.findall(code)))
    functions = sorted(set(_FUNC_DEF_RE.findall(code)) - set(tables))
    namespaces = sorted({f".{ns}" for ns in _NAMESPACE_RE.findall(code)} - {".z"})

    risky = []
    for handler, why in _RISKY_Z_HANDLERS.items():
        if handler in code:
            risky.append((handler, why))

    hardcoded = len(_HARDCODED_HOST_RE.findall(code))
    loads = _LOAD_RE.findall(code)
    agg = len(_SELECT_BY_RE.findall(code))
    fanout = len(_ASYNC_HANDLE_RE.findall(code))

    return ScriptAnalysis(name=name, loc=loc, tables=tables, functions=functions,
                          namespaces=namespaces, risky_handlers=risky,
                          hardcoded_hosts=hardcoded, loads=loads,
                          aggregation_queries=agg, async_fanout=fanout)


@dataclass
class EffortEstimate:
    size: str                 # S / M / L / XL
    weeks_low: int
    weeks_high: int
    score: int
    factors: list             # human-readable reasons, most-impactful first


_SIZE_BANDS = [
    (0, 15, "S", 1, 2),
    (15, 35, "M", 2, 5),
    (35, 65, "L", 5, 10),
    (65, 10**9, "XL", 10, 20),
]


def estimate_effort(analyses: list) -> EffortEstimate:
    """Score is an explicit sum of weighted, inspectable signals - not a
    trained model. Weights encode which signals actually predict migration
    pain: bespoke IPC handlers and hardcoded topology are worse than raw LOC,
    because they mean behavior has to be re-discovered and re-architected,
    not just ported line-by-line."""
    total_loc = sum(a.loc for a in analyses)
    total_tables = len({t for a in analyses for t in a.tables})
    total_functions = sum(len(a.functions) for a in analyses)
    total_risky = sum(len(a.risky_handlers) for a in analyses)
    total_hardcoded = sum(a.hardcoded_hosts for a in analyses)
    total_agg = sum(a.aggregation_queries for a in analyses)
    script_count = len(analyses)

    score = 0
    factors = []

    score += min(30, total_loc // 50)
    if total_loc > 0:
        factors.append((total_loc // 50, f"{total_loc:,} lines of q across {script_count} script(s)"))

    w = total_risky * 8
    score += w
    if total_risky:
        handlers = sorted({h for a in analyses for h, _ in a.risky_handlers})
        factors.append((w, f"{total_risky} bespoke .z.* handler(s) in use ({', '.join(handlers)}) - "
                            "behavior baked into custom IPC/session logic, not a documented framework"))

    w = total_hardcoded * 5
    score += w
    if total_hardcoded:
        factors.append((w, f"{total_hardcoded} hardcoded host:port reference(s) - "
                            "topology is wired into the code rather than configured"))

    w = min(15, total_functions // 5)
    score += w
    if total_functions:
        factors.append((w, f"{total_functions} custom function(s) across {total_tables} table schema(s)"))

    w = min(10, total_agg)
    score += w
    if total_agg:
        factors.append((w, f"{total_agg} aggregation quer{'y' if total_agg == 1 else 'ies'} "
                            "(select-by) - worth checking these still perform after any resharding"))

    factors.sort(key=lambda f: -f[0])
    size, lo, hi = next((s, l, h) for lo_b, hi_b, s, l, h in _SIZE_BANDS if lo_b <= score < hi_b)
    return EffortEstimate(size=size, weeks_low=lo, weeks_high=hi, score=score,
                          factors=[f[1] for f in factors if f[0] > 0])


def analyze(files: list) -> dict:
    """files: [{"name": str, "content": str}] -> full report dict (the API
    return shape). Empty/whitespace-only files are skipped."""
    analyses = [analyze_script(f["name"], f["content"]) for f in files if (f.get("content") or "").strip()]
    effort = estimate_effort(analyses)
    return {
        "scripts": [
            {"name": a.name, "loc": a.loc, "tables": a.tables, "functions": a.functions,
             "namespaces": a.namespaces,
             "risky_handlers": [{"handler": h, "why": why} for h, why in a.risky_handlers],
             "hardcoded_hosts": a.hardcoded_hosts, "loads": a.loads,
             "aggregation_queries": a.aggregation_queries}
            for a in analyses
        ],
        "totals": {
            "scripts": len(analyses),
            "loc": sum(a.loc for a in analyses),
            "tables": sorted({t for a in analyses for t in a.tables}),
            "functions": sum(len(a.functions) for a in analyses),
            "risky_handlers": sum(len(a.risky_handlers) for a in analyses),
        },
        "effort": {
            "size": effort.size, "weeks_low": effort.weeks_low, "weeks_high": effort.weeks_high,
            "score": effort.score, "factors": effort.factors,
        },
    }
