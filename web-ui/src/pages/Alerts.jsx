import { useEffect, useMemo, useRef, useState } from "react";
import { api, metricsSocket } from "../api.js";

const REFRESH_MS = 1000;
const WATCH = ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "GOOG"];

export default function Alerts({ onNavigate }) {
  const [alerts, setAlerts] = useState([]);
  const [connected, setConnected] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const prevPrices = useRef(new Map());

  useEffect(() => {
    let closed = false;

    async function tick() {
      if (closed) return;
      const [ops, market, exec] = await Promise.all([
        buildOpsAlerts(),
        buildMarketAlerts(prevPrices.current),
        buildExecutionAlerts(),
      ]);
      const merged = [...ops, ...market, ...exec]
        .sort((a, b) => severityRank(b.severity) - severityRank(a.severity));
      setAlerts(merged.slice(0, 30));
      setLastUpdated(new Date());
    }

    tick();
    const id = setInterval(tick, REFRESH_MS);

    const ws = metricsSocket(() => setConnected(true));
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    return () => {
      closed = true;
      clearInterval(id);
      ws.close();
    };
  }, []);

  const grouped = useMemo(() => ({
    critical: alerts.filter((a) => a.severity === "critical"),
    warning: alerts.filter((a) => a.severity === "warning"),
    info: alerts.filter((a) => a.severity === "info"),
  }), [alerts]);

  return (
    <div className="page">
      <h2>Alerts center</h2>
      <p className="muted">
        {connected ? "● live" : "○ reconnecting..."} - low-latency mode, refreshed every second from market, execution, and platform pressure signals.
      </p>

      <div className="metric-cards">
        <AlertStat label="Critical" value={grouped.critical.length} cls="down" />
        <AlertStat label="Warning" value={grouped.warning.length} cls={grouped.warning.length ? "warn" : ""} />
        <AlertStat label="Info" value={grouped.info.length} />
        <AlertStat label="Last update" value={lastUpdated ? lastUpdated.toLocaleTimeString() : "—"} />
      </div>

      <div className="card">
        <div className="section-head">
          <h3>Live desk alerts</h3>
          <button className="link-btn" onClick={() => onNavigate?.("metrics")}>Open Metrics →</button>
        </div>
        {alerts.length === 0 ? (
          <p className="muted">No active alerts in the latest one-second window.</p>
        ) : (
          <div className="alerts-grid">
            {alerts.map((a, i) => (
              <article key={`${a.kind}-${a.key}-${i}`} className={`alert-card ${a.severity}`}>
                <header>
                  <span className={`signal-pill ${severityToPill(a.severity)}`}>{a.severity}</span>
                  <span className="muted">{a.kind}</span>
                </header>
                <h4>{a.title}</h4>
                <p>{a.detail}</p>
                <footer>
                  <span>{a.source}</span>
                  <span className="mono">{a.ts}</span>
                </footer>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function AlertStat({ label, value, cls }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${cls || ""}`}>{value}</div>
    </div>
  );
}

function severityRank(sev) {
  return sev === "critical" ? 3 : sev === "warning" ? 2 : 1;
}

function severityToPill(sev) {
  if (sev === "critical") return "down";
  if (sev === "warning") return "flat";
  return "up";
}

async function buildOpsAlerts() {
  const out = [];
  try {
    const snap = await api.metricsSnapshot();
    const comps = snap?.componentMetrics || [];
    for (const row of comps) {
      const queue = Number(row.tpQueue || 0);
      const lag = Number(row.tpSubLag || 0);
      if (queue > 0 || lag > 0) {
        out.push({
          key: `${row.shard}-pressure`,
          kind: "ops",
          severity: queue > 100000 || lag > 1000 ? "critical" : "warning",
          title: `Subscriber pressure on ${row.shard}`,
          detail: `Queue ${queue.toLocaleString()} bytes, downstream lag ${lag.toLocaleString()}.`,
          source: "gateway/componentMetrics",
          ts: qStamp(),
        });
      }
      if (row.rdbConnected === false || row.wdbConnected === false) {
        out.push({
          key: `${row.shard}-disconnect`,
          kind: "ops",
          severity: "critical",
          title: `Tier disconnect on ${row.shard}`,
          detail: `RDB connected=${String(row.rdbConnected)} WDB connected=${String(row.wdbConnected)}.`,
          source: "gateway/componentMetrics",
          ts: qStamp(),
        });
      }
    }

    const transit = snap?.transitLag || [];
    for (const row of transit) {
      const lag = Number(row.lagMs ?? row.avgMs ?? 0);
      if (lag >= 300) {
        out.push({
          key: `${row.shard}-${row.table}-${row.stage}-lag`,
          kind: "latency",
          severity: lag >= 1000 ? "critical" : "warning",
          title: `Transit lag ${lag.toFixed(1)} ms (${row.stage})`,
          detail: `${row.shard}/${row.table} has elevated pipeline delay.`,
          source: "gateway/transitLag",
          ts: qStamp(),
        });
      }
    }
  } catch (_) {
    // no-op: best effort
  }
  return out;
}

async function buildMarketAlerts(prevMap) {
  const out = [];
  try {
    const syms = WATCH.map((s) => `\`${s}`).join("");
    const res = await api.runQuery({
      target: "gateway",
      query: `select time,sym,price,size from trade where sym in ${syms}`,
      limit: 1200,
    });
    const cols = res.columns || [];
    const ti = cols.indexOf("time");
    const si = cols.indexOf("sym");
    const pi = cols.indexOf("price");
    const zi = cols.indexOf("size");
    const rows = res.rows || [];

    const bySym = new Map();
    for (const r of rows) {
      const sym = si >= 0 ? r[si] : null;
      if (!sym) continue;
      const bucket = bySym.get(sym) || [];
      bucket.push({ time: ti >= 0 ? r[ti] : null, price: Number(r[pi]), size: Number(r[zi]) });
      bySym.set(sym, bucket);
    }

    for (const [sym, bucket] of bySym.entries()) {
      const last = bucket[bucket.length - 1];
      if (!Number.isFinite(last?.price)) continue;
      const prev = prevMap.get(sym);
      prevMap.set(sym, last.price);
      if (prev && prev > 0) {
        const movePct = ((last.price - prev) / prev) * 100;
        if (Math.abs(movePct) >= 0.45) {
          out.push({
            key: `${sym}-move`,
            kind: "market",
            severity: Math.abs(movePct) >= 0.9 ? "critical" : "warning",
            title: `${sym} moved ${movePct.toFixed(2)}% in 1s`,
            detail: `Last ${last.price.toFixed(2)} vs previous ${prev.toFixed(2)}.`,
            source: "trade tape",
            ts: String(last.time || qStamp()),
          });
        }
      }

      const sizes = bucket.map((x) => x.size).filter((x) => Number.isFinite(x));
      if (sizes.length >= 5) {
        const avg = sizes.reduce((a, b) => a + b, 0) / sizes.length;
        const spike = sizes[sizes.length - 1];
        if (avg > 0 && spike >= avg * 3) {
          out.push({
            key: `${sym}-size`,
            kind: "market",
            severity: "info",
            title: `${sym} trade-size spike`,
            detail: `Last size ${Math.round(spike)} vs avg ${Math.round(avg)}.`,
            source: "trade tape",
            ts: String(last.time || qStamp()),
          });
        }
      }
    }
  } catch (_) {
    // no-op: best effort
  }
  return out;
}

async function buildExecutionAlerts() {
  const out = [];
  try {
    const rows = (await api.listOrders()).slice(0, 30);
    if (!rows.length) return out;

    const rejects = rows.filter((o) => o.status === "rejected").length;
    const cancels = rows.filter((o) => o.status === "cancelled").length;
    const failedPct = ((rejects + cancels) / rows.length) * 100;
    if (failedPct >= 20) {
      out.push({
        key: "exec-failure-rate",
        kind: "execution",
        severity: failedPct >= 35 ? "critical" : "warning",
        title: `Execution failure rate ${failedPct.toFixed(1)}%`,
        detail: `${rejects} rejected and ${cancels} cancelled in the latest ${rows.length} orders.`,
        source: "order blotter",
        ts: qStamp(),
      });
    }

    const noFills = rows.filter((o) => o.status === "new").length;
    if (noFills >= 10) {
      out.push({
        key: "exec-stuck-orders",
        kind: "execution",
        severity: "warning",
        title: `${noFills} open orders pending`,
        detail: "Orders are remaining in NEW status longer than expected for low-latency mode.",
        source: "order blotter",
        ts: qStamp(),
      });
    }
  } catch (_) {
    // no-op: best effort
  }
  return out;
}

function qStamp() {
  const d = new Date();
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return `${d.getUTCFullYear()}.${pad(d.getUTCMonth() + 1)}.${pad(d.getUTCDate())}D${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}.${pad(d.getUTCMilliseconds(), 3)}000`;
}
