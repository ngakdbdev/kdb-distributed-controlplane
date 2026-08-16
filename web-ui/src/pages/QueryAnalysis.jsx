import { useEffect, useState } from "react";
import { api } from "../api.js";

// Observability for the query workspace: recent executions with profiling
// (connect/query time split, intelligent-routing decisions - see
// control-api/app/query_profile.py and query_router.py), plus a standalone
// explain/optimize/suggest box for any query pasted in directly - the same
// backend (control-api/app/query_advisor.py) the Query page's "Analyze"
// button uses, just without needing a query already loaded in that editor.
export default function QueryAnalysis() {
  const [history, setHistory] = useState({ entries: [], stats: {} });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [q, setQ] = useState("select from trade where sym=`AAPL");
  const [analysis, setAnalysis] = useState(null);
  const [analyzeBusy, setAnalyzeBusy] = useState(false);

  function refresh() {
    setLoading(true);
    api.queryHistory(50)
      .then((d) => { setHistory(d); setError(""); })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(refresh, []);

  async function analyzeIt() {
    if (!q.trim()) return;
    setAnalyzeBusy(true); setAnalysis(null);
    try {
      setAnalysis(await api.analyzeQuery(q, "gateway"));
    } catch (err) {
      setAnalysis({ issues: [], suggestions: [], llm_error: String(err).replace(/^Error:\s*/, "") });
    } finally { setAnalyzeBusy(false); }
  }

  const { entries = [], stats = {} } = history;

  return (
    <div className="page query-analysis-page">
      <h2>Query analysis</h2>
      <p className="muted">
        Recent query executions across every target, with profiling (connection vs. query time) and
        intelligent-routing decisions — plus a standalone explain/optimize box for any q expression, whether
        or not it's ever been run.
      </p>

      <div className="query-workspace">
        <aside className="query-side">
          <label className="query-label">Explain a query</label>
          <textarea className="query-editor" style={{ minHeight: 90 }} value={q} spellCheck={false}
                    onChange={(e) => setQ(e.target.value)} />
          <button className="chip generate" disabled={analyzeBusy || !q.trim()} onClick={analyzeIt}>
            {analyzeBusy ? "Analyzing…" : "Analyze →"}
          </button>
          {analysis && <AnalysisResult analysis={analysis} onApply={setQ} />}
        </aside>

        <div className="query-main">
          <div className="query-result-meta muted" style={{ marginBottom: 8 }}>
            {stats.count == null ? "no queries recorded yet this session" : (
              <>
                {stats.count} recent · {stats.ok_count} ok · {stats.error_count} failed ·
                {" "}{stats.routed_count} intelligently routed
                {stats.avg_query_ms != null && <> · avg {stats.avg_query_ms}ms, max {stats.max_query_ms}ms query time</>}
              </>
            )}
            <button className="chip" style={{ marginLeft: 8 }} onClick={refresh} disabled={loading}>
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          </div>

          {error && <div className="error query-error">{error}</div>}

          <div className="query-grid-scroll">
            <table className="data-table query-grid">
              <thead>
                <tr>
                  <th>time</th><th>actor</th><th>target</th><th>query</th><th>rows</th>
                  <th>connect</th><th>query</th><th>routing</th><th>status</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.id}>
                    <td>{new Date(e.timestamp).toLocaleTimeString()}</td>
                    <td>{e.actor}</td>
                    <td>{e.target}</td>
                    <td><code title={e.query}>{e.query.length > 60 ? `${e.query.slice(0, 60)}…` : e.query}</code></td>
                    <td>{e.row_count ?? "—"}</td>
                    <td>{e.connect_ms != null ? `${e.connect_ms}ms` : "—"}</td>
                    <td>{e.query_ms != null ? `${e.query_ms}ms` : "—"}</td>
                    <td>{e.routed_shards ? e.routed_shards.join(", ") : "—"}</td>
                    <td>
                      {e.ok
                        ? <span className="per-target-pill ok">ok</span>
                        : <span className="per-target-pill fail" title={e.error}>failed</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {entries.length === 0 && !loading && (
            <div className="muted">Nothing yet — run a query from the Query workspace and it'll show up here.</div>
          )}
        </div>
      </div>
    </div>
  );
}

function AnalysisResult({ analysis, onApply }) {
  const { explanation, issues = [], optimized_q, suggestions = [], provider, llm_error } = analysis;
  return (
    <div className="analysis-panel" style={{ marginTop: 10 }}>
      {provider && <div className="muted">via {provider}</div>}
      {explanation && <p>{explanation}</p>}
      {issues.length > 0 && (
        <ul className="analysis-issues">{issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul>
      )}
      {optimized_q && (
        <div className="analysis-optimized">
          <code>{optimized_q}</code>
          <button className="chip" onClick={() => onApply(optimized_q)}>Use this →</button>
        </div>
      )}
      {suggestions.length > 0 && (
        <div className="chip-list">
          {suggestions.map((s, i) => (
            <button key={i} className="chip" title={s.q} onClick={() => onApply(s.q)}>{s.label || s.q}</button>
          ))}
        </div>
      )}
      {!explanation && issues.length === 0 && !optimized_q && suggestions.length === 0 && (
        <div className="muted">
          {llm_error ? `No model-backed analysis available (${llm_error}).` : "Nothing to flag."}
        </div>
      )}
    </div>
  );
}
