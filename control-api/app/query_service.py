"""
query_service.py - run ad-hoc q queries against a live cluster and shape the
result for the UI's query workspace.

SECURITY - read this. q is not sandboxable the way SQL is: a q process will
happily run `system`, open files/sockets, and write to disk. So:
  * Queries are validated read-only by default (a denylist blocks the obvious
    dangerous constructs). This is DEFENCE IN DEPTH, not a sandbox - a
    determined query can still do surprising things.
  * The REAL boundary is operational: point the workspace at a restricted /
    read-only kdb service, on a network only reachable with the right auth,
    scoped per tenant. Never expose a write-capable prod process here.
  * Row caps + a connection timeout bound blast radius.
Writes are refused unless QUERY_ALLOW_WRITE=1 AND the request opts in - and even
then you're trusting the caller. Keep it off in multi-tenant deployments.
"""
from __future__ import annotations

import re

DEFAULT_ROW_LIMIT = 1000
MAX_ROW_LIMIT = 10000

# Constructs that let a query escape "just read data". Matched case-sensitively
# as whole tokens where sensible. Not exhaustive - q is terse - but it catches
# the obvious foot-guns and signals intent.
_DENY = [
    r"\bsystem\b", r"\bhopen\b", r"\bhclose\b", r"\bhdel\b", r"\bhsym\b",
    r"\bgetenv\b", r"\bsetenv\b", r"\bexit\b", r"\bsave\b", r"\brsave\b",
    r"\bdpft\b", r"\bdsave\b", r"\bread0\b", r"\bread1\b", r"\bvalue\b",
    r"\beval\b", r"\breval\b", r"\bset\b", r"\bupsert\b", r"\.z\.",
    r"\.Q\.dpft", r"\.Q\.dsave", r"[012]:", r"^\s*\\", r"\\\\",
]
_DENY_RE = [re.compile(p, re.MULTILINE) for p in _DENY]


def check_readonly(query: str) -> tuple[bool, str]:
    """(ok, reason). ok=False means the query trips a write/IO/system guard."""
    q = (query or "").strip()
    if not q:
        return False, "empty query"
    for rx in _DENY_RE:
        m = rx.search(q)
        if m:
            tok = m.group(0).strip() or "\\"
            return False, f"blocked in read-only mode: '{tok}'"
    return True, ""


def clamp_limit(limit) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_ROW_LIMIT
    return max(1, min(n, MAX_ROW_LIMIT))


# --------------------------------------------------------------------------- #
# result shaping: q result -> {columns, rows, row_count, truncated, kind}
# --------------------------------------------------------------------------- #
def _jsonable(v):
    """Coerce a q/numpy scalar to something JSON can carry."""
    import datetime as _dt
    if v is None:
        return None
    if isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    # numpy scalars expose .item(); fall back to str
    item = getattr(v, "item", None)
    if callable(item):
        try:
            return _jsonable(item())
        except Exception:  # noqa: BLE001
            pass
    return str(v)


def shape_result(result, limit: int = DEFAULT_ROW_LIMIT) -> dict:
    """Turn a q result (table / dict / vector / scalar) into a grid payload.

    Accepts, for testability and for real qpython results:
      - dict {colname: [values...]}  -> a table
      - list/tuple                   -> a single 'value' column
      - scalar                       -> a 1x1 grid
      - a qpython QTable (numpy)     -> converted via its dtype names
    """
    limit = clamp_limit(limit)

    # qpython QTable / numpy structured array -> dict of columns
    if hasattr(result, "dtype") and getattr(result.dtype, "names", None):
        result = {n: list(result[n]) for n in result.dtype.names}

    if isinstance(result, dict):
        # a table (columns of equal length) vs a plain q dict (key->val)
        cols = list(result.keys())
        vals = list(result.values())
        if vals and all(isinstance(v, (list, tuple)) for v in vals):
            total = max((len(v) for v in vals), default=0)
            rows = [[_jsonable(result[c][i]) if i < len(result[c]) else None for c in cols]
                    for i in range(min(total, limit))]
            return {"columns": [str(c) for c in cols], "rows": rows,
                    "row_count": total, "truncated": total > limit, "kind": "table"}
        # plain dictionary -> key/value grid
        rows = [[_jsonable(k), _jsonable(v)] for k, v in list(result.items())[:limit]]
        return {"columns": ["key", "value"], "rows": rows,
                "row_count": len(result), "truncated": len(result) > limit, "kind": "dict"}

    if isinstance(result, (list, tuple)):
        rows = [[_jsonable(v)] for v in result[:limit]]
        return {"columns": ["value"], "rows": rows,
                "row_count": len(result), "truncated": len(result) > limit, "kind": "vector"}

    return {"columns": ["value"], "rows": [[_jsonable(result)]],
            "row_count": 1, "truncated": False, "kind": "scalar"}


def run_query(query: str, conn, limit: int = DEFAULT_ROW_LIMIT,
              allow_write: bool = False) -> dict:
    """Validate + execute + shape. `conn` is callable(query)->q result (a real
    qpython QConnection, or a fake in tests). Returns a grid payload or raises
    ValueError for a blocked query."""
    if not allow_write:
        ok, reason = check_readonly(query)
        if not ok:
            raise ValueError(reason)
    result = conn(query)
    payload = shape_result(result, limit=limit)
    payload["query"] = query
    return payload
