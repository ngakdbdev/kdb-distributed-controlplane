import { useEffect, useRef, useState } from "react";
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid,
} from "recharts";
import { api, metricsSocket } from "../api.js";

const TOOLTIP_STYLE = {
  background: "var(--panel-2)", border: "1px solid var(--border)", borderRadius: 10,
  color: "var(--text)", fontSize: "0.82rem", boxShadow: "0 12px 28px rgba(0,0,0,.4)",
};
const TOOLTIP_LABEL_STYLE = { color: "var(--muted)" };

const MAX = 60;
const TP_POLL_MS = 3000;

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
  const [tps, setTps] = useState(null);       // real per-tickerplant health (Data Fabric strip)
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
    // Same real per-tickerplant data Tickerplants.jsx shows in full detail
    // (dropped count, publish latency, sequence-check pass/fail) - this
    // page only needs the aggregate, so it polls at Tickerplants.jsx's own
    // slower 3s cadence rather than every second.
    const pullTps = () => api.tickerplants().then((d) => setTps(d.tickerplants || [])).catch(() => setTps([]));
    pullTps();
    const tpId = setInterval(pullTps, TP_POLL_MS);
    const ws = metricsSocket((s) => {
      setConnected(true);
      setPressure(summarizePressure(s));
      const trade = s.rowCounts?.trade ?? 0, risk = s.rowCounts?.risk ?? 0;
      setTotals({ trade, risk });
      const now = Date.now();
      if (prev.current) {
        const dt = Math.max(0.001, (now - prev.current.ts) / 1000);
        const tps_ = Math.max(0, (trade - prev.current.trade) / dt);
        const rps = Math.max(0, (risk - prev.current.risk) / dt);
        setRate((r) => {
          const next = [...r, { t: new Date().toLocaleTimeString().slice(0, 8), tps: tps_, rps, mps: tps_ + rps }];
          return next.length > MAX ? next.slice(-MAX) : next;
        });
      }
      prev.current = { trade, risk, ts: now };
    });
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    return () => { clearInterval(id); clearInterval(healthId); clearInterval(tpId); ws.close(); };
  }, []);

  const svc = Object.entries(topo);
  const running = svc.filter(([, s]) => s === "running").length;
  const tp = svc.filter(([n]) => n.startsWith("tp-"));
  const tpUp = tp.some(([, s]) => s === "running");
  const totalMsgs = totals.trade + totals.risk;
  const mps = rate.length ? rate[rate.length - 1].mps : 0;

  // health of the whole stream path, used to drive the honest banner
  const stream = !svc.length ? "unknown" : !tpUp ? "no-tp" : (totalMsgs === 0 || (rate.length > 3 && mps === 0)) ? "no-feed" : "live";

  // ---- Data Fabric strip: real per-tickerplant health, rolled up to one
  // calm line instead of individual TP pills fighting for attention here
  // (that detail lives at Data > Tickerplants). Every number below reads
  // straight off api.tickerplants()'s real IPC counters - nothing here is
  // synthesized. Sequence integrity specifically has no numeric "gap
  // count" anywhere in the backend (only a per-TP pass/fail check), so
  // this reports how many tickerplants have that check failing rather
  // than inventing a gap count that doesn't exist.
  const liveTps = (tps || []).filter((t) => t.ok);
  const feedCount = feeds.filter((f) => f.enabled).length;
  const tpCount = (tps || []).length;
  const droppedTotal = liveTps.reduce((sum, t) => sum + (t.stats?.dropped || 0), 0);
  const latencies = liveTps.map((t) => t.stats?.pubLatencyUs).filter((v) => v != null);
  const maxLatency = latencies.length ? Math.max(...latencies) : null;
  const seqFailing = liveTps.filter((t) => t.health?.checks?.sequence === false).length;
  const fabricHealthy = tps !== null && tpCount > 0 && liveTps.length === tpCount
    && liveTps.every((t) => t.health?.overall) && droppedTotal === 0 && seqFailing === 0;
  const fabricKnown = tps !== null;

  return (
    <div className="page ops">
      <div className="ops-head">
        <div>
          <h2>Overview</h2>
          <p className="muted" style={{ margin: 0 }}>Platform status and live data flow. <span className="live-cadence">Refreshing every second.</span></p>
        </div>
        <span className={`live-pill ${connected ? "on" : "off"}`}>{connected ? "● LIVE" : "○ offline"}</span>
      </div>

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

      {/* Data Fabric — "is market data flowing correctly," one calm line,
          not which tickerplant owns which shard letter range. */}
      <button className="fabric-strip" onClick={() => onNavigate?.("tickerplants")} title="Open the tickerplant fabric view">
        <span className={`fabric-dot ${!fabricKnown ? "unknown" : fabricHealthy ? "healthy" : "degraded"}`} />
        <span className="fabric-label">Data Fabric</span>
        <span className={`fabric-state ${!fabricKnown ? "unknown" : fabricHealthy ? "healthy" : "degraded"}`}>
          {!fabricKnown ? "checking…" : fabricHealthy ? "healthy" : tpCount === 0 ? "no tickerplants" : "attention needed"}
        </span>
        <span className="fabric-metrics">
          <span><strong>{feedCount}</strong> feeds</span>
          <span><strong>{tpCount}</strong> tickerplants</span>
          <span><strong>{fmt(mps)}</strong> msg/s</span>
          <span><strong>{fmt(droppedTotal)}</strong> dropped</span>
          <span><strong>{maxLatency != null ? fmt(maxLatency) : "—"}</strong> µs publish</span>
          <span><strong>{fabricKnown ? (seqFailing === 0 ? "OK" : `${seqFailing} flagged`) : "—"}</strong> sequence</span>
        </span>
        <span className="fabric-go">Tickerplant fabric →</span>
      </button>

      {/* One-glance platform health - composes the same infra/tickhouse/
          security/trading checks Topology, Metrics and Audit log each show
          separately, so "is everything healthy" doesn't require visiting
          all three. See routers/platform_health.py. */}
      {health && (
        <div className="kpi-row" style={{ marginTop: "0.75rem" }}>
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

      {/* live ingest chart */}
      <div className="card" style={{ marginTop: "0.75rem" }}>
        <div className="ov-section-head" style={{ margin: 0 }}>
          <h3 style={{ margin: 0 }}>Data throughput</h3>
          <button className="link-btn" onClick={() => onNavigate?.("metrics")}>Full metrics →</button>
        </div>
        {rate.length < 2 ? (
          <p className="muted">{connected ? "Collecting samples…" : "Waiting for the metrics stream…"}</p>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
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

      {/* Process-level detail (self-healing, individual container status)
          intentionally isn't duplicated here as a grid of pills - that's
          Topology's job. One line, not "N processes" cluttering the home
          screen. */}
      <div className="fabric-footnote">
        <span className={running === svc.length && svc.length ? "ok" : "warn"}>
          {svc.length ? `${running}/${svc.length} processes healthy` : "no processes reporting"}
        </span>
        <button className="link-btn" onClick={() => onNavigate?.("topology")}>Process control →</button>
      </div>

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
        <NavCard title="Markets" onGo={() => onNavigate?.("markets")} desc="Instrument watchlist, candlestick drilldown, calendar-horizon forecast." />
        <NavCard title="Query workspace" onGo={() => onNavigate?.("query")} desc="Run q across live targets; rows render as a grid." />
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
