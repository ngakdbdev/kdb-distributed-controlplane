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

import datetime as _dt
import re

from qpython.qcollection import QDictionary, QKeyedTable

DEFAULT_ROW_LIMIT = 1000
MAX_ROW_LIMIT = 10000

# kdb+ epoch is 2000.01.01, not the Unix epoch - and qpython (with pandas=False,
# what this app uses) hands timestamp/date columns back as plain int64 (raw
# nanoseconds/days since 2000.01.01), NOT numpy datetime64. Nothing upstream
# converts that, so without this, a huge nanosecond-since-2000 integer gets
# sent to the UI as a bare JSON number; the frontend's `new Date(...)`
# interprets it as milliseconds-since-1970, overflows past JS's valid Date
# range, and renders as "Invalid Date". Confirmed empirically against a live
# RDB: QTable.meta gives the q type code per column (e.g. time=-12), which is
# the only way to tell a timestamp column's int64 from an ordinary long
# column's int64 - numpy dtype alone can't distinguish them here.
_KDB_EPOCH = _dt.datetime(2000, 1, 1)
_QTYPE_TIMESTAMP = 12   # nanoseconds since 2000.01.01
_QTYPE_DATE = 14        # days since 2000.01.01


def _kdb_temporal_to_iso(raw, qtype: int):
    if raw is None:
        return None
    magnitude = abs(qtype)
    if magnitude == _QTYPE_TIMESTAMP:
        return (_KDB_EPOCH + _dt.timedelta(microseconds=int(raw) // 1000)).isoformat()
    if magnitude == _QTYPE_DATE:
        return (_KDB_EPOCH + _dt.timedelta(days=int(raw))).date().isoformat()
    return raw


def _columns_from_structured(arr) -> dict:
    """A qpython QTable/structured numpy array -> {colname: [values...]},
    converting any timestamp/date column via its q type (see module docstring)."""
    qtypes = {}
    meta = getattr(arr, "meta", None)
    if meta is not None and hasattr(meta, "as_dict"):
        qtypes = meta.as_dict()
    cols = {}
    for n in arr.dtype.names:
        qtype = qtypes.get(n)
        if qtype is not None and abs(qtype) in (_QTYPE_TIMESTAMP, _QTYPE_DATE):
            cols[n] = [_kdb_temporal_to_iso(v, qtype) for v in arr[n]]
        else:
            cols[n] = list(arr[n])
    return cols

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


def clamp_limit(limit, max_allowed: int = MAX_ROW_LIMIT) -> int:
    """`max_allowed` defaults to the interactive workspace's ceiling
    (MAX_ROW_LIMIT) - the background bulk-export path (export_jobs.py) is the
    one caller that passes a much higher ceiling, since the whole point of
    that path is pulling more rows than the interactive grid ever holds."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return min(DEFAULT_ROW_LIMIT, max_allowed)
    return max(1, min(n, max_allowed))


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


def shape_result(result, limit: int = DEFAULT_ROW_LIMIT, max_allowed: int = MAX_ROW_LIMIT) -> dict:
    """Turn a q result (table / dict / vector / scalar) into a grid payload.

    Accepts, for testability and for real qpython results:
      - dict {colname: [values...]}  -> a table
      - list/tuple                   -> a single 'value' column
      - scalar                       -> a 1x1 grid
      - a qpython QTable (numpy)     -> converted via its dtype names
    """
    limit = clamp_limit(limit, max_allowed)

    # qpython QTable / numpy structured array -> dict of columns
    if hasattr(result, "dtype") and getattr(result.dtype, "names", None):
        result = _columns_from_structured(result)
    elif hasattr(result, "dtype") and hasattr(result, "tolist") and getattr(result, "ndim", 0) >= 1:
        # A plain (non-structured) numpy array - e.g. `cols\`trade` returns a
        # bare symbol vector, not a Python list/tuple. Without this it fell
        # through every branch below to the scalar fallback and got
        # stringified whole (confirmed against a live cluster: `cols\`trade`
        # rendered as the literal text "[b'time' b'sym' ...]" instead of a
        # proper vector grid, one row per column name). .tolist() hands back
        # plain Python objects (bytes, for q symbols) that the list/tuple
        # branch below and _jsonable already know how to decode. A 0-d array
        # (ndim 0, a true scalar) is deliberately excluded - tolist() on
        # THAT returns the bare scalar, not a one-item list, and the
        # existing scalar path below (via .item()) already handles it fine.
        result = result.tolist()

    # qpython's own keyed-table class - what `select ... by ... from t`
    # actually comes back as. NOT a QDictionary subclass (confirmed against
    # qpython's source: `class QKeyedTable(object)`, entirely separate from
    # `class QDictionary(object)`) despite being structurally identical
    # (.keys/.values, each a QTable) - so without this check every grouped
    # query fell straight through every branch below to the scalar
    # fallback, stringifying the whole object instead of shaping it into a
    # proper grid. Confirmed live: `select count i by sym from trade`
    # rendered as one row of literal repr text ("[(b'AAPL',) ...]![(3810,)
    # ...]") instead of a sym|count table.
    if isinstance(result, QKeyedTable):
        kcols = {f"k_{n}": v for n, v in _columns_from_structured(result.keys).items()}
        vcols = {str(n): v for n, v in _columns_from_structured(result.values).items()}
        return shape_result({**kcols, **vcols}, limit=limit, max_allowed=max_allowed)

    # qpython dictionary - covers a keyed table built by hand (e.g. in a
    # test) as keys/values structured arrays, and a plain q dict otherwise
    if isinstance(result, QDictionary):
        keys = getattr(result, "keys", None)
        vals = getattr(result, "values", None)
        # keyed table: both keys and values are structured arrays
        if (hasattr(keys, "dtype") and getattr(keys.dtype, "names", None) and
                hasattr(vals, "dtype") and getattr(vals.dtype, "names", None)):
            kcols = {f"k_{n}": v for n, v in _columns_from_structured(keys).items()}
            vcols = {str(n): v for n, v in _columns_from_structured(vals).items()}
            return shape_result({**kcols, **vcols}, limit=limit, max_allowed=max_allowed)

        items = getattr(result, "iteritems", None)
        if callable(items):
            result = {k: v for k, v in items()}
        else:
            result = {k: v for k, v in zip(keys, vals)}

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


_SELECT_RE = re.compile(r"^\s*select\b", re.IGNORECASE)
_ALREADY_TAKEN_RE = re.compile(r"^\s*-?\d+\s*#")


def _cap_result_rows(query: str, cap: int) -> str:
    """Push the row cap DOWN into the q query itself for a plain `select`,
    instead of pulling the FULL result over IPC and only truncating it for
    display afterward. Confirmed live: `select from trade` against a 16M+
    row shard hung the whole request for 30s+ (past every client/server
    timeout) even though the UI's `limit` field claimed to cap it - the
    limit was only ever applied client-side, after the entire table had
    already been fetched.

    `N sublist (select ...)` takes the first N rows of a plain result, or the
    first N keys of a `by`-grouped one (a different but still bounded, still
    useful meaning of "limit" for a grouped query). Deliberately `sublist`,
    not `#` (take) - confirmed live: `#` on a table CYCLES/pads when the
    real result has fewer rows than N (plain q list semantics: `5#1 2` is
    `1 2 1 2 1`), so any query returning fewer rows than the limit - a bare
    aggregate like `select count i from trade` most visibly, but really any
    narrow/filtered select - came back with its one real row repeated out to
    `limit` rows instead of just the one row it actually had. `sublist`
    takes at most N and never pads short input.
    Skipped when the caller already prefixed their own take (respect that
    explicit choice, don't double-wrap) or the query isn't a plain textual
    `select` - exec/update/delete/insert/functional-form (`?[...]`) queries
    have their own semantics; wrapping those isn't safe.
    """
    q = query.strip()
    if _ALREADY_TAKEN_RE.match(q) or not _SELECT_RE.match(q):
        return query
    return f"{cap} sublist ({q})"


def run_query(query: str, conn, limit: int = DEFAULT_ROW_LIMIT,
              allow_write: bool = False, max_allowed: int = MAX_ROW_LIMIT) -> dict:
    """Validate + execute + shape. `conn` is callable(query)->q result (a real
    qpython QConnection, or a fake in tests). Returns a grid payload or raises
    ValueError for a blocked query. `max_allowed` - see clamp_limit."""
    if not allow_write:
        ok, reason = check_readonly(query)
        if not ok:
            raise ValueError(reason)
    limit = clamp_limit(limit, max_allowed)
    # cap+1, not cap: shape_result's own truncated/row_count math (below)
    # already works off however many rows actually came back, so fetching
    # one extra is what lets it correctly report "truncated" instead of
    # silently looking like an exact, complete result of exactly `limit` rows
    result = conn(_cap_result_rows(query, limit + 1))
    payload = shape_result(result, limit=limit, max_allowed=max_allowed)
    payload["query"] = query
    return payload


def combine_results(labeled: list, add_provenance: bool = True,
                    limit: int = DEFAULT_ROW_LIMIT) -> dict:
    """Federate results from several targets into one grid (scatter-gather).

    `labeled` is a list of (target_id, grid) for the targets that succeeded.
    Rows are unioned; when schemas differ, columns are outer-joined and missing
    cells filled with None. A `_target` provenance column is prepended so you
    can see which cluster each row came from.

    NOTE on aggregation: this unions rows. A single gateway already
    scatter-gathers *within* its cluster, so combining gateways gives you each
    cluster's rows side by side. For a grouped aggregate (select ... by ...),
    the per-target partials appear together and a semantic re-aggregation (re-sum
    / re-average by key) may still be wanted - that's noted to the caller rather
    than guessed at, since the right merge depends on each column's aggregation.
    """
    # union of columns, preserving first-seen order
    columns = []
    for _tid, grid in labeled:
        for c in grid.get("columns", []):
            if c not in columns:
                columns.append(c)

    out_cols = (["_target"] + columns) if add_provenance else columns
    rows = []
    total = 0
    truncated = False
    for tid, grid in labeled:
        gcols = grid.get("columns", [])
        idx = {c: i for i, c in enumerate(gcols)}
        total += grid.get("row_count", len(grid.get("rows", [])))
        truncated = truncated or grid.get("truncated", False)
        for r in grid.get("rows", []):
            aligned = [r[idx[c]] if c in idx and idx[c] < len(r) else None for c in columns]
            rows.append(([tid] + aligned) if add_provenance else aligned)
            if len(rows) >= limit:
                break

    return {"columns": out_cols, "rows": rows[:limit], "row_count": total,
            "truncated": truncated or total > limit, "kind": "federated",
            "target_count": len(labeled)}
