import { useEffect, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api.js";
import { buildTimeForecast } from "../lib/timeForecast.js";
import {
  buildCorrelationMatrix, changePct, correlationPairs, corrClass, fetchTradeTape, fmt,
  normalizeSeries, paletteColor, seriesReturns,
} from "../lib/tradingCore.js";
import { loadWatchlist } from "../lib/watchlist.js";

const REFRESH_MS = 1000;
const TOOLTIP_STYLE = {
  background: "#161b26", border: "1px solid #232938", borderRadius: 10,
  color: "#eef1f7", fontSize: "0.82rem", boxShadow: "0 12px 28px rgba(0,0,0,.4)",
};
const AXIS_TICK = { fontSize: 11, fill: "var(--muted)" };

// Your holdings: positions/P&L, a per-position 10-minute exposure forecast,
// and basket correlation - split out of the old Trading.jsx so "what am I
// holding and how might it move" is its own page instead of being mixed in
// with order entry and pure market-data browsing.
export default function Portfolio({ onNavigate }) {
  const [portfolio, setPortfolio] = useState(null);
  const [drilldown, setDrilldown] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [focus, setFocus] = useState(null);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refresh() {
    try {
      const orders = await api.listOrders();
      const marks = orders.filter((o) => o.fill_price).map((o) => `${o.symbol}:${o.fill_price}`).join(",");
      const summary = await api.getPositions(marks);
      setPortfolio(summary);
      const held = (summary?.positions || []).filter((p) => Number(p.qty) !== 0);
      // Watchlist symbols (shared with Markets, lib/watchlist.js) that aren't
      // held yet still get pulled into the constellation/correlation view and
      // their own watch-only rows below - this is what makes Portfolio pick
      // up a symbol you just added on Markets without any extra step.
      const watchOnly = loadWatchlist().filter((sym) => !held.some((pos) => pos.symbol === sym));
      setDrilldown(await loadPortfolioDrilldown(held));
      setAnalytics(await loadPortfolioAnalytics(held, watchOnly));
      if (!focus && held.length) setFocus(held[0].symbol);
    } catch { /* best effort - keep showing the last good snapshot */ }
  }

  const focusRow = drilldown.find((r) => r.symbol === focus) || null;
  const focusPosition = portfolio?.positions?.find((p) => p.symbol === focus) || null;

  return (
    <div className="page">
      <h2>Portfolio</h2>
      <p className="muted">Positions, P&amp;L, a 10-minute exposure forecast per holding, and basket correlation. Paper positions only.</p>

      {analytics?.members?.some((m) => m.qty === 0) && (
        <div className="card" style={{ padding: "0.4rem 1rem" }}>
          <h3>Watchlist <span className="muted" style={{ fontWeight: 400 }}>tracked on Markets, not held yet</span></h3>
          <div>
            {analytics.members.filter((m) => m.qty === 0).map((m) => (
              <div className="list-row" key={m.symbol}>
                <span className="list-row-icon">{m.symbol.slice(0, 2)}</span>
                <div className="list-row-main"><div className="list-row-title">{m.symbol}</div><div className="list-row-sub">{m.signalText}</div></div>
                <div className="list-row-value">
                  <b>{m.last != null ? fmt(m.last) : "—"}</b>
                  <span className={m.changePct >= 0 ? "up" : "down"}>{m.last != null ? `${m.changePct >= 0 ? "▲" : "▼"} ${fmt(Math.abs(m.changePct), 2)}%` : ""}</span>
                </div>
                <button className="chip" onClick={() => onNavigate?.("orders", { symbol: m.symbol, side: "buy" })}>Trade →</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {!portfolio?.positions?.length ? (
        <div className="card"><p className="muted" style={{ margin: 0 }}>No positions yet — place a paper order from Orders to see them here.</p></div>
      ) : (
        <>
          <div className="metric-cards">
            <Metric label="Net exposure" value={fmt(portfolio.net_exposure)} />
            <Metric label="Gross" value={fmt(portfolio.gross_exposure)} />
            <Metric label="Unreal. P&L" value={fmt(portfolio.unrealized_pnl)} cls={portfolio.unrealized_pnl >= 0 ? "up" : "down"} />
            <Metric label="Realized" value={fmt(portfolio.realized_pnl)} cls={portfolio.realized_pnl >= 0 ? "up" : "down"} />
            <Metric label="Concentration" value={`${fmt(portfolio.concentration_pct)}%`} />
          </div>

          <div className="card">
            <h3>Positions</h3>
            <table className="data-table">
              <thead><tr><th>symbol</th><th>qty</th><th>avg</th><th>last</th><th>mkt val</th><th>P&amp;L</th><th></th></tr></thead>
              <tbody>
                {portfolio.positions.map((pos) => (
                  <tr key={pos.symbol}>
                    <td>{pos.symbol}</td><td>{fmt(pos.qty)}</td><td>{fmt(pos.avg_price)}</td><td>{fmt(pos.last)}</td>
                    <td>{fmt(pos.market_value)}</td><td className={pos.unrealized_pnl >= 0 ? "up" : "down"}>{fmt(pos.unrealized_pnl)}</td>
                    <td style={{ display: "flex", gap: "0.35rem" }}>
                      <button className="chip" onClick={() => onNavigate?.("markets", { symbol: pos.symbol })}>View →</button>
                      <button className="chip" onClick={() => onNavigate?.("orders", { symbol: pos.symbol })}>Trade →</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {focusRow && (
            <div className="card">
              <h3>Impact focus <span className="muted" style={{ fontWeight: 400 }}>{focus}</span></h3>
              <div className="metric-cards">
                <Metric label="Current P&L" value={fmt(focusPosition?.unrealized_pnl)} cls={focusPosition?.unrealized_pnl >= 0 ? "up" : "down"} />
                <Metric label="Projected P&L (10m)" value={fmt(focusRow.projectedPnl)} cls={focusRow.projectedPnl >= 0 ? "up" : "down"} />
                <Metric label="Held qty" value={fmt(focusPosition?.qty)} />
                <Metric label="Projected move (10m)" value={fmt(focusRow.projectedMove)} cls={focusRow.projectedMove >= 0 ? "up" : "down"} />
              </div>
              <p className="muted">Forecasted impact if the expected 10-minute move lands near the band midpoint — not advice, see Markets for the full confidence cone.</p>
            </div>
          )}

          {drilldown.length > 0 && (
            <div className="card">
              <div className="section-head"><h3>Exposure forecast</h3><span className="muted">click a row to focus it above</span></div>
              <table className="data-table portfolio-forecast-table">
                <thead><tr><th>symbol</th><th>qty</th><th>weight</th><th>trend</th><th>next 10m move</th><th>projected P&amp;L</th><th>last</th></tr></thead>
                <tbody>
                  {drilldown.map((row) => (
                    <tr key={row.symbol} className={row.symbol === focus ? "is-selected" : ""} onClick={() => setFocus(row.symbol)}>
                      <td>{row.symbol}</td>
                      <td>{fmt(row.qty)}</td>
                      <td>{fmt(row.weight_pct)}%</td>
                      <td><span className={`signal-pill ${row.signal}`}>{row.signalText}</span></td>
                      <td className={row.projectedMove >= 0 ? "up" : "down"}>{fmt(row.projectedMove)} ({fmt(row.projectedMovePct)}%)</td>
                      <td className={row.projectedPnl >= 0 ? "up" : "down"}>{fmt(row.projectedPnl)}</td>
                      <td>{fmt(row.last)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {analytics && (
            <div className="card desk-card">
              <div className="section-head"><h3>Portfolio constellation</h3><span className="muted">Auto-refreshing basket analytics</span></div>
              <div className="metric-cards">
                <Metric label="Names tracked" value={fmt(analytics.members.length, 0)} />
                <Metric label="Avg correlation" value={fmt(analytics.averageCorrelation, 2)} cls={analytics.averageCorrelation >= 0.65 ? "warn" : ""} />
                <Metric label="Projected basket P&L" value={fmt(analytics.projectedPnl)} cls={analytics.projectedPnl >= 0 ? "up" : "down"} />
                <Metric label="Breadth" value={`${analytics.advancers}/${analytics.members.length}`} cls={analytics.advancers >= Math.ceil(analytics.members.length / 2) ? "up" : "down"} />
                <Metric label="Tightest pair" value={analytics.strongestPair?.label || "—"} />
                <Metric label="Most inverse" value={analytics.weakestPair?.label || "—"} cls={analytics.weakestPair && analytics.weakestPair.value < 0 ? "down" : ""} />
              </div>
              <div className="basket-layout">
                <div className="basket-panel basket-chart-panel">
                  <div className="basket-panel-head"><h4>Normalized multi-instrument tape</h4><span>Base 100, recent prints</span></div>
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={analytics.chartSeries} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                      <XAxis dataKey="t" tick={AXIS_TICK} minTickGap={20} stroke="var(--border)"
                             label={{ value: "recent prints (oldest → newest)", position: "insideBottom", offset: -2, fontSize: 11, fill: "var(--muted)" }} />
                      <YAxis tick={AXIS_TICK} domain={["auto", "auto"]} stroke="var(--border)"
                             label={{ value: "indexed to 100 at series start", angle: -90, position: "insideLeft", fontSize: 11, fill: "var(--muted)" }} />
                      <Tooltip formatter={(value, name) => [Number(value).toFixed(2), name]} labelFormatter={(label) => `print #${label}`} contentStyle={TOOLTIP_STYLE} />
                      <Legend verticalAlign="top" height={28} iconType="line" wrapperStyle={{ fontSize: "0.82rem", color: "var(--text-dim)" }} />
                      <ReferenceLine y={100} stroke="var(--muted)" strokeDasharray="4 4" label={{ value: "start (100)", position: "right", fontSize: 10, fill: "var(--muted)" }} />
                      {analytics.members.map((member, idx) => (
                        <Line key={member.symbol} type="monotone" dataKey={member.symbol} stroke={paletteColor(idx)} dot={false}
                              activeDot={{ r: 4 }} strokeWidth={member.symbol === focus ? 3 : 1.6} strokeOpacity={member.symbol === focus ? 1 : 0.65} />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <div className="basket-panel">
                  <div className="basket-panel-head"><h4>Correlation matrix</h4><span>Rolling return overlap</span></div>
                  <CorrelationMatrix matrix={analytics.correlationMatrix} />
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Metric({ label, value, cls }) {
  return <div className="metric-card"><div className="metric-label">{label}</div><div className={`metric-value ${cls || ""}`}>{value}</div></div>;
}

function CorrelationMatrix({ matrix }) {
  if (!matrix?.length) return <p className="muted">Not enough overlapping tape to compute correlations yet.</p>;
  return (
    <div className="corr-matrix">
      <div className="corr-row corr-header">
        <span className="corr-cell corr-corner">sym</span>
        {matrix.map((row) => <span key={row.symbol} className="corr-cell corr-label">{row.symbol}</span>)}
      </div>
      {matrix.map((row) => (
        <div key={row.symbol} className="corr-row">
          <span className="corr-cell corr-label">{row.symbol}</span>
          {row.values.map((cell) => (
            <span key={`${row.symbol}-${cell.symbol}`} className={`corr-cell ${corrClass(cell.value)}`} title={`${row.symbol}/${cell.symbol}: ${fmt(cell.value, 2)}`}>
              {fmt(cell.value, 2)}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

async function loadPortfolioDrilldown(held) {
  const top = [...held].sort((a, b) => Math.abs(Number(b.market_value || 0)) - Math.abs(Number(a.market_value || 0))).slice(0, 6);
  if (!top.length) return [];
  try {
    const { res } = await fetchTradeTape(top.map((pos) => pos.symbol), 1200);
    const grouped = groupRowsBySymbol(res, top[0]?.symbol);
    return top.map((pos) => {
      const rows = grouped.get(pos.symbol) || [];
      const forecast = buildTimeForecast(rows);
      const next = forecast.points?.[0] || null; // shortest horizon = 10m
      const lastPrice = forecast.last ?? pos.last;
      const projectedMove = next && lastPrice != null ? next.expected - lastPrice : 0;
      const projectedPnl = projectedMove * Number(pos.qty || 0);
      return {
        symbol: pos.symbol, qty: pos.qty, weight_pct: pos.weight_pct, last: lastPrice ?? pos.last,
        projectedMove, projectedMovePct: lastPrice ? (projectedMove / lastPrice) * 100 : 0, projectedPnl,
        signal: forecast.trend, signalText: forecast.trend === "up" ? "momentum up" : forecast.trend === "down" ? "momentum down" : rows.length ? "flat" : "no tape",
      };
    });
  } catch {
    return top.map((pos) => ({
      symbol: pos.symbol, qty: pos.qty, weight_pct: pos.weight_pct, last: pos.last,
      projectedMove: 0, projectedMovePct: 0, projectedPnl: 0, signal: "flat", signalText: "no tape",
    }));
  }
}

async function loadPortfolioAnalytics(held, watchOnly = []) {
  const basket = [...new Set([...held.map((pos) => pos.symbol), ...watchOnly])].slice(0, 6);
  if (!basket.length) return null;
  const positionMap = new Map(held.map((pos) => [pos.symbol, pos]));
  try {
    const { res } = await fetchTradeTape(basket, 2400);
    const grouped = groupRowsBySymbol(res, basket[0]);
    const members = basket.map((sym) => {
      const rows = (grouped.get(sym) || []).filter((row) => Number.isFinite(row.price));
      rows.sort((left, right) => String(left.time || "").localeCompare(String(right.time || "")));
      const prices = rows.map((row) => row.price);
      const forecast = buildTimeForecast(rows);
      const next = forecast.points?.[0] || null;
      const pos = positionMap.get(sym) || {};
      const lastPrice = forecast.last ?? prices[prices.length - 1] ?? null;
      const projectedMove = next && lastPrice != null ? next.expected - lastPrice : 0;
      return {
        symbol: sym, qty: Number(pos.qty || 0), last: lastPrice, changePct: changePct(prices),
        signal: forecast.trend, signalText: forecast.trend === "up" ? "momentum up" : forecast.trend === "down" ? "momentum down" : rows.length ? "flat" : "no tape",
        projectedPnl: projectedMove * Number(pos.qty || 0), correlationAverage: 0,
        indexSeries: normalizeSeries(prices), returns: seriesReturns(prices),
      };
    });
    const correlationMatrix = buildCorrelationMatrix(members);
    const enrichedMembers = members.map((member) => {
      const matrixRow = correlationMatrix.find((row) => row.symbol === member.symbol);
      const peers = (matrixRow?.values || []).filter((cell) => cell.symbol !== member.symbol && Number.isFinite(cell.value));
      const correlationAverage = peers.length ? peers.reduce((sum, cell) => sum + cell.value, 0) / peers.length : 0;
      return { ...member, correlationAverage };
    });
    const pairs = correlationPairs(correlationMatrix);
    return {
      members: enrichedMembers, chartSeries: buildBasketChartSeries(enrichedMembers), correlationMatrix,
      strongestPair: pairs[0] || null, weakestPair: pairs[pairs.length - 1] || null,
      averageCorrelation: pairs.length ? pairs.reduce((sum, pair) => sum + pair.value, 0) / pairs.length : 0,
      projectedPnl: enrichedMembers.reduce((sum, member) => sum + Number(member.projectedPnl || 0), 0),
      advancers: enrichedMembers.filter((member) => Number(member.changePct || 0) >= 0).length,
    };
  } catch {
    return null;
  }
}

function groupRowsBySymbol(res, fallbackSymbol) {
  const cols = res.columns || [];
  const ti = cols.indexOf("time"), si = cols.indexOf("sym"), pi = cols.indexOf("price"), zi = cols.indexOf("size");
  const grouped = new Map();
  for (const row of res.rows || []) {
    const sym = si >= 0 ? row[si] : fallbackSymbol;
    if (!sym) continue;
    const bucket = grouped.get(sym) || [];
    bucket.push({ time: ti >= 0 ? row[ti] : null, price: pi >= 0 ? Number(row[pi]) : null, size: zi >= 0 ? Number(row[zi]) : null });
    grouped.set(sym, bucket);
  }
  return grouped;
}

function buildBasketChartSeries(members) {
  const trimmed = members.map((member) => ({ ...member, indexSeries: member.indexSeries.slice(-36) }));
  const maxLen = Math.max(...trimmed.map((member) => member.indexSeries.length), 0);
  return Array.from({ length: maxLen }, (_, idx) => {
    const point = { t: `${idx + 1}` };
    for (const member of trimmed) {
      const offset = maxLen - member.indexSeries.length;
      point[member.symbol] = idx >= offset ? member.indexSeries[idx - offset] : null;
    }
    return point;
  });
}
