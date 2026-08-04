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
        const point = {
          t: new Date().toLocaleTimeString(),
          trade: snapshot.rowCounts?.trade ?? 0,
          risk: snapshot.rowCounts?.risk ?? 0,
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

  return (
    <div className="page">
      <h2>Live metrics</h2>
      <p className="muted">
        {connected ? "● live" : "○ reconnecting..."} - streamed from the sharded gateway every second.
      </p>

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
        <h3>Transit lag (batch_sent → batch_arrived, per shard/table)</h3>
        {transitLag.length === 0 ? (
          <p className="muted">No lag samples yet - start the feed connectors to generate traffic.</p>
        ) : (
          <table className="data-table">
            <thead><tr><th>Shard</th><th>Table</th><th>Avg lag (ms)</th></tr></thead>
            <tbody>
              {transitLag.map((row, i) => (
                <tr key={i}>
                  <td>{row.shard}</td>
                  <td>{row.table}</td>
                  <td>{typeof row.avgMs === "number" ? row.avgMs.toFixed(2) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
