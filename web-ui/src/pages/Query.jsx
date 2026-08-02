import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";

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
  const [selected, setSelected] = useState(["gateway"]);
  const [tables, setTables] = useState([]);
  const [text, setText] = useState("select from trade");
  const [limit, setLimit] = useState(1000);
  const [allowWrite, setAllowWrite] = useState(false);
  const [syms, setSyms] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.queryTargets().then((d) => {
      setTargets(d.targets || []); setMeta(d); setLimit(d.row_limit_default || 1000);
    }).catch((e) => setError(String(e)));
  }, []);

  const primary = selected[0] || "gateway";
  useEffect(() => {
    if (!primary) return;
    api.queryTables(primary).then((d) => setTables(d.tables || [])).catch(() => setTables([]));
  }, [primary]);

  function toggleTarget(id) {
    setSelected((cur) => cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);
  }

  function insertSymFilter() {
    if (!syms.length) return;
    const list = syms.map((s) => `\`${s}`).join("");
    setText((t) => /where/i.test(t) ? `${t}, sym in ${list}` : `${t} where sym in ${list}`);
  }

  async function runIt() {
    if (!selected.length) { setError("select at least one target"); return; }
    setBusy(true); setError(""); setResult(null);
    try {
      setResult(await api.runQuery({ targets: selected, query: text, limit: Number(limit), allow_write: allowWrite }));
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
        Select one or more live targets, write q, and results render below. Pick several gateways or
        tickerplants and the query fans out and the rows are aggregated (with a <code>_target</code> column
        showing provenance). Queries are <strong>read-only by default</strong>.
        <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Enter</kbd> runs.
      </p>

      <div className="query-workspace">
        <aside className="query-side">
          <label className="query-label">Targets ({selected.length})</label>
          <div className="target-list">
            {targets.map((t) => (
              <label key={t.id} className="target-item">
                <input type="checkbox" checked={selected.includes(t.id)} onChange={() => toggleTarget(t.id)} />
                {t.label}
              </label>
            ))}
          </div>

          <label className="query-label">Symbol filter</label>
          <SymbolPicker value={syms} onChange={setSyms} placeholder="add symbols…" />
          <button className="chip" disabled={!syms.length} onClick={insertSymFilter}>+ insert sym filter</button>

          <label className="query-label">Tables ({primary})</label>
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
              {busy ? "Running…" : selected.length > 1 ? `Run on ${selected.length} targets` : "Run"}
            </button>
            <label className="query-limit">
              limit
              <input type="number" min="1" max={meta.row_limit_max} value={limit}
                     onChange={(e) => setLimit(e.target.value)} />
            </label>
            {meta.allow_write ? (
              <label className="query-write" title="Writes are enabled on this deployment — use with care">
                <input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} /> allow writes
              </label>
            ) : (
              <span className="readonly-badge">read-only</span>
            )}
          </div>

          {error && <div className="error query-error">{error}</div>}
          {result && <ResultView result={result} />}
        </div>
      </div>
    </div>
  );
}

function ResultView({ result }) {
  const { columns, rows, row_count, truncated, elapsed_ms, kind, per_target } = result;
  const shown = useMemo(() => rows || [], [rows]);
  return (
    <div className="query-result">
      {per_target && (
        <div className="per-target">
          {per_target.map((t) => (
            <span key={t.target} className={`per-target-pill ${t.ok ? "ok" : "fail"}`}
                  title={t.error || ""}>
              {t.target}: {t.ok ? `${t.rows} rows` : "failed"} · {t.elapsed_ms}ms
            </span>
          ))}
        </div>
      )}
      <div className="query-result-meta muted">
        {row_count} row{row_count === 1 ? "" : "s"} · {kind}{elapsed_ms != null ? ` · ${elapsed_ms} ms` : ""}
        {truncated && <span className="truncated"> · showing first {shown.length}</span>}
      </div>
      <div className="query-grid-scroll">
        <table className="data-table query-grid">
          <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
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
