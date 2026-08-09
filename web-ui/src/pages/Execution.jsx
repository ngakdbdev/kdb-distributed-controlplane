import { useEffect, useState } from "react";
import { api, metricsSocket } from "../api.js";

const REFRESH_MS = 1000;

export default function Execution({ onNavigate }) {
  const [connected, setConnected] = useState(false);
  const [summary, setSummary] = useState(null);
  const [orders, setOrders] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let closed = false;

    async function refresh() {
      if (closed) return;
      try {
        const rows = await api.listOrders();
        setOrders(rows || []);
        setSummary(computeSummary(rows || []));
        setError("");
      } catch (err) {
        setError(String(err).replace(/^Error:\s*/, ""));
      }
    }

    refresh();
    const id = setInterval(refresh, REFRESH_MS);

    const ws = metricsSocket(() => setConnected(true));
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    return () => {
      closed = true;
      clearInterval(id);
      ws.close();
    };
  }, []);

  const recent = orders.slice(0, 50);

  return (
    <div className="page">
      <h2>Execution quality</h2>
      <p className="muted">
        {connected ? "● live" : "○ reconnecting..."} - one-second refresh for order flow, fill quality, and execution pressure.
      </p>

      {error && <div className="error">{error}</div>}

      <div className="hero-stat">
        <div className="hero-stat-main">
          <div className="hero-stat-label">Fill rate, last {summary?.count ?? 0} orders</div>
          <div className="hero-stat-value">{fmtPct(summary?.fillRate ?? 0)}<span className="kpi-unit">%</span></div>
          <span className={`hero-stat-delta ${(summary?.fillRate ?? 0) >= 90 ? "up" : (summary?.fillRate ?? 0) >= 70 ? "flat" : "down"}`}>
            {(summary?.rejectRate ?? 0).toFixed ? fmtPct(summary?.rejectRate ?? 0) : 0}% rejected
          </span>
        </div>
      </div>

      <div className="metric-cards">
        <Metric label="Pending" value={summary?.pending ?? 0} cls={summary?.pending > 10 ? "down" : ""} />
        <Metric label="Buys / Sells" value={`${summary?.buys ?? 0} / ${summary?.sells ?? 0}`} />
        <Metric label="Latest" value={summary?.latestAt || "—"} />
      </div>

      <div className="card">
        <div className="section-head">
          <h3>Execution diagnostics</h3>
          <button className="link-btn" onClick={() => onNavigate?.("alerts")}>Open Alerts →</button>
        </div>
        <div className="metric-cards">
          <Metric label="Avg fill (buy)" value={fmt(summary?.avgBuyFill)} cls="up" />
          <Metric label="Avg fill (sell)" value={fmt(summary?.avgSellFill)} cls="down" />
          <Metric label="Route mix (paper)" value={`${summary?.paperRoutePct ?? 0}%`} />
          <Metric label="Failure score" value={`${summary?.failureScore ?? 0}`} cls={summary?.failureScore >= 40 ? "down" : summary?.failureScore >= 20 ? "" : "up"} />
        </div>
      </div>

      <div className="card">
        <h3>Recent orders</h3>
        {recent.length === 0 ? (
          <p className="muted">No orders yet. Place a paper order from Trading to populate this view.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>time</th><th>symbol</th><th>side</th><th>qty</th><th>type</th><th>status</th><th>route</th><th>fill</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((o) => (
                <tr key={o.id}>
                  <td className="mono">{fmtTime(o.created_at)}</td>
                  <td>{o.symbol}</td>
                  <td className={o.side === "buy" ? "up" : "down"}>{o.side}</td>
                  <td>{fmt(o.qty)}</td>
                  <td>{o.order_type}</td>
                  <td><span className={`status-pill ${statusClass(o.status)}`}>{o.status}</span></td>
                  <td>{o.route}</td>
                  <td>{fmt(o.fill_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value, cls }) {
  return <div className="metric-card"><div className="metric-label">{label}</div>
    <div className={`metric-value ${cls || ""}`}>{value}</div></div>;
}

function computeSummary(orders) {
  const rows = orders.slice(0, 50);
  const count = rows.length;
  const fills = rows.filter((o) => o.status === "filled");
  const rejects = rows.filter((o) => o.status === "rejected");
  const cancelled = rows.filter((o) => o.status === "cancelled");
  const pending = rows.filter((o) => o.status === "new").length;
  const buys = rows.filter((o) => o.side === "buy").length;
  const sells = rows.filter((o) => o.side === "sell").length;
  const buyFills = fills.filter((o) => o.side === "buy" && isFiniteNum(o.fill_price)).map((o) => Number(o.fill_price));
  const sellFills = fills.filter((o) => o.side === "sell" && isFiniteNum(o.fill_price)).map((o) => Number(o.fill_price));
  const paperRoutes = rows.filter((o) => String(o.route || "").toLowerCase() === "paper").length;
  const failureScore = Math.round(((rejects.length * 2 + cancelled.length + pending) / Math.max(1, count)) * 100);
  return {
    count,
    fillRate: count ? (fills.length / count) * 100 : 0,
    rejectRate: count ? (rejects.length / count) * 100 : 0,
    pending,
    buys,
    sells,
    avgBuyFill: avg(buyFills),
    avgSellFill: avg(sellFills),
    paperRoutePct: count ? Math.round((paperRoutes / count) * 100) : 0,
    latestAt: rows[0]?.created_at ? fmtTime(rows[0].created_at) : "—",
    failureScore,
  };
}

function statusClass(status) {
  if (status === "filled") return "ok";
  if (status === "rejected") return "bad";
  if (status === "cancelled") return "warn";
  return "new";
}

function avg(xs) {
  if (!xs.length) return null;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

function isFiniteNum(v) {
  return Number.isFinite(Number(v));
}

function fmt(v, dp = 2) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: dp });
}

function fmtPct(v) {
  return Number(v || 0).toFixed(1);
}

function fmtTime(v) {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleTimeString();
}
