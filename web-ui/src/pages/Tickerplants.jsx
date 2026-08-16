import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

const POLL_MS = 2000;

function fmt(n) {
  if (n == null || n === "—") return "—";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(Math.round(n));
}
function bytes(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB"]; let i = 0; let v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}
function clockOf(iso) {
  if (!iso) return "—";
  const m = String(iso).match(/(\d{2}:\d{2}:\d{2}(?:\.\d+)?)/);
  return m ? m[1].slice(0, 15) : String(iso);
}

const CHECK_LABELS = {
  process: "Process", feed: "Feed", sequence: "Sequence", tplog: "TP log",
  subscribers: "Subscribers", publishLatency: "Publish latency",
  queueDepth: "Queue depth", downstreamLag: "Downstream lag",
};

export default function Tickerplants({ onNavigate }) {
  const [tps, setTps] = useState(null);
  const [rates, setRates] = useState({});   // id -> {recvps, pubps}
  const prev = useRef({});                    // id -> {recv, pub, ts}

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const d = await api.tickerplants();
        if (!alive) return;
        setTps(d.tickerplants || []);
        const now = Date.now();
        const nextRates = {};
        for (const tp of d.tickerplants || []) {
          if (!tp.ok || !tp.stats) continue;
          const p = prev.current[tp.id];
          if (p) {
            const dt = Math.max(0.001, (now - p.ts) / 1000);
            nextRates[tp.id] = {
              recvps: Math.max(0, ((tp.stats.recv ?? 0) - p.recv) / dt),
              pubps: Math.max(0, ((tp.stats.pub ?? 0) - p.pub) / dt),
            };
          }
          prev.current[tp.id] = { recv: tp.stats.recv ?? 0, pub: tp.stats.pub ?? 0, ts: now };
        }
        setRates((r) => ({ ...r, ...nextRates }));
      } catch { if (alive) setTps([]); }
    }
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const liveTps = tps || [];
  const healthyCount = liveTps.filter((tp) => tp.ok).length;
  const totalRecv = liveTps.reduce((sum, tp) => sum + (tp.ok ? (tp.stats?.recv ?? 0) : 0), 0);
  const totalQueue = liveTps.reduce((sum, tp) => sum + (tp.ok ? (tp.stats?.queueDepth ?? 0) : 0), 0);
  const degradedCount = liveTps.length - healthyCount;

  return (
    <div className="page tickerplants-page">
      <div className="tp-hero">
        <div>
          <div className="tp-kicker">Operations</div>
          <h2>Tickerplant monitor</h2>
        </div>
        <div className="tp-legend">
          <span className="tp-legend-dot live" /> Live data path
        </div>
      </div>
      <p className="muted">Live per-tickerplant throughput, queues, sequence, and health — polled every {POLL_MS / 1000}s.</p>

      {tps === null && <p className="muted">Loading…</p>}
      {tps && tps.length === 0 && (
        <div className="ops-banner warn">
          <div><strong>No tickerplants reachable.</strong> Start the stack (or provision a cluster) — this view reads each TP's live counters over IPC.</div>
          <button className="primary" onClick={() => onNavigate?.("topology")}>Open Topology →</button>
        </div>
      )}

      {tps && tps.length > 0 && (
        <div className="tp-summary-grid">
          <div className="tp-summary-card">
            <span className="tp-summary-label">Active TPs</span>
            <strong>{healthyCount}/{liveTps.length}</strong>
            <small>{degradedCount ? `${degradedCount} degraded` : "All healthy"}</small>
          </div>
          <div className="tp-summary-card">
            <span className="tp-summary-label">Total ingest</span>
            <strong>{fmt(totalRecv)} msg/s</strong>
            <small>Across all live TPs</small>
          </div>
          <div className="tp-summary-card">
            <span className="tp-summary-label">Queue depth</span>
            <strong>{bytes(totalQueue)}</strong>
            <small>Current backlog in memory</small>
          </div>
          <div className="tp-summary-card">
            <span className="tp-summary-label">Health state</span>
            <strong className={degradedCount ? "warn" : "ok"}>{degradedCount ? "Watch" : "Healthy"}</strong>
            <small>{degradedCount ? "attention needed" : "steady state"}</small>
          </div>
        </div>
      )}

      <div className="tp-grid">
        {(tps || []).map((tp) => <TpCard key={tp.id} tp={tp} rate={rates[tp.id]} onNavigate={onNavigate} />)}
      </div>
    </div>
  );
}

function TpCard({ tp, rate, onNavigate }) {
  if (!tp.ok) {
    return (
      <div className="tp-card down">
        <div className="tp-head"><h3>{tp.label}</h3><span className="tp-pill bad">unreachable</span></div>
        <p className="muted">{tp.error}</p>
        <button className="chip" onClick={() => onNavigate?.("topology")}>Control in Topology →</button>
      </div>
    );
  }
  const s = tp.stats || {};
  const h = tp.health || {};
  const checks = h.checks || {};
  const overall = h.overall;

  const metrics = [
    ["ingress", `${fmt(rate?.recvps ?? 0)} msg/s`],
    ["publish", `${fmt(rate?.pubps ?? 0)} msg/s`],
    ["queue", bytes(s.queueDepth)],
    ["subscribers", fmt(s.subs)],
    ["dropped", fmt(s.dropped)],
    ["upd errors", fmt(s.errs)],
    ["last seq", (s.lastSeq ?? 0).toLocaleString()],
    ["last msg", clockOf(s.lastTs)],
    ["publish latency", `${fmt(s.pubLatencyUs)} µs`],
    ["log write", `${fmt(s.logLatencyUs)} µs`],
    ["log size", bytes(s.logBytes)],
  ];

  const primaryMetrics = [
    { label: "Ingress", value: `${fmt(rate?.recvps ?? 0)} msg/s` },
    { label: "Publish", value: `${fmt(rate?.pubps ?? 0)} msg/s` },
    { label: "Queue", value: bytes(s.queueDepth) },
    { label: "Subscribers", value: fmt(s.subs) },
  ];

  return (
    <div className={`tp-card ${overall ? "healthy" : "degraded"}`}>
      <div className="tp-head">
        <div className="tp-entity">
          <span className="tp-entity-mark">TP</span>
          <div>
            <h3>{tp.label}</h3>
            <small>{tp.id}</small>
          </div>
        </div>
        <span className={`tp-pill ${overall ? "ok" : "warn"}`}>{overall ? "HEALTHY" : "DEGRADED"}</span>
      </div>

      <div className="tp-inline-metrics">
        {primaryMetrics.map((metric) => (
          <div className="tp-stat" key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
          </div>
        ))}
      </div>

      <div className="tp-secondary-grid">
        {metrics.map(([k, v]) => (
          <div className="tp-metric" key={k}><span className="tp-k">{k}</span><span className="tp-v">{v}</span></div>
        ))}
      </div>

      <div className="tp-health">
        <div className="tp-health-title">Health</div>
        {Object.entries(CHECK_LABELS).map(([key, label]) => (
          <div className="tp-check" key={key}>
            <span>{label}</span>
            <span className={checks[key] ? "chk ok" : "chk bad"}>{checks[key] ? "✓" : "✗"}</span>
          </div>
        ))}
        <div className={`tp-overall ${overall ? "ok" : "warn"}`}>Overall: {overall ? "HEALTHY" : "DEGRADED"}</div>
      </div>
    </div>
  );
}
