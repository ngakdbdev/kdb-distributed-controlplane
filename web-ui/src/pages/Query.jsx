import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";

// table -> columns, from schema.q; used for autocomplete + the q generator
const SCHEMA = {
  trade: ["time", "sym", "price", "size", "side", "venue", "shard"],
  risk: ["time", "sym", "riskType", "limit", "exposure", "status", "shard"],
};
const QWORDS = ["select", "from", "where", "by", "update", "delete", "exec", "count",
  "avg", "sum", "wavg", "max", "min", "last", "first", "dev", "med", "sums", "distinct",
  "asc", "desc", "xbar", "within", "in", "like"];

const PRESETS = [
  { label: "Recent trades", q: "select from trade" },
  { label: "By symbol", q: "select from trade where sym=`AAPL" },
  { label: "VWAP by sym", q: "select vwap:size wavg price, cnt:count i by sym from trade" },
  { label: "Cluster health", q: ".gw.health[]" },
  { label: "List tables", q: "tables[]" },
];

// ------- plain-English / pseudocode -> q -------------------------------------
function pickCol(s, cols, dflt) {
  for (const c of cols) if (new RegExp(`\\b${c.toLowerCase()}\\b`).test(s)) return c;
  return dflt;
}
function nl2q(input) {
  const s = (input || "").toLowerCase().trim();
  if (!s) return "";
  const table = /\brisk\b/.test(s) ? "risk" : "trade";
  const cols = SCHEMA[table];
  // symbol: an uppercase ticker in the original text, or "for xyz"/"symbol xyz"
  let sym = null;
  const up = (input.match(/\b[A-Z]{2,6}\b/g) || []).filter((w) => !["VWAP", "AAPL"].includes(w) || w === "AAPL");
  if (up.length) sym = up[0];
  const forM = s.match(/(?:for|symbol|sym)\s+([a-z]{1,6})\b/);
  if (!sym && forM) sym = forM[1].toUpperCase();
  const where = sym ? ` where sym=\`${sym}` : "";
  // grouping
  const byM = s.match(/(?:by|per|grouped by|group by)\s+(sym|symbol|venue|side|status|shard|risktype)/);
  const groupCol = byM ? (byM[1] === "symbol" ? "sym" : byM[1] === "risktype" ? "riskType" : byM[1]) : null;
  const byClause = groupCol ? ` by ${groupCol}` : "";
  // aggregation
  let agg = null;
  if (/\bvwap\b/.test(s)) agg = "vwap:size wavg price";
  else if (/\b(avg|average|mean)\b/.test(s)) { const c = pickCol(s, cols, "price"); agg = `avg${c[0].toUpperCase() + c.slice(1)}:avg ${c}`; }
  else if (/\b(sum|total)\b/.test(s)) { const c = pickCol(s, cols, "size"); agg = `sum${c[0].toUpperCase() + c.slice(1)}:sum ${c}`; }
  else if (/\b(max|highest|peak)\b/.test(s)) { const c = pickCol(s, cols, "price"); agg = `max${c[0].toUpperCase() + c.slice(1)}:max ${c}`; }
  else if (/\b(min|lowest)\b/.test(s)) { const c = pickCol(s, cols, "price"); agg = `min${c[0].toUpperCase() + c.slice(1)}:min ${c}`; }
  else if (/\b(count|number of|how many)\b/.test(s)) agg = "n:count i";
  else if (/\b(last|latest)\b/.test(s) && groupCol) agg = "lastPrice:last price, lastTime:last time";
  // limit N
  const nM = s.match(/\b(last|top|first|recent)\s+(\d+)/);
  const n = nM ? nM[2] : null;
  let q = agg ? `select ${agg}${byClause} from ${table}${where}` : `select from ${table}${where}`;
  if (n && !agg) q = /last|recent/.test(nM[1]) ? `-${n}#(${q})` : `${n}#${q}`;
  return q;
}

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
  const [nl, setNl] = useState("");
  const [ac, setAc] = useState({ open: false, items: [], from: 0, to: 0 });
  const editorRef = useRef(null);

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

  const available = useMemo(() => targets.filter((t) => !selected.includes(t.id)), [targets, selected]);
  function addTarget(id) { if (id) setSelected((c) => [...c, id]); }
  function removeTarget(id) { setSelected((c) => c.filter((x) => x !== id)); }
  const labelOf = (id) => targets.find((t) => t.id === id)?.label || id;

  function insertSymFilter() {
    if (!syms.length) return;
    const list = syms.map((s) => `\`${s}`).join("");
    setText((t) => /where/i.test(t) ? `${t}, sym in ${list}` : `${t} where sym in ${list}`);
  }

  // ---- autocomplete ----
  function candidatesFor(word, fullText) {
    const mentioned = Object.keys(SCHEMA).filter((t) => fullText.includes(t));
    const cols = mentioned.flatMap((t) => SCHEMA[t]);
    const tbls = [...new Set([...(tables || []), ...Object.keys(SCHEMA)])];
    const pool = [...new Set([...QWORDS, ...tbls, ...cols])];
    const w = word.toLowerCase();
    if (!w) return [];
    return pool.filter((x) => x.toLowerCase().startsWith(w) && x.toLowerCase() !== w).slice(0, 8);
  }
  function onEditorChange(e) {
    const v = e.target.value; setText(v);
    const pos = e.target.selectionStart;
    const before = v.slice(0, pos);
    const m = before.match(/[A-Za-z0-9_]+$/);
    if (m) {
      const items = candidatesFor(m[0], v);
      setAc({ open: items.length > 0, items, from: pos - m[0].length, to: pos });
    } else setAc((a) => ({ ...a, open: false }));
  }
  function applyCompletion(word) {
    setText((t) => t.slice(0, ac.from) + word + t.slice(ac.to));
    setAc((a) => ({ ...a, open: false }));
    requestAnimationFrame(() => {
      const el = editorRef.current; if (!el) return;
      const c = ac.from + word.length; el.focus(); el.setSelectionRange(c, c);
    });
  }
  function onKey(e) {
    if (ac.open && (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey))) {
      e.preventDefault(); applyCompletion(ac.items[0]); return;
    }
    if (e.key === "Escape") setAc((a) => ({ ...a, open: false }));
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); runIt(); }
  }

  function generate() {
    const q = nl2q(nl);
    if (q) { setText(q); setAc((a) => ({ ...a, open: false })); }
  }

  async function runIt() {
    if (!selected.length) { setError("select at least one target"); return; }
    setBusy(true); setError(""); setResult(null);
    try {
      setResult(await api.runQuery({ targets: selected, query: text, limit: Number(limit), allow_write: allowWrite }));
    } catch (err) {
      setError(formatQueryError(String(err).replace(/^Error:\s*/, ""), selected));
    } finally { setBusy(false); }
  }

  return (
    <div className="page">
      <h2>Query workspace</h2>
      <p className="muted">
        Choose one or more targets — a gateway, a <strong>tickerplant</strong>'s live buffer, or an RDB — write q (with
        autocomplete), or describe what you want in plain English and generate it. Multi-target queries fan out and
        aggregate with a <code>_target</code> column. Read-only by default. <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Enter</kbd> runs.
      </p>

      <div className="query-workspace">
        <aside className="query-side">
          <label className="query-label">Targets</label>
          <select className="target-select" value="" onChange={(e) => { addTarget(e.target.value); e.target.value = ""; }}>
            <option value="" disabled>＋ add a target…</option>
            {available.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
          <div className="target-chips">
            {selected.length === 0 && <span className="muted">none selected</span>}
            {selected.map((id) => (
              <span key={id} className="target-chip">{labelOf(id)}
                <button onClick={() => removeTarget(id)} title="remove">×</button></span>
            ))}
          </div>

          <label className="query-label">Symbol filter</label>
          <SymbolPicker value={syms} onChange={setSyms} placeholder="add symbols…" />
          <button className="chip" disabled={!syms.length} onClick={insertSymFilter}>+ insert sym filter</button>

          <label className="query-label">Tables ({primary})</label>
          <div className="chip-list">
            {tables.length === 0 && <span className="muted">(none / unreachable)</span>}
            {tables.map((t) => <button key={t} className="chip" onClick={() => setText(`select from ${t}`)}>{t}</button>)}
          </div>

          <label className="query-label">Presets</label>
          <div className="chip-list">
            {PRESETS.map((p) => <button key={p.label} className="chip" title={p.q} onClick={() => setText(p.q)}>{p.label}</button>)}
          </div>
        </aside>

        <div className="query-main">
          {/* plain-English -> q */}
          <div className="nl2q">
            <input className="nl2q-input" value={nl} placeholder="Describe it: e.g. 'vwap by symbol for AAPL' or 'last 100 trades'"
                   onChange={(e) => setNl(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); generate(); } }} />
            <button className="chip generate" disabled={!nl.trim()} onClick={generate}>Generate q →</button>
          </div>

          <div className="editor-wrap">
            <textarea ref={editorRef} className="query-editor" value={text} spellCheck={false}
                      onChange={onEditorChange} onKeyDown={onKey}
                      onBlur={() => setTimeout(() => setAc((a) => ({ ...a, open: false })), 120)}
                      placeholder="select from trade where sym=`AAPL" />
            {ac.open && (
              <div className="ac-pop">
                {ac.items.map((it, i) => (
                  <button key={it} className={`ac-item ${i === 0 ? "first" : ""}`} onMouseDown={(e) => { e.preventDefault(); applyCompletion(it); }}>
                    {it}{i === 0 && <span className="ac-hint">Tab</span>}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="query-actions">
            <button className="primary" disabled={busy || !text.trim()} onClick={runIt}>
              {busy ? "Running…" : selected.length > 1 ? `Run on ${selected.length} targets` : "Run"}
            </button>
            <label className="query-limit">limit
              <input type="number" min="1" max={meta.row_limit_max} value={limit} onChange={(e) => setLimit(e.target.value)} />
            </label>
            {meta.allow_write ? (
              <label className="query-write" title="Writes are enabled on this deployment — use with care">
                <input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} /> allow writes
              </label>
            ) : <span className="readonly-badge">read-only</span>}
          </div>

          {error && <div className="error query-error">{error}</div>}
          {result && <ResultView result={result} />}
        </div>
      </div>
    </div>
  );
}

function formatQueryError(message, targets) {
  if (/unreachable/i.test(message) || /502/.test(message)) {
    const targetText = targets.length > 1 ? `${targets.length} targets` : targets[0] || "gateway";
    return `Target path unavailable on ${targetText}. The gateway or shard RDB path did not answer in time. Check the Metrics page for transit pressure or switch to a live RDB target.`;
  }
  if (/read-only/i.test(message)) {
    return "Query blocked by the read-only guard. Use a select/exec expression or explicitly enable writes on the deployment.";
  }
  return message;
}

function ResultView({ result }) {
  const { columns, rows, row_count, truncated, elapsed_ms, kind, per_target, warning } = result;
  const shown = useMemo(() => rows || [], [rows]);
  return (
    <div className="query-result">
      {per_target && (
        <div className="per-target">
          {per_target.map((t) => (
            <span key={t.target} className={`per-target-pill ${t.ok ? "ok" : "fail"}`} title={t.error || ""}>
              {t.target}: {t.ok ? `${t.rows} rows` : "failed"} · {t.elapsed_ms}ms
            </span>
          ))}
        </div>
      )}
      <div className="query-result-meta muted">
        {row_count} row{row_count === 1 ? "" : "s"} · {kind}{elapsed_ms != null ? ` · ${elapsed_ms} ms` : ""}
        {truncated && <span className="truncated"> · showing first {shown.length}</span>}
      </div>
      {warning && <div className="query-warning">{warning}</div>}
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
