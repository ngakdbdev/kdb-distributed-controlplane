import { useEffect, useRef, useState } from "react";
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid,
} from "recharts";
import { api, metricsSocket } from "../api.js";
import { PRODUCT } from "../brand.js";
import Sparkline from "../components/Sparkline.jsx";

const TOOLTIP_STYLE = {
  background: "#161b26", border: "1px solid #232938", borderRadius: 10,
  color: "#eef1f7", fontSize: "0.82rem", boxShadow: "0 12px 28px rgba(0,0,0,.4)",
};
const TOOLTIP_LABEL_STYLE = { color: "#838ba1" };

const MAX = 60;

// compact large-number formatter: 1234 -> 1.2K, 3.4e9 -> 3.4B
function fmt(n) {
  if (n == null) return "—";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(Math.round(n));
}

export default function Overview({ onNavigate }) {
  const REFRESH_MS = 1000;
  const [topo, setTopo] = useState({});
  const [connected, setConnected] = useState(false);
  const [totals, setTotals] = useState({ trade: 0, risk: 0 });
  const [rate, setRate] = useState([]);       // msgs/sec history
  const [clusters, setClusters] = useState([]);
  const [feeds, setFeeds] = useState([]);
  const [pressure, setPressure] = useState(null);
  const [health, setHealth] = useState(null);
  const prev = useRef(null);

  useEffect(() => {
    const pull = () => api.topologyStatus().then(setTopo).catch(() => {});
    pull();
    api.listTickhouses().then(setClusters).catch(() => setClusters([]));
    api.listConnectors().then(setFeeds).catch(() => setFeeds([]));
    // Own, slower cadence (not REFRESH_MS/1s) - this rolls up infra/
    // tickhouse/security/trading state, none of which meaningfully changes
    // second to second the way live throughput does.
    const pullHealth = () => api.platformHealth().then(setHealth).catch(() => setHealth(null));
    pullHealth();
    const healthId = setInterval(pullHealth, 15000);
    const id = setInterval(pull, REFRESH_MS);
    // Pressure comes from the SAME websocket snapshot below (s.componentMetrics),
    // not a separate REST poll - a second independent /metrics/snapshot call
    // every second was computing the identical gateway snapshot twice over,
    // doubling real gateway IPC load for no benefit.
    const ws = metricsSocket((s) => {
      setConnected(true);
      setPressure(summarizePressure(s));
      const trade = s.rowCounts?.trade ?? 0, risk = s.rowCounts?.risk ?? 0;
      setTotals({ trade, risk });
      const now = Date.now();
      if (prev.current) {
        const dt = Math.max(0.001, (now - prev.current.ts) / 1000);
        const tps = Math.max(0, (trade - prev.current.trade) / dt);
        const rps = Math.max(0, (risk - prev.current.risk) / dt);
        setRate((r) => {
          const next = [...r, { t: new Date().toLocaleTimeString().slice(0, 8), tps, rps, mps: tps + rps }];
          return next.length > MAX ? next.slice(-MAX) : next;
        });
      }
      prev.current = { trade, risk, ts: now };
    });
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    return () => { clearInterval(id); clearInterval(healthId); ws.close(); };
  }, []);

  const svc = Object.entries(topo);
  const running = svc.filter(([, s]) => s === "running").length;
  const shardCount = new Set(svc.map(([n]) => (n.match(/-s(\d+)$/) || [])[1]).filter((x) => x != null)).size;
  const tp = svc.filter(([n]) => n.startsWith("tp-"));
  const tpUp = tp.some(([, s]) => s === "running");
  const feedsUp = feeds.some((f) => (f.status || f.state || "").toLowerCase() === "running" || f.active || f.enabled);
  const totalMsgs = totals.trade + totals.risk;
  const mps = rate.length ? rate[rate.length - 1].mps : 0;

  // health of the whole stream path, used to drive the honest banner
  const stream = !svc.length ? "unknown" : !tpUp ? "no-tp" : (totalMsgs === 0 || (rate.length > 3 && mps === 0)) ? "no-feed" : "live";

  return (
    <div className="page ops ops-premium">
      <div className="ops-head">
        <div>
          <h2>{PRODUCT} operations</h2>
          <p className="muted" style={{ margin: 0 }}>Live control plane — throughput, cluster health, and self-healing at a glance. <span className="live-cadence">Low-latency mode: refresh every second.</span></p>
        </div>
        <span className={`live-pill ${connected ? "on" : "off"}`}>{connected ? "● LIVE" : "○ offline"}</span>
      </div>

      {/* One-glance platform health - composes the same infra/tickhouse/
          security/trading checks Topology, Metrics and Audit log each show
          separately, so "is everything healthy" doesn't require visiting
          all three. See routers/platform_health.py. */}
      {health && (
        <div className="kpi-row" style={{ marginBottom: "0.75rem" }}>
          {Object.entries(health.components).map(([name, c]) => (
            <div className="kpi" key={name} style={{ cursor: "pointer" }}
                 onClick={() => onNavigate?.(
                   name === "infrastructure" ? "topology" : name === "tickhouse" ? "metrics"
                   : name === "security" ? "audit" : "orders")}>
              <div className="kpi-label">{name}</div>
              <div className="kpi-value" style={{ fontSize: "0.95rem" }}>
                <span className={`health-dot ${c.status}`} /> {c.status}
              </div>
              <div className="kpi-sub">{c.detail}</div>
            </div>
          ))}
        </div>
      )}

      {/* Honest, actionable state banner — never a dead screen */}
      {stream === "no-tp" && (
        <div className="ops-banner err">
          <div><strong>No tickerplant is running.</strong> Data can't flow until a tickerplant is up — every metric below stays at zero.
            {tp.length > 0 && <> Missing/instable: {tp.filter(([, s]) => s !== "running").map(([n]) => n).join(", ") || "tp-*"}.</>}</div>
          <button className="primary" onClick={() => onNavigate?.("topology")}>Fix in Topology →</button>
        </div>
      )}
      {stream === "no-feed" && (
        <div className="ops-banner warn">
          <div><strong>Tickerplants are up, but no feed is publishing.</strong> Enable a feed connector (or a market-data provider) to start the stream.</div>
          <button className="primary" onClick={() => onNavigate?.("connectors")}>Enable a feed →</button>
        </div>
      )}
      {stream === "unknown" && (
        <div className="ops-banner">
          <div><strong>No live processes reporting.</strong> Provision a cluster or start the local stack to bring this dashboard to life.</div>
          <button className="primary" onClick={() => onNavigate?.("tickhouses")}>Go to TickHouses →</button>
        </div>
      )}
      {pressure?.elevated && (
        <div className="ops-banner warn">
          <div><strong>Load shedding is active.</strong> {pressure.summary} Metrics and trading will prefer live routes over slow subscribers.</div>
          <button className="primary" onClick={() => onNavigate?.("metrics")}>Inspect pressure →</button>
        </div>
      )}

      {/* headline hero stat — the one number a viewer should see first */}
      <div className="hero-stat">
        <div className="hero-stat-main">
          <div className="hero-stat-label">Throughput, both shards</div>
          <div className="hero-stat-value">{fmt(mps)}<span className="kpi-unit">msg/s</span></div>
          <span className={`hero-stat-delta ${mps > 0 ? "up" : "flat"}`}>
            {mps > 0 ? "▲" : "•"} {stream === "live" ? "live ingest" : "waiting for data"}
          </span>
        </div>
        <div className="hero-stat-spark">
          <Sparkline data={rate.map((r) => r.mps)} width={160} height={48} strokeWidth={2.5} />
        </div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="kpi-label">Messages ingested</div>
          <div className="kpi-value">{fmt(totalMsgs)}</div>
          <div className="kpi-sub">{fmt(totals.trade)} trade · {fmt(totals.risk)} risk</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Shards</div>
          <div className="kpi-value">{shardCount || "—"}</div>
          <div className="kpi-sub">{svc.length} processes</div>
        </div>
        <div className={`kpi ${svc.length && running === svc.length ? "ok" : running ? "" : "bad"}`}>
          <div className="kpi-label">Healthy</div>
          <div className="kpi-value">{svc.length ? `${running}/${svc.length}` : "—"}</div>
          <div className="kpi-sub">self-healing {running === svc.length && svc.length ? "· all green" : "· watchdog active"}</div>
        </div>
      </div>

      {/* live ingest chart */}
      <div className="card">
        <div className="ov-section-head" style={{ margin: 0 }}>
          <h3 style={{ margin: 0 }}>Live ingest rate</h3>
          <button className="link-btn" onClick={() => onNavigate?.("metrics")}>Full metrics →</button>
        </div>
        {rate.length < 2 ? (
          <p className="muted">{connected ? "Collecting samples…" : "Waiting for the metrics stream…"}</p>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={rate} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="t" tick={{ fontSize: 11, fill: "var(--muted)" }} minTickGap={40} stroke="var(--border)" />
              <YAxis tick={{ fontSize: 11, fill: "var(--muted)" }} stroke="var(--border)" tickFormatter={fmt} />
              <Tooltip formatter={(v) => fmt(v) + " msg/s"} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} />
              <Area type="monotone" dataKey="mps" stroke="var(--accent)" strokeWidth={2} fill="url(#g)" isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* cluster health grid — the self-healing story */}
      <div className="ov-section-head"><h3>Cluster health</h3>
        <button className="link-btn" onClick={() => onNavigate?.("topology")}>Control processes →</button></div>
      {svc.length === 0 ? (
        <div className="card"><p className="muted" style={{ margin: 0 }}>no resource found — no processes are reporting yet.</p></div>
      ) : (
        <div className="health-grid">
          {svc.sort(([a], [b]) => a.localeCompare(b)).map(([name, status]) => (
            <button key={name} className={`health-tile s-${status}`} onClick={() => onNavigate?.("topology")} title={`${name}: ${status}`}>
              <span className="ht-dot" />
              <span className="ht-name">{name}</span>
              <span className="ht-status">{status}</span>
            </button>
          ))}
        </div>
      )}

      {/* clusters + quant nav, condensed */}
      <div className="ov-section-head"><h3>Your TickHouses</h3>
        <button className="link-btn" onClick={() => onNavigate?.("tickhouses")}>Manage →</button></div>
      {clusters.length === 0 ? (
        <div className="card empty-cta"><p>No tick clusters defined yet.</p>
          <button className="primary" onClick={() => onNavigate?.("tickhouses")}>Create a TickHouse</button></div>
      ) : (
        <div className="ov-cards">
          {clusters.map((c) => (
            <button key={c.id} className="ov-card clickable" onClick={() => onNavigate?.("tickhouses")}>
              <div className="ov-card-title">{c.name}</div>
              <div className="ov-card-sub"><span className={`env-badge env-${c.location}`}>{c.location}</span>
                <span className="tier-badge tier-live">{c.profile}</span></div>
              <div className={`ov-status ov-${c.status}`}>{c.status}</div>
            </button>
          ))}
        </div>
      )}

      <div className="ov-cards" style={{ marginTop: "1rem" }}>
        <NavCard title="Query workspace" onGo={() => onNavigate?.("query")} desc="Run q across live targets; rows render as a grid." />
        <NavCard title="Markets" onGo={() => onNavigate?.("markets")} desc="Market metrics, candlestick drilldown, calendar-horizon forecast." />
        <NavCard title="Live metrics" onGo={() => onNavigate?.("metrics")} desc="Streaming row counts and transit lag per shard." />
      </div>
    </div>
  );
}

// tp.q's REAL slow-subscriber discard threshold (SLOW_SUB_MAX_BYTES, see
// tick.q / docs/tickerplant-administration.md) defaults to 50MB, held for
// several consecutive checks before anything is actually dropped. A queue
// depth of a few KB is completely normal, healthy jitter on a live-
// streaming system - alerting on ANY nonzero value (the old behavior here)
// meant this banner was "active" essentially always, which trains people to
// ignore it right when it matters. ELEVATED_BYTES is an early-warning
// fraction of the real threshold: high enough that it doesn't fire on
// ordinary jitter, low enough to give real notice before anything is
// actually shed.
const ELEVATED_BYTES = 2 * 1024 * 1024; // 2MB - ~4% of the real 50MB drop threshold

function summarizePressure(snapshot) {
  const rows = (snapshot?.componentMetrics || []).filter((row) => {
    const queue = Number(row.tpQueue || 0);
    const lag = Number(row.tpSubLag || 0);
    return queue > ELEVATED_BYTES || lag > ELEVATED_BYTES || row.rdbConnected === false || row.wdbConnected === false;
  });
  if (!rows.length) return { elevated: false, rows: [], summary: "No active load shedding or subscriber pressure." };
  const labels = rows.map((row) => row.shard).join(", ");
  const maxQueue = Math.max(...rows.map((row) => Number(row.tpQueue || 0)));
  const maxLag = Math.max(...rows.map((row) => Number(row.tpSubLag || 0)));
  return {
    elevated: true,
    rows,
    summary: `${labels} under pressure: queue depth ${fmt(maxQueue, 0)}B, subscriber lag ${fmt(maxLag, 0)}B (real drop threshold is 50MB).`,
  };
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
