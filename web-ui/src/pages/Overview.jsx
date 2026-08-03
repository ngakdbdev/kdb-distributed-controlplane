import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { PRODUCT } from "../brand.js";

// A synthetic random-walk price series so the quant/forecast panels show
// something meaningful before a live cluster is connected.
function samplePrices(n = 60, start = 180) {
  const out = [start];
  for (let i = 1; i < n; i++) {
    out.push(Math.max(1, out[i - 1] * (1 + (Math.random() - 0.48) * 0.02)));
  }
  return out.map((p) => Math.round(p * 100) / 100);
}

export default function Overview({ onNavigate }) {
  const [clusters, setClusters] = useState(null);
  const [topo, setTopo] = useState(null);
  const [forecast, setForecast] = useState(null);
  const prices = useMemo(() => samplePrices(), []);

  useEffect(() => {
    api.listTickhouses().then(setClusters).catch(() => setClusters([]));
    api.topologyStatus().then(setTopo).catch(() => setTopo({}));
    api.forecast({ prices, horizon: 12 }).then(setForecast).catch(() => {});
  }, [prices]);

  const svc = topo ? Object.entries(topo) : [];
  const running = svc.filter(([, s]) => s === "running").length;
  const shardCount = new Set(
    svc.map(([n]) => (n.match(/-s(\d+)$/) || [])[1]).filter((x) => x != null)
  ).size;

  return (
    <div className="page">
      <h2>Welcome to {PRODUCT}</h2>
      <p className="muted">
        Your control plane at a glance. Jump to any area below — this page just orients you.
      </p>

      {/* clusters */}
      <div className="ov-section-head">
        <h3>Your TickHouses (tick clusters)</h3>
        <button className="link-btn" onClick={() => onNavigate?.("tickhouses")}>Manage →</button>
      </div>
      {clusters === null ? (
        <p className="muted">Loading…</p>
      ) : clusters.length === 0 ? (
        <div className="card empty-cta">
          <p>No tick clusters defined yet.</p>
          <button className="primary" onClick={() => onNavigate?.("tickhouses")}>Create a TickHouse</button>
        </div>
      ) : (
        <div className="ov-cards">
          {clusters.map((c) => (
            <button key={c.id} className="ov-card clickable" onClick={() => onNavigate?.("tickhouses")}>
              <div className="ov-card-title">{c.name}</div>
              <div className="ov-card-sub">
                <span className={`env-badge env-${c.location}`}>{c.location}</span>
                <span className="tier-badge tier-live">{c.profile}</span>
              </div>
              <div className={`ov-status ov-${c.status}`}>{c.status}</div>
            </button>
          ))}
        </div>
      )}

      {/* running topology */}
      <div className="ov-section-head">
        <h3>Running topology</h3>
        <button className="link-btn" onClick={() => onNavigate?.("topology")}>Open Topology →</button>
      </div>
      <div className="metric-cards">
        <Metric label="Shards" value={shardCount || "—"} />
        <Metric label="Processes" value={svc.length || "—"} />
        <Metric label="Running" value={svc.length ? `${running}/${svc.length}` : "—"}
                cls={svc.length && running === svc.length ? "up" : running ? "" : "down"} />
      </div>
      {svc.length === 0 && (
        <p className="muted" style={{ marginTop: "0.5rem" }}>
          No live processes reporting yet — provision a cluster (TickHouses → Provision) or start the
          local stack. The Topology tab controls individual processes and demonstrates self-healing.
        </p>
      )}

      {/* quant / market */}
      <div className="ov-section-head"><h3>Quant &amp; market</h3></div>
      <div className="ov-cards">
        <NavCard title="Query workspace" onGo={() => onNavigate?.("query")}
          desc="Run q against one or many live targets and see results as a grid (Superset-style)." />
        <NavCard title="Trading terminal" onGo={() => onNavigate?.("trading")}
          desc="Per-symbol market metrics, portfolio P&L, option greeks, forecasts, and paper orders." />
        <NavCard title="Live metrics" onGo={() => onNavigate?.("metrics")}
          desc="Streaming row counts, gateway health, and transit lag from the running cluster." />
      </div>

      {/* forecast preview (sample data) */}
      <div className="ov-section-head">
        <h3>Forecast preview <span className="sample-badge">SAMPLE</span></h3>
        <button className="link-btn" onClick={() => onNavigate?.("trading")}>Full quant terminal →</button>
      </div>
      <div className="card">
        <p className="muted" style={{ marginTop: 0 }}>
          An illustrative statistical projection on sample data — <strong>not a prediction, not advice</strong>.
          Connect a symbol in the Trading terminal for live figures.
        </p>
        {forecast?.points?.length > 0 && <ForecastSpark forecast={forecast} />}
      </div>
    </div>
  );
}

function Metric({ label, value, cls }) {
  return <div className="metric-card"><div className="metric-label">{label}</div>
    <div className={`metric-value ${cls || ""}`}>{value}</div></div>;
}

function NavCard({ title, desc, onGo }) {
  return (
    <button className="ov-card clickable" onClick={onGo}>
      <div className="ov-card-title">{title}</div>
      <div className="ov-card-desc">{desc}</div>
      <div className="ov-card-go">Open →</div>
    </button>
  );
}

function ForecastSpark({ forecast }) {
  const pts = forecast.points;
  const all = pts.flatMap((p) => [p.lower, p.upper]);
  const min = Math.min(...all), max = Math.max(...all), rng = max - min || 1;
  const y = (v) => 90 - ((v - min) / rng) * 80;
  const x = (i) => 10 + (i / Math.max(1, pts.length - 1)) * 480;
  return (
    <svg viewBox="0 0 500 100" className="forecast-svg" style={{ maxWidth: "520px" }}>
      <polyline fill="none" stroke="var(--muted)" strokeDasharray="3 3" strokeWidth="1"
                points={pts.map((p, i) => `${x(i)},${y(p.upper)}`).join(" ")} />
      <polyline fill="none" stroke="var(--muted)" strokeDasharray="3 3" strokeWidth="1"
                points={pts.map((p, i) => `${x(i)},${y(p.lower)}`).join(" ")} />
      <polyline fill="none" stroke="var(--accent)" strokeWidth="2"
                points={pts.map((p, i) => `${x(i)},${y(p.expected)}`).join(" ")} />
    </svg>
  );
}
