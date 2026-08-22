import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

const CLOUDS = ["aws", "azure", "gcp", "onprem"];
const PROFILES = ["high-throughput", "low-latency", "balanced"];

export default function Migration() {
  const [files, setFiles] = useState([]);   // [{name, content}]
  const [pasted, setPasted] = useState("");
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInputRef = useRef(null);

  async function onFilesPicked(e) {
    const picked = [...(e.target.files || [])];
    const read = await Promise.all(picked.map((f) => f.text().then((content) => ({ name: f.name, content }))));
    setFiles((cur) => [...cur, ...read]);
    e.target.value = "";
  }

  function addPasted() {
    if (!pasted.trim()) return;
    setFiles((cur) => [...cur, { name: `pasted-${cur.length + 1}.q`, content: pasted }]);
    setPasted("");
  }

  function removeFile(i) {
    setFiles((cur) => cur.filter((_, j) => j !== i));
  }

  async function analyze() {
    if (!files.length) return;
    setBusy(true);
    setError("");
    try {
      setReport(await api.analyzeMigration(files));
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page migration-page">
      <h2>Migration assessment</h2>
      <p className="muted">
        Point this at your existing kdb+ scripts to get an effort estimate, then size and cost an equivalent
        cluster on this platform for comparison. Static analysis only &mdash; nothing here executes your code,
        and nothing is stored server-side; it lives only in this browser session.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="card">
        <h3>1. Your existing q scripts</h3>
        <div className="form-row">
          <button onClick={() => fileInputRef.current?.click()}>Upload .q files</button>
          <input ref={fileInputRef} type="file" multiple accept=".q,.txt" style={{ display: "none" }} onChange={onFilesPicked} />
        </div>
        <div className="form-row wrap">
          <textarea rows={4} style={{ flex: 1, minWidth: "20rem" }} placeholder="...or paste a script here"
                    value={pasted} onChange={(e) => setPasted(e.target.value)} />
          <button onClick={addPasted} disabled={!pasted.trim()}>Add pasted script</button>
        </div>
        {files.length > 0 && (
          <ul className="file-chip-list">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="file-chip">
                <span>{f.name}</span>
                <span className="muted">({f.content.split("\n").length} lines)</span>
                <button className="chip" onClick={() => removeFile(i)}>remove</button>
              </li>
            ))}
          </ul>
        )}
        <div className="form-row">
          <button className="primary" disabled={!files.length || busy} onClick={analyze}>
            {busy ? "Analyzing…" : "Analyze"}
          </button>
        </div>
      </div>

      {report && <AnalysisReport report={report} />}

      <TcoCalculator />
    </div>
  );
}

function sizeColor(size) {
  return { S: "ok", M: "ok", L: "warn", XL: "bad" }[size] || "ok";
}

function AnalysisReport({ report }) {
  const { totals, effort, scripts } = report;
  return (
    <div className="card highlight">
      <div className="section-head">
        <h3>2. Effort assessment</h3>
        <span className={`status-pill ${sizeColor(effort.size)}`}>{effort.size} &middot; {effort.weeks_low}-{effort.weeks_high} weeks</span>
      </div>
      <div className="kpi-row">
        <div className="kpi"><div className="kpi-label">Scripts</div><div className="kpi-value">{totals.scripts}</div></div>
        <div className="kpi"><div className="kpi-label">Lines of q</div><div className="kpi-value">{totals.loc.toLocaleString()}</div></div>
        <div className="kpi"><div className="kpi-label">Table schemas</div><div className="kpi-value">{totals.tables.length}</div></div>
        <div className="kpi"><div className="kpi-label">Bespoke .z.* handlers</div><div className="kpi-value">{totals.risky_handlers}</div></div>
      </div>

      {effort.factors.length > 0 && (
        <>
          <div className="wizard-section-label">What's driving the estimate</div>
          <ul className="factor-list">
            {effort.factors.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        </>
      )}

      <div className="wizard-section-label">Per-script detail</div>
      <table className="data-table">
        <thead><tr><th>script</th><th>loc</th><th>tables</th><th>functions</th><th>risky handlers</th><th>hardcoded hosts</th></tr></thead>
        <tbody>
          {scripts.map((s) => (
            <tr key={s.name}>
              <td>{s.name}</td><td>{s.loc}</td><td>{s.tables.length}</td><td>{s.functions.length}</td>
              <td>{s.risky_handlers.length > 0 ? s.risky_handlers.map((h) => h.handler).join(", ") : "—"}</td>
              <td>{s.hardcoded_hosts || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TcoCalculator() {
  const [rates, setRates] = useState(null);
  const [location, setLocation] = useState("aws");
  const [profile, setProfile] = useState("balanced");
  const [shardRanges, setShardRanges] = useState("a-m, n-z");
  const [currentCost, setCurrentCost] = useState("");
  const [rateMultiplier, setRateMultiplier] = useState(1);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.migrationTcoRates().then(setRates).catch(() => {}); }, []);

  async function run() {
    setBusy(true);
    setError("");
    try {
      const ratesOverride = rates && rateMultiplier !== 1
        ? Object.fromEntries(Object.entries(rates.cloud_hourly_usd).map(([k, v]) => [k, v * rateMultiplier]))
        : undefined;
      setResult(await api.migrationTco({
        location, profile, shard_ranges: shardRanges,
        current_annual_cost: currentCost ? Number(currentCost) : null,
        rates_override: ratesOverride,
      }));
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h3>3. Infrastructure cost estimate</h3>
      <p className="muted">
        Sizes real hardware for the workload (the same auto-tuning TickHouses uses) and prices it at
        {" "}{rates ? "illustrative public on-demand rates" : "…"}. Not a quote &mdash; adjust the rate
        multiplier for your actual region/commitment discount before relying on this number.
      </p>
      <div className="form-row wrap">
        <select value={location} onChange={(e) => setLocation(e.target.value)}>
          {CLOUDS.map((c) => <option key={c}>{c}</option>)}
        </select>
        <select value={profile} onChange={(e) => setProfile(e.target.value)}>
          {PROFILES.map((p) => <option key={p}>{p}</option>)}
        </select>
        <input placeholder="a-d, e-h, ..." value={shardRanges} onChange={(e) => setShardRanges(e.target.value)} style={{ minWidth: "12rem" }} />
        <input placeholder="your current annual license cost ($, optional)" type="number"
               value={currentCost} onChange={(e) => setCurrentCost(e.target.value)} style={{ minWidth: "18rem" }} />
      </div>
      <div className="form-row">
        <label>Rate multiplier (1.0 = on-demand list price)
          <input type="number" step="0.05" min="0.1" value={rateMultiplier}
                 onChange={(e) => setRateMultiplier(Number(e.target.value) || 1)} style={{ width: "5rem", marginLeft: "0.5rem" }} />
        </label>
        <button className="primary" disabled={busy} onClick={run}>{busy ? "Estimating…" : "Estimate"}</button>
      </div>
      {error && <div className="error">{error}</div>}

      {result && (
        <div className="command-log">
          <table className="spec-table">
            <thead><tr><th>component</th><th>instance</th><th>count</th><th>$/hr</th><th>$/month</th></tr></thead>
            <tbody>
              {result.lines.map((l) => (
                <tr key={l.component}>
                  <td>{l.component}</td><td>{l.instance_type || "(bare metal)"}</td><td>{l.count}</td>
                  <td>{l.hourly_usd.toFixed(3)}</td><td>{l.monthly_usd.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="kpi-row" style={{ marginTop: "0.75rem" }}>
            <div className="kpi"><div className="kpi-label">Monthly infra</div><div className="kpi-value">${result.monthly_infra_usd.toLocaleString()}</div></div>
            <div className="kpi"><div className="kpi-label">Annual infra</div><div className="kpi-value">${result.annual_infra_usd.toLocaleString()}</div></div>
            {"estimated_annual_savings_usd" in result && (
              <div className="kpi accent glow">
                <div className="kpi-label">Estimated annual savings</div>
                <div className="kpi-value">${result.estimated_annual_savings_usd.toLocaleString()}</div>
              </div>
            )}
          </div>
          <p className="muted" style={{ fontSize: "0.78rem", marginTop: "0.5rem" }}>{result.disclaimer}</p>
          {result.savings_disclaimer && <p className="muted" style={{ fontSize: "0.78rem" }}>{result.savings_disclaimer}</p>}
        </div>
      )}
    </div>
  );
}
