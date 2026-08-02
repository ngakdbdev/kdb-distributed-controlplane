import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";

const PRESETS = [
  { label: "Recent trades", q: "select from trade" },
  { label: "By symbol", q: "select from trade where sym=`AAPL" },
  { label: "VWAP by sym", q: "select vwap:size wavg price, cnt:count i by sym from trade" },
  { label: "Cluster health", q: ".gw.health[]" },
  { label: "List tables", q: "tables[]" },
];

export default function Query() {
  const [targets, setTargets] = useState([]);
  const [meta, setMeta] = useState({ allow_write: false, row_limit_default: 1000, row_limit_max: 10000 });
  const [target, setTarget] = useState("gateway");
  const [tables, setTables] = useState([]);
  const [text, setText] = useState("select from trade");
  const [limit, setLimit] = useState(1000);
  const [allowWrite, setAllowWrite] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.queryTargets().then((d) => {
      setTargets(d.targets || []);
      setMeta(d);
      setLimit(d.row_limit_default || 1000);
    }).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!target) return;
    api.queryTables(target).then((d) => setTables(d.tables || [])).catch(() => setTables([]));
  }, [target]);

  async function runIt() {
    setBusy(true); setError(""); setResult(null);
    try {
      setResult(await api.runQuery({ target, query: text, limit: Number(limit), allow_write: allowWrite }));
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  }

  function onKey(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); runIt(); }
  }

  return (
    <div className="page">
      <h2>Query workspace</h2>
      <p className="muted">
        Connect to a live kdb+ target and run q &mdash; results render as a grid below.
        Queries are <strong>read-only by default</strong>; a restricted, network-scoped kdb service is the
        real safety boundary, so point this at one. <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Enter</kbd> runs.
      </p>

      <div className="query-workspace">
        <aside className="query-side">
          <label className="query-label">Target</label>
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            {targets.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>

          <label className="query-label">Tables</label>
          <div className="chip-list">
            {tables.length === 0 && <span className="muted">(none / unreachable)</span>}
            {tables.map((t) => (
              <button key={t} className="chip" onClick={() => setText(`select from ${t}`)}>{t}</button>
            ))}
          </div>

          <label className="query-label">Presets</label>
          <div className="chip-list">
            {PRESETS.map((p) => (
              <button key={p.label} className="chip" title={p.q} onClick={() => setText(p.q)}>{p.label}</button>
            ))}
          </div>
        </aside>

        <div className="query-main">
          <textarea className="query-editor" value={text} spellCheck={false}
                    onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
                    placeholder="select from trade where sym=`AAPL" />
          <div className="query-actions">
            <button className="primary" disabled={busy || !text.trim()} onClick={runIt}>
              {busy ? "Running…" : "Run"}
            </button>
            <label className="query-limit">
              limit
              <input type="number" min="1" max={meta.row_limit_max} value={limit}
                     onChange={(e) => setLimit(e.target.value)} />
            </label>
            {meta.allow_write ? (
              <label className="query-write" title="Writes are enabled on this deployment — use with care">
                <input type="checkbox" checked={allowWrite}
                       onChange={(e) => setAllowWrite(e.target.checked)} /> allow writes
              </label>
            ) : (
              <span className="readonly-badge">read-only</span>
            )}
          </div>

          {error && <div className="error query-error">{error}</div>}
          {result && <ResultGrid result={result} />}
        </div>
      </div>
    </div>
  );
}

function ResultGrid({ result }) {
  const { columns, rows, row_count, truncated, elapsed_ms, kind } = result;
  const shown = useMemo(() => rows || [], [rows]);
  return (
    <div className="query-result">
      <div className="query-result-meta muted">
        {row_count} row{row_count === 1 ? "" : "s"} · {kind} · {elapsed_ms} ms
        {truncated && <span className="truncated"> · showing first {shown.length}</span>}
      </div>
      <div className="query-grid-scroll">
        <table className="data-table query-grid">
          <thead>
            <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i}>{r.map((v, j) => <td key={j}>{v === null ? <span className="null">∅</span> : String(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown.length === 0 && <div className="muted">No rows.</div>}
    </div>
  );
}
