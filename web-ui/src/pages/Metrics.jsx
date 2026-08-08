import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, metricsSocket } from "../api.js";

const MAX_POINTS = 60;

export default function Metrics() {
  const [history, setHistory] = useState([]);
  const [latest, setLatest] = useState(null);
  const [connected, setConnected] = useState(false);
  const [msgsPerSec, setMsgsPerSec] = useState(0);
  const [totalMsgs, setTotalMsgs] = useState(0);
  const wsRef = useRef(null);
  // tpRecv (see data-plane/q/tick.q .u.stats) is a monotonically increasing
  // counter since that tickerplant started - the q code's own comment says
  // "the control plane polls this and derives per-second rates from deltas
  // between polls," which nothing actually did until now. Tracked in a ref
  // (not state) purely as "previous sample" bookkeeping for the rate calc -
  // it should never itself trigger a render.
  const prevMsgRef = useRef(null);
  // Rolling pool of REAL observed per-hop lag samples (feed->tp->rdb->gateway,
  // never the flush-lag stage) - every websocket tick contributes whatever
  // transitLag rows it carried, capped so this stays a "recent window" rather
  // than growing forever. Percentiles below are computed straight from this,
  // not synthesized - kdb+ shops care about tail latency (p99/p99.9), not the
  // average, so that's what a serious dashboard should lead with.
  const lagSamplesRef = useRef([]);
  const LAG_SAMPLE_CAP = 2000;
  const [latencyDist, setLatencyDist] = useState({ p50: 0, p90: 0, p99: 0, p999: 0, max: 0, n: 0 });

  useEffect(() => {
    api.metricsSnapshot().then(setLatest).catch(() => {});

    const ws = metricsSocket((snapshot) => {
      setConnected(true);
      setLatest(snapshot);

      const total = (snapshot.componentMetrics || []).reduce((sum, row) => sum + (Number(row.tpRecv) || 0), 0);
      const now = Date.now();
      let rate = 0;
      const prevSample = prevMsgRef.current;
      if (prevSample && total >= prevSample.total) {
        const elapsedSec = (now - prevSample.time) / 1000;
        if (elapsedSec > 0) rate = (total - prevSample.total) / elapsedSec;
      }
      // a shrinking total means a tickerplant restarted and its in-memory
      // counter reset to 0 - report 0 for that tick rather than a bogus
      // negative rate; the next tick picks the new baseline up cleanly.
      prevMsgRef.current = { total, time: now };
      setMsgsPerSec(rate);
      setTotalMsgs(total);

      const liveRowsForSampling = (snapshot.transitLag || []).filter((row) => row.stage !== "tp_to_wdb_flush");
      const newLags = liveRowsForSampling.map((row) => Number(row.lagMs ?? row.avgMs)).filter(Number.isFinite);
      if (newLags.length) {
        const pool = [...lagSamplesRef.current, ...newLags];
        lagSamplesRef.current = pool.length > LAG_SAMPLE_CAP ? pool.slice(pool.length - LAG_SAMPLE_CAP) : pool;
        const sorted = [...lagSamplesRef.current].sort((a, b) => a - b);
        setLatencyDist({
          p50: percentile(sorted, 0.50), p90: percentile(sorted, 0.90),
          p99: percentile(sorted, 0.99), p999: percentile(sorted, 0.999),
          max: sorted[sorted.length - 1] ?? 0, n: sorted.length,
        });
      }

      setHistory((prev) => {
        const liveRows = (snapshot.transitLag || []).filter((row) => row.stage !== "tp_to_wdb_flush");
        const lagStats = summarizeLag(liveRows);
        const point = {
          t: new Date().toLocaleTimeString(),
          trade: snapshot.rowCounts?.trade ?? 0,
          risk: snapshot.rowCounts?.risk ?? 0,
          lagMax: lagStats.max,
          lagAvg: lagStats.avg,
          msgsPerSec: Math.round(rate),
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
  // tp_to_wdb_flush is NOT comparable to the other hops: wdb deliberately
  // only flushes rows older than its flush interval (2 min default - see
  // data-plane/q/wdb.q's flushIntv), so this stage's lag legitimately
  // sawtooths up near that interval even when everything is healthy. Lumping
  // it into the same "transit lag" number as the live feed->tp->rdb->gateway
  // hops (which really are sub-second) made the headline KPI both perpetually
  // alarming AND useless for its actual job - a real live-pipeline slowdown
  // would be invisible, dwarfed by the always-present ~2min flush figure.
  const liveTransitLag = transitLag.filter((row) => row.stage !== "tp_to_wdb_flush");
  const flushLag = transitLag.filter((row) => row.stage === "tp_to_wdb_flush");
  const lagStats = summarizeLag(liveTransitLag);
  const flushLagStats = summarizeLag(flushLag);
  const transitScopes = buildTransitScopes(liveTransitLag);
  const componentScopes = buildComponentScopes(components);
  const scopeCount = new Set([...transitScopes.map((scope) => scope.scope), ...componentScopes.map((scope) => scope.scope)]).size;

  return (
    <div className="page">
      <h2>Live metrics</h2>
      <p className="muted">
        {connected ? "● live" : "○ reconnecting..."} - streamed from the sharded gateway every second.
      </p>

      <div className="kpi-row hero">
        <div className="kpi accent glow">
          <div className="kpi-label">Messages ingested / sec</div>
          <div className="kpi-value">{fmtMetric(msgsPerSec, 0)}</div>
          <div className="kpi-sub">Live tickerplant receive rate, all shards</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Total messages ingested</div>
          <div className="kpi-value">{fmtCompact(totalMsgs)}</div>
          <div className="kpi-sub">Cumulative since each tickerplant started</div>
        </div>
        <div className="kpi"><div className="kpi-label">Tracked scopes</div><div className="kpi-value">{scopeCount}</div><div className="kpi-sub">Adaptive grouping from live gateway rows</div></div>
        <div className="kpi"><div className="kpi-label">Pressure scopes</div><div className="kpi-value">{pressure.length}</div><div className="kpi-sub">Queues, lag, or downstream disconnects</div></div>
      </div>
      <div className="kpi-row">
        <div className="kpi"><div className="kpi-label">Max transit lag</div><div className="kpi-value">{fmtMetric(lagStats.max)}<span className="kpi-unit">ms</span></div><div className="kpi-sub">Live feed → TP → RDB → gateway hops only</div></div>
        <div className="kpi"><div className="kpi-label">Avg transit lag</div><div className="kpi-value">{fmtMetric(lagStats.avg)}<span className="kpi-unit">ms</span></div><div className="kpi-sub">Mean live delay across sampled hops</div></div>
        <div className="kpi" title="Time since wdb's last durable flush - wdb only flushes rows older than its flush interval by design, so this normally sawtooths up toward that interval (2 min default) and resets on each flush. Not a live-query latency signal.">
          <div className="kpi-label">Flush lag</div><div className="kpi-value">{fmtMetric(flushLagStats.max / 1000, 1)}<span className="kpi-unit">s</span></div><div className="kpi-sub">Durability only - expected up to the flush interval</div>
        </div>
      </div>

      <div className="card highlight">
        <h3>Ingest throughput (messages / sec)</h3>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={history}>
            <defs>
              <linearGradient id="msgFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" tick={{ fontSize: 11 }} minTickGap={30} />
            <YAxis tick={{ fontSize: 11 }} unit="/s" />
            <Tooltip formatter={(value) => `${Number(value).toLocaleString()} msg/s`} />
            <Line type="monotone" dataKey="msgsPerSec" name="messages/sec" stroke="var(--accent)" dot={false}
                  strokeWidth={2.5} fill="url(#msgFill)" activeDot={{ r: 4 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <h3>Row counts (trade + risk, both shards)</h3>
        <p className="muted">Cumulative rows landed in the RDB since it started - a flat line means that feed is idle, not broken.</p>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={history} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="tradeFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.28} />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" tick={{ fontSize: 11 }} minTickGap={30} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend verticalAlign="top" height={28} iconType="line" />
            <Line type="monotone" dataKey="trade" name="trade rows" stroke="var(--accent)" dot={false}
                  strokeWidth={2.5} fill="url(#tradeFill)" activeDot={{ r: 4 }} />
            <Line type="monotone" dataKey="risk" name="risk rows" stroke="#ff8a4f" dot={false}
                  strokeWidth={2} activeDot={{ r: 4 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <h3>Transit lag pulse</h3>
        <p className="muted">Max vs. average per-hop lag over time, live feed hops only (excludes the durability-bounded flush stage).</p>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={history} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" tick={{ fontSize: 11 }} minTickGap={30} />
            <YAxis tick={{ fontSize: 11 }} unit="ms" />
            <Tooltip formatter={(value) => `${Number(value).toFixed(2)} ms`} />
            <Legend verticalAlign="top" height={28} iconType="line" />
            <Line type="monotone" dataKey="lagMax" name="max lag" stroke="#dc2626" dot={false} strokeWidth={2.2} activeDot={{ r: 4 }} />
            <Line type="monotone" dataKey="lagAvg" name="avg lag" stroke="#0f766e" dot={false} strokeWidth={2} strokeDasharray="5 3" activeDot={{ r: 4 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <div className="section-head">
          <h3>Latency distribution (feed → TP → RDB → gateway)</h3>
          <span className="muted" style={{ fontSize: "0.78rem" }}>{latencyDist.n} recent hop samples</span>
        </div>
        <p className="muted">
          Tail latency, not the average - a p99.9 spike hiding behind a fine-looking mean is exactly what an
          average-only dashboard misses.
        </p>
        {latencyDist.n === 0 ? (
          <p className="muted">No lag samples yet - start the feed connectors to generate traffic.</p>
        ) : (
          <div className="latency-hist">
            {[["p50", latencyDist.p50], ["p90", latencyDist.p90], ["p99", latencyDist.p99],
              ["p99.9", latencyDist.p999], ["max", latencyDist.max]].map(([label, value]) => (
              <div className="latency-hist-row" key={label}>
                <span className="latency-hist-label">{label}</span>
                <span className="latency-hist-bar-track">
                  <span className="latency-hist-bar" style={{
                    width: `${Math.min(100, (value / Math.max(1, latencyDist.max)) * 100)}%`,
                  }} />
                </span>
                <span className="latency-hist-value">{fmtMetric(value)} ms</span>
              </div>
            ))}
          </div>
        )}
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

function percentile(sortedAsc, p) {
  if (!sortedAsc.length) return 0;
  const idx = Math.min(sortedAsc.length - 1, Math.floor(p * sortedAsc.length));
  return sortedAsc[idx];
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

function fmtCompact(value) {
  if (value == null || Number.isNaN(value)) return "0";
  return Number(value).toLocaleString(undefined, { notation: "compact", maximumFractionDigits: 1 });
}
