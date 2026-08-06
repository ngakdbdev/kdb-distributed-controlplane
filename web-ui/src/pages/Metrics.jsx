import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, metricsSocket } from "../api.js";

const MAX_POINTS = 60;

export default function Metrics() {
  const [history, setHistory] = useState([]);
  const [latest, setLatest] = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    api.metricsSnapshot().then(setLatest).catch(() => {});

    const ws = metricsSocket((snapshot) => {
      setConnected(true);
      setLatest(snapshot);
      setHistory((prev) => {
        const lagStats = summarizeLag(snapshot.transitLag || []);
        const point = {
          t: new Date().toLocaleTimeString(),
          trade: snapshot.rowCounts?.trade ?? 0,
          risk: snapshot.rowCounts?.risk ?? 0,
          lagMax: lagStats.max,
          lagAvg: lagStats.avg,
        };
        const next = [...prev, point];
        return next.length > MAX_POINTS ? next.slice(next.length - MAX_POINTS) : next;
      });
    });
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    wsRef.current = ws;
    return () => ws.close();
  }, []);

  const transitLag = latest?.transitLag || [];
  const health = latest?.health || [];
  const components = latest?.componentMetrics || [];
  const pressure = components.filter((row) => (row.tpQueue ?? 0) > 0 || (row.tpSubLag ?? 0) > 0 || (row.rdbConnected === false) || (row.wdbConnected === false));
  const lagStats = summarizeLag(transitLag);
  const transitScopes = buildTransitScopes(transitLag);
  const componentScopes = buildComponentScopes(components);
  const scopeCount = new Set([...transitScopes.map((scope) => scope.scope), ...componentScopes.map((scope) => scope.scope)]).size;

  return (
    <div className="page">
      <h2>Live metrics</h2>
      <p className="muted">
        {connected ? "● live" : "○ reconnecting..."} - streamed from the sharded gateway every second.
      </p>

      <div className="kpi-row">
        <div className="kpi accent"><div className="kpi-label">Tracked scopes</div><div className="kpi-value">{scopeCount}</div><div className="kpi-sub">Adaptive grouping from live gateway rows</div></div>
        <div className="kpi"><div className="kpi-label">Max transit lag</div><div className="kpi-value">{fmtMetric(lagStats.max)}<span className="kpi-unit">ms</span></div><div className="kpi-sub">Across all stages and tickhouses</div></div>
        <div className="kpi"><div className="kpi-label">Avg transit lag</div><div className="kpi-value">{fmtMetric(lagStats.avg)}<span className="kpi-unit">ms</span></div><div className="kpi-sub">Mean live delay across sampled hops</div></div>
        <div className="kpi"><div className="kpi-label">Pressure scopes</div><div className="kpi-value">{pressure.length}</div><div className="kpi-sub">Queues, lag, or downstream disconnects</div></div>
      </div>

      <div className="card">
        <h3>Row counts (trade + risk, both shards)</h3>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={history}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="t" tick={{ fontSize: 11 }} minTickGap={30} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Line type="monotone" dataKey="trade" stroke="var(--accent)" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="risk" stroke="#ff8a4f" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <h3>Transit lag pulse</h3>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={history}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="t" tick={{ fontSize: 11 }} minTickGap={30} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Line type="monotone" dataKey="lagMax" stroke="#dc2626" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="lagAvg" stroke="#0f766e" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <h3>Adaptive transit lanes</h3>
        <p className="muted">These cards are derived from whatever tickhouse, shard, or entry identifiers the gateway emits. Newly surfaced rows are grouped automatically.</p>
        {transitScopes.length === 0 ? (
          <p className="muted">No lag samples yet - start the feed connectors to generate traffic.</p>
        ) : (
          <div className="transit-grid">
            {transitScopes.map((scope) => (
              <div key={scope.scope} className="transit-card">
                <div className="transit-card-head">
                  <div>
                    <strong>{scope.scope}</strong>
                    <span>{scope.rowCount} hop samples</span>
                  </div>
                  <span className={`status-pill ${scope.maxLag >= 25 ? "bad" : scope.maxLag >= 10 ? "warn" : "ok"}`}>{fmtMetric(scope.maxLag)} ms</span>
                </div>
                <div className="transit-stats">
                  <div className="transit-stat"><span>Avg lag</span><b>{fmtMetric(scope.avgLag)} ms</b></div>
                  <div className="transit-stat"><span>Max queue</span><b>{fmtMetric(scope.maxQueue, 0)}</b></div>
                  <div className="transit-stat"><span>Rows seen</span><b>{fmtMetric(scope.maxRows, 0)}</b></div>
                </div>
                <div className="transit-stage-list">
                  {scope.stages.map((stage) => (
                    <div key={`${scope.scope}-${stage.label}`} className="transit-stage-pill">
                      <span>{stage.label}</span>
                      <b>{fmtMetric(stage.lag)} ms</b>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h3>Transit lag by hop (feed → TP → RDB → gateway)</h3>
        {transitLag.length === 0 ? (
          <p className="muted">No lag samples yet - start the feed connectors to generate traffic.</p>
        ) : (
          <table className="data-table">
            <thead><tr><th>Shard</th><th>Table</th><th>Stage</th><th>Lag (ms)</th><th>TP queue (bytes)</th><th>RDB rows</th></tr></thead>
            <tbody>
              {transitLag.map((row, i) => (
                <tr key={i}>
                  <td>{row.shard}</td>
                  <td>{row.table}</td>
                  <td>{row.stage || "-"}</td>
                  <td>{typeof row.lagMs === "number" ? row.lagMs.toFixed(2) : (typeof row.avgMs === "number" ? row.avgMs.toFixed(2) : "-")}</td>
                  <td>{row.queueBytes ?? "-"}</td>
                  <td>{row.rdbRows ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>Adaptive component metrics</h3>
        {componentScopes.length === 0 ? (
          <p className="muted">Component metrics not available yet.</p>
        ) : (
          <div className="component-grid">
            {componentScopes.map((row) => (
              <div key={row.scope} className="component-card">
                <div className="component-card-head">
                  <strong>{row.scope}</strong>
                  <span>{row.connectedLabel}</span>
                </div>
                <div className="component-metrics-grid">
                  <div className="component-metric"><span>TP recv</span><b>{fmtMetric(row.tpRecv, 0)}</b></div>
                  <div className="component-metric"><span>TP pub</span><b>{fmtMetric(row.tpPub, 0)}</b></div>
                  <div className="component-metric"><span>TP queue</span><b>{fmtMetric(row.tpQueue, 0)}</b></div>
                  <div className="component-metric"><span>TP sub lag</span><b>{fmtMetric(row.tpSubLag, 0)}</b></div>
                  <div className="component-metric"><span>RDB rows</span><b>{fmtMetric(row.rdbRowsTrade + row.rdbRowsRisk, 0)}</b></div>
                  <div className="component-metric"><span>RDB reconnects</span><b>{fmtMetric(row.rdbReconnects, 0)}</b></div>
                  <div className="component-metric"><span>WDB reconnects</span><b>{fmtMetric(row.wdbReconnects, 0)}</b></div>
                  <div className="component-metric"><span>Watermark</span><b className="mono">{row.wdbLastWatermark || "—"}</b></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h3>Subscriber pressure</h3>
        <p className="muted">This surface makes backpressure visible so you can scale or shed subscribers before the RDB/gateway path saturates.</p>
        {pressure.length === 0 ? (
          <p className="muted">No elevated TP queue or downstream lag right now.</p>
        ) : (
          <div className="pressure-grid">
            {pressure.map((row) => (
              <div key={row.shard} className="pressure-card">
                <div className="pressure-head">
                  <strong>{row.shard}</strong>
                  <span>{String(row.rdbConnected)}/{String(row.wdbConnected)}</span>
                </div>
                <div className="pressure-row"><span>TP queue</span><b>{row.tpQueue ?? 0}</b></div>
                <div className="pressure-row"><span>TP sub lag</span><b>{row.tpSubLag ?? 0}</b></div>
                <div className="pressure-row"><span>RDB rows</span><b>{(row.rdbRowsTrade ?? 0) + (row.rdbRowsRisk ?? 0)}</b></div>
                <div className="pressure-row"><span>WDB watermark</span><b className="mono">{row.wdbLastWatermark ?? "—"}</b></div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h3>Per-shard tier health</h3>
        {health.length === 0 ? (
          <p className="muted">Gateway not reachable yet.</p>
        ) : (
          <table className="data-table">
            <thead><tr><th>Shard</th><th>RDB rows (trade/risk)</th><th>IDB rows (trade/risk)</th></tr></thead>
            <tbody>
              {health.map((row, i) => (
                <tr key={i}>
                  <td>{row.shard}</td>
                  <td>{row.rdb?.rowsTrade ?? "-"} / {row.rdb?.rowsRisk ?? "-"}</td>
                  <td>{row.idb?.rowsTrade ?? "-"} / {row.idb?.rowsRisk ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function summarizeLag(rows) {
  const lags = rows.map((row) => Number(row.lagMs ?? row.avgMs)).filter((value) => Number.isFinite(value));
  if (!lags.length) return { avg: 0, max: 0 };
  return {
    avg: lags.reduce((sum, value) => sum + value, 0) / lags.length,
    max: Math.max(...lags),
  };
}

function buildTransitScopes(rows) {
  const grouped = new Map();
  for (const row of rows || []) {
    const scope = scopeLabel(row.shard || row.tickhouse || row.entry || "gateway");
    const lag = Number(row.lagMs ?? row.avgMs ?? 0);
    const queue = Number(row.queueBytes ?? 0);
    const rdbRows = Number(row.rdbRows ?? 0);
    const entry = grouped.get(scope) || { scope, stages: [], rowCount: 0, maxLag: 0, avgLag: 0, maxQueue: 0, maxRows: 0 };
    entry.stages.push({ label: `${row.table || "table"} · ${row.stage || "stage"}`, lag });
    entry.rowCount += 1;
    entry.avgLag += lag;
    entry.maxLag = Math.max(entry.maxLag, lag);
    entry.maxQueue = Math.max(entry.maxQueue, queue);
    entry.maxRows = Math.max(entry.maxRows, rdbRows);
    grouped.set(scope, entry);
  }
  return [...grouped.values()].map((entry) => ({
    ...entry,
    avgLag: entry.rowCount ? entry.avgLag / entry.rowCount : 0,
    stages: entry.stages.sort((left, right) => right.lag - left.lag),
  })).sort((left, right) => right.maxLag - left.maxLag);
}

function buildComponentScopes(rows) {
  return (rows || []).map((row) => ({
    scope: scopeLabel(row.shard || row.tickhouse || row.entry || "gateway"),
    tpRecv: Number(row.tpRecv || 0),
    tpPub: Number(row.tpPub || 0),
    tpQueue: Number(row.tpQueue || 0),
    tpSubLag: Number(row.tpSubLag || 0),
    rdbRowsTrade: Number(row.rdbRowsTrade || 0),
    rdbRowsRisk: Number(row.rdbRowsRisk || 0),
    rdbReconnects: Number(row.rdbReconnects || 0),
    wdbReconnects: Number(row.wdbReconnects || 0),
    wdbLastWatermark: row.wdbLastWatermark,
    connectedLabel: `${String(row.rdbConnected)} / ${String(row.wdbConnected)}`,
  })).sort((left, right) => right.tpQueue - left.tpQueue || right.tpSubLag - left.tpSubLag);
}

function scopeLabel(value) {
  const text = String(value || "gateway");
  return text.replace(/-s\d+$/i, "") || text;
}

function fmtMetric(value, digits = 2) {
  if (value == null || Number.isNaN(value)) return "0";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}
