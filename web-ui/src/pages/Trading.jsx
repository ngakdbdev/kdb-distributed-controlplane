import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";

export default function Trading({ onNavigate }) {
  const REFRESH_MS = 1000;
  const liveRunRef = useRef(false);
  const symbolsRef = useRef(["AAPL", "MSFT"]);
  const [perm, setPerm] = useState({ can_trade: false, mode: "paper" });
  const [syms, setSyms] = useState(["AAPL", "MSFT"]);
  const symbol = syms[0] || "AAPL";
  const [market, setMarket] = useState(null);
  const [forecast, setForecast] = useState(null);
  const [symbolDrilldown, setSymbolDrilldown] = useState(null);
  const [sample, setSample] = useState(false);
  const [orders, setOrders] = useState([]);
  const [portfolio, setPortfolio] = useState(null);
  const [portfolioDrilldown, setPortfolioDrilldown] = useState([]);
  const [portfolioAnalytics, setPortfolioAnalytics] = useState(null);
  const [pressure, setPressure] = useState(null);
  const [queryHealth, setQueryHealth] = useState({ status: "idle", message: "Waiting for query path…" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => { symbolsRef.current = syms; }, [syms]);
  useEffect(() => { api.tradingPermission().then(setPerm).catch(() => {}); }, []);

  useEffect(() => {
    refreshBlotter();
    refreshPressure();
    const id = setInterval(() => {
      refreshBlotter();
      refreshPressure();
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!portfolio) return;
    loadPortfolioAnalytics(portfolio.positions || [], symbolsRef.current)
      .then(setPortfolioAnalytics)
      .catch(() => {});
  }, [syms, portfolio]);

  useEffect(() => {
    if (sample || !market) return;
    const id = setInterval(() => {
      if (liveRunRef.current) return;
      liveRunRef.current = true;
      fetchMarketData(symbol, { clear: false, quiet: true }).catch((err) => {
        setQueryHealth({ status: "bad", message: explainQueryFailure(err) });
      }).finally(() => {
        liveRunRef.current = false;
      });
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [symbol, sample, market]);

  async function refreshBlotter() {
    try {
      const nextOrders = await api.listOrders();
      setOrders(nextOrders);
      const marks = nextOrders.filter((order) => order.fill_price).map((order) => `${order.symbol}:${order.fill_price}`).join(",");
      const summary = await api.getPositions(marks);
      setPortfolio(summary);
      setPortfolioDrilldown(await loadPortfolioDrilldown(summary?.positions || []));
      setPortfolioAnalytics(await loadPortfolioAnalytics(summary?.positions || [], symbolsRef.current));
    } catch (err) {
      setPortfolioAnalytics(null);
    }
  }

  async function refreshPressure() {
    try {
      const snap = await api.metricsSnapshot();
      setPressure(summarizePressure(snap));
    } catch (err) {
      setPressure(null);
    }
  }

  async function loadMarket() {
    setLoading(true);
    setError("");
    setSample(false);
    try {
      await fetchMarketData(symbol, { clear: true, quiet: false });
    } catch (err) {
      setQueryHealth({ status: "bad", message: explainQueryFailure(err) });
      setError(`couldn't load market data (${String(err).replace(/^Error:\s*/, "")}). Load a live cluster with trades for ${symbol}, or use Sample data to preview the panels.`);
    } finally {
      setLoading(false);
    }
  }

  async function loadSample() {
    setLoading(true);
    setError("");
    setMarket(null);
    setForecast(null);
    setSymbolDrilldown(null);
    const prices = [180];
    for (let i = 1; i < 80; i += 1) {
      prices.push(Math.max(1, +(prices[i - 1] * (1 + (Math.random() - 0.48) * 0.02)).toFixed(2)));
    }
    const sizes = prices.map(() => Math.round(100 + Math.random() * 900));
    try {
      const rows = prices.map((price, i) => ({ time: i, price, size: sizes[i] }));
      const summary = await api.marketSummary({ prices, sizes });
      const nextForecast = await api.forecast({ prices, horizon: 10 });
      setMarket(summary);
      setForecast(nextForecast);
      setSymbolDrilldown(buildSeriesInsight(symbol, rows, summary, nextForecast, true));
      setSample(true);
      setQueryHealth({ status: "ok", message: "Sample mode active. Live query path not required for this preview." });
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setLoading(false);
    }
  }

  async function fetchMarketData(sym, options = {}) {
    const clear = Boolean(options.clear);
    const quiet = Boolean(options.quiet);
    if (clear) {
      setMarket(null);
      setForecast(null);
      setSymbolDrilldown(null);
    }
    const { res, sourceLabel } = await fetchTradeTape([sym], 750);
    setQueryHealth({ status: "ok", message: `Query path live via ${sourceLabel}.` });
    const cols = res.columns || [];
    const ti = cols.indexOf("time");
    const pi = cols.indexOf("price");
    const si = cols.indexOf("size");
    const rows = (res.rows || []).map((row) => ({
      time: ti >= 0 ? row[ti] : null,
      price: pi >= 0 ? row[pi] : null,
      size: si >= 0 ? row[si] : null,
    }));
    const prices = rows.map((row) => row.price).filter((value) => value != null);
    const sizes = si >= 0 ? rows.map((row) => row.size) : null;
    if (!prices.length) {
      if (!quiet) setError(`no trades for ${sym} on the cluster`);
      return;
    }
    const [summary, nextForecast] = await Promise.all([
      api.marketSummary({ prices, sizes }),
      api.forecast({ prices, horizon: 10 }),
    ]);
    setMarket(summary);
    setForecast(nextForecast);
    setSymbolDrilldown(buildSeriesInsight(sym, rows, summary, nextForecast));
  }

  const last = market?.last;
  const nextPoint = forecast?.points?.[forecast.points.length - 1] || null;
  const positionForSymbol = portfolio?.positions?.find((pos) => pos.symbol === symbol) || null;
  const projectedSymbolMove = nextPoint && last != null ? nextPoint.expected - last : null;
  const projectedSymbolMovePct = nextPoint && last != null ? ((nextPoint.expected - last) / last) * 100 : null;
  const projectedPositionPnl = positionForSymbol && projectedSymbolMove != null ? positionForSymbol.qty * projectedSymbolMove : null;

  return (
    <div className="page">
      <h2>Trading terminal <span className="paper-badge">PAPER</span></h2>
      <p className="muted">
        Choose one or more symbols to evaluate a live basket, compare correlation, inspect portfolio movement,
        and price the active name with a one-second cadence. Orders run in <strong>paper mode</strong> — no live market routing.
        <span className="live-cadence">Low-latency mode: refresh every second.</span>
        {perm.can_trade ? "" : " You don't have trading permission; ask an admin to grant it."}
      </p>

      {pressure?.elevated && (
        <div className="ops-banner warn">
          <div>
            <strong>Backpressure is active.</strong> {pressure.summary} Slow subscribers are being shed to keep the feed moving.
          </div>
          <button className="primary" onClick={() => onNavigate?.("metrics")}>
            Open Metrics →
          </button>
        </div>
      )}

      <div className="card">
        <div className="form-row">
          <div style={{ flex: 1, minWidth: "16rem" }}>
            <SymbolPicker value={syms} onChange={(value) => setSyms(value.slice(0, 6))} placeholder="build a basket…" />
          </div>
          <button className="primary" disabled={loading} onClick={loadMarket}>
            {loading ? "Loading…" : `Load ${symbol}${syms.length > 1 ? " + basket" : ""}`}
          </button>
          <button disabled={loading} onClick={loadSample} title="Preview the panels on synthetic data (no live cluster needed)">
            Sample data
          </button>
        </div>
        <div className="trading-toolbar-note">
          <span>Left-most symbol is the active drilldown and order ticket focus.</span>
          <span>{syms.length > 1 ? `${syms.length} names in the live basket.` : "Add more names to compare relative movement and correlation."}</span>
        </div>
        <div className={`desk-status ${queryHealth.status}`}>
          <strong>Query path</strong>
          <span>{queryHealth.message}</span>
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      {sample && <div className="sample-note">Showing <span className="sample-badge">SAMPLE</span> data — illustrative only, not live market figures.</div>}

      {market && (
        <div className="metric-cards">
          <Metric label="Last" value={fmt(market.last)} />
          <Metric label="Change" value={`${fmt(market.change)} (${fmt(market.change_pct)}%)`} cls={market.change >= 0 ? "up" : "down"} />
          <Metric label="High" value={fmt(market.high)} />
          <Metric label="Low" value={fmt(market.low)} />
          <Metric label="VWAP" value={fmt(market.vwap)} />
          <Metric label="Ann. vol" value={`${fmt(market.realized_vol_annualized * 100)}%`} />
        </div>
      )}

      {market && forecast && (
        <div className="metric-cards">
          <Metric label="Next expected" value={fmt(nextPoint?.expected)} cls={projectedSymbolMove >= 0 ? "up" : "down"} />
          <Metric label="Next range" value={`${fmt(nextPoint?.lower)} - ${fmt(nextPoint?.upper)}`} />
          <Metric label="Projected move" value={`${fmt(projectedSymbolMove)} (${fmt(projectedSymbolMovePct)}%)`} cls={projectedSymbolMove >= 0 ? "up" : "down"} />
          <Metric label="Signal" value={forecast.trend === "up" ? "momentum up" : forecast.trend === "down" ? "momentum down" : "flat"} />
        </div>
      )}

      {symbolDrilldown && (
        <div className="card">
          <div className="section-head">
            <h3>Symbol drilldown</h3>
            <span className={`signal-pill ${symbolDrilldown.signal}`}>{symbolDrilldown.signalText}</span>
          </div>
          <div className="drilldown-grid">
            <div>
              <svg viewBox="0 0 320 140" className="drilldown-svg">
                <defs>
                  <linearGradient id="drill-grad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.35" />
                    <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
                  </linearGradient>
                </defs>
                <polyline fill="none" stroke="var(--accent)" strokeWidth="2" points={symbolDrilldown.sparkline} />
                <polygon fill="url(#drill-grad)" points={symbolDrilldown.area} />
              </svg>
              <div className="drill-microgrid">
                {symbolDrilldown.heat.map((cell, idx) => (
                  <span key={idx} className={`heat-cell ${cell.cls}`} title={cell.title} />
                ))}
              </div>
            </div>
            <div className="drilldown-stats">
              <div className="drill-row"><span>Last print</span><b>{fmt(symbolDrilldown.last)}</b></div>
              <div className="drill-row"><span>Session change</span><b className={symbolDrilldown.change >= 0 ? "up" : "down"}>{fmt(symbolDrilldown.change)} ({fmt(symbolDrilldown.changePct)}%)</b></div>
              <div className="drill-row"><span>Range</span><b>{fmt(symbolDrilldown.low)} - {fmt(symbolDrilldown.high)}</b></div>
              <div className="drill-row"><span>VWAP</span><b>{fmt(symbolDrilldown.vwap)}</b></div>
              <div className="drill-row"><span>Forecast</span><b className={symbolDrilldown.forecastSignal}>{symbolDrilldown.forecastLabel}</b></div>
              <div className="drill-row"><span>Expected next</span><b>{fmt(symbolDrilldown.expectedNext)}</b></div>
            </div>
          </div>
        </div>
      )}

      <div className="trading-grid">
        {perm.can_trade && <OrderTicket symbol={symbol} last={last} onDone={refreshBlotter} onError={setError} />}
        <GreeksCard spot={last} onError={setError} />
      </div>

      {portfolio && (
        <div className="card">
          <h3>Portfolio impact</h3>
          <div className="metric-cards">
            <Metric label="Current P&L" value={fmt(portfolio.unrealized_pnl)} cls={portfolio.unrealized_pnl >= 0 ? "up" : "down"} />
            <Metric label="Projected symbol P&L" value={fmt(projectedPositionPnl)} cls={projectedPositionPnl >= 0 ? "up" : "down"} />
            <Metric label="Held qty" value={fmt(positionForSymbol?.qty)} />
            <Metric label="Projected move" value={fmt(projectedSymbolMove)} cls={projectedSymbolMove >= 0 ? "up" : "down"} />
          </div>
          {positionForSymbol ? (
            <p className="muted">This is the forecasted impact of {symbol} on the current portfolio if the expected move lands near the band midpoint.</p>
          ) : (
            <p className="muted">No position in {symbol} yet, but the forecast still shows the current market direction and expected range.</p>
          )}
        </div>
      )}

      {portfolioDrilldown.length > 0 && (
        <div className="card">
          <div className="section-head">
            <h3>Portfolio exposure forecast</h3>
            <span className="muted">multi-symbol view</span>
          </div>
          <table className="data-table portfolio-forecast-table">
            <thead>
              <tr><th>symbol</th><th>qty</th><th>weight</th><th>trend</th><th>next move</th><th>projected P&amp;L</th><th>last</th></tr>
            </thead>
            <tbody>
              {portfolioDrilldown.map((row) => (
                <tr key={row.symbol} className={row.symbol === symbol ? "is-selected" : ""} onClick={() => setSyms((prev) => [row.symbol, ...prev.filter((name) => name !== row.symbol)].slice(0, 6))}>
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

      {portfolioAnalytics && (
        <div className="card desk-card">
          <div className="section-head">
            <h3>Portfolio constellation</h3>
            <span className="muted">Auto-refreshing basket analytics</span>
          </div>
          <div className="metric-cards">
            <Metric label="Names tracked" value={fmt(portfolioAnalytics.members.length, 0)} />
            <Metric label="Avg correlation" value={fmt(portfolioAnalytics.averageCorrelation, 2)} cls={portfolioAnalytics.averageCorrelation >= 0.65 ? "warn" : ""} />
            <Metric label="Projected basket P&L" value={fmt(portfolioAnalytics.projectedPnl)} cls={portfolioAnalytics.projectedPnl >= 0 ? "up" : "down"} />
            <Metric label="Breadth" value={`${portfolioAnalytics.advancers}/${portfolioAnalytics.members.length}`} cls={portfolioAnalytics.advancers >= Math.ceil(portfolioAnalytics.members.length / 2) ? "up" : "down"} />
            <Metric label="Tightest pair" value={portfolioAnalytics.strongestPair?.label || "—"} />
            <Metric label="Most inverse" value={portfolioAnalytics.weakestPair?.label || "—"} cls={portfolioAnalytics.weakestPair && portfolioAnalytics.weakestPair.value < 0 ? "down" : ""} />
          </div>

          <div className="basket-layout">
            <div className="basket-panel basket-chart-panel">
              <div className="basket-panel-head">
                <h4>Normalized multi-instrument tape</h4>
                <span>Base 100, recent prints</span>
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={portfolioAnalytics.chartSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="t" tick={{ fontSize: 11 }} minTickGap={20} />
                  <YAxis tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
                  <Tooltip />
                  {portfolioAnalytics.members.map((member, idx) => (
                    <Line
                      key={member.symbol}
                      type="monotone"
                      dataKey={member.symbol}
                      stroke={paletteColor(idx)}
                      dot={false}
                      strokeWidth={member.symbol === symbol ? 2.8 : 1.8}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div className="basket-panel">
              <div className="basket-panel-head">
                <h4>Correlation matrix</h4>
                <span>Rolling return overlap</span>
              </div>
              <CorrelationMatrix matrix={portfolioAnalytics.correlationMatrix} />
            </div>
          </div>

          <div className="basket-panel" style={{ marginTop: "1rem" }}>
            <div className="basket-panel-head">
              <h4>Basket movement table</h4>
              <span>Selected symbols and largest held positions</span>
            </div>
            <table className="data-table basket-table">
              <thead>
                <tr><th>symbol</th><th>qty</th><th>last</th><th>move</th><th>forecast</th><th>corr avg</th><th>proj. P&amp;L</th></tr>
              </thead>
              <tbody>
                {portfolioAnalytics.members.map((row) => (
                  <tr key={row.symbol} className={row.symbol === symbol ? "is-selected" : ""} onClick={() => setSyms((prev) => [row.symbol, ...prev.filter((name) => name !== row.symbol)].slice(0, 6))}>
                    <td>{row.symbol}</td>
                    <td>{fmt(row.qty)}</td>
                    <td>{fmt(row.last)}</td>
                    <td className={row.changePct >= 0 ? "up" : "down"}>{fmt(row.changePct)}%</td>
                    <td><span className={`signal-pill ${row.signal}`}>{row.signalText}</span></td>
                    <td>{fmt(row.correlationAverage, 2)}</td>
                    <td className={row.projectedPnl >= 0 ? "up" : "down"}>{fmt(row.projectedPnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {forecast && <ForecastCard forecast={forecast} />}

      <PortfolioCard portfolio={portfolio} />
      <OrdersBlotter orders={orders} />
    </div>
  );
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

function Metric({ label, value, cls }) {
  return <div className="metric-card"><div className="metric-label">{label}</div><div className={`metric-value ${cls || ""}`}>{value}</div></div>;
}

function OrderTicket({ symbol, last, onDone, onError }) {
  const [side, setSide] = useState("buy");
  const [qty, setQty] = useState(100);
  const [type, setType] = useState("market");
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function submit() {
    setBusy(true);
    setMsg("");
    try {
      const order = await api.placeOrder({
        symbol,
        side,
        qty: Number(qty),
        order_type: type,
        limit_price: type === "limit" ? Number(limit) : null,
        ref_price: last != null ? Number(last) : null,
      });
      setMsg(`Filled (paper) ${order.side} ${order.qty} ${order.symbol} @ ${order.fill_price}`);
      onDone();
    } catch (err) {
      onError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card order-ticket">
      <h3>Order ticket <span className="paper-badge">PAPER</span></h3>
      <div className="ticket-row">
        <div className="side-toggle">
          <button className={side === "buy" ? "buy active" : "buy"} onClick={() => setSide("buy")}>Buy</button>
          <button className={side === "sell" ? "sell active" : "sell"} onClick={() => setSide("sell")}>Sell</button>
        </div>
        <span className="muted">{symbol}{last != null ? ` @ ~${fmt(last)}` : ""}</span>
      </div>
      <div className="ticket-row">
        <label>Qty <input type="number" value={qty} onChange={(e) => setQty(e.target.value)} /></label>
        <label>Type
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="market">market</option><option value="limit">limit</option>
          </select>
        </label>
        {type === "limit" && <label>Limit <input type="number" value={limit} onChange={(e) => setLimit(e.target.value)} /></label>}
      </div>
      <button className={`primary submit-order ${side}`} disabled={busy || (type === "market" && last == null)} onClick={submit}>
        {busy ? "Placing…" : `${side === "buy" ? "Buy" : "Sell"} ${qty} ${symbol}`}
      </button>
      {type === "market" && last == null && <div className="muted">Load market data first for a reference price.</div>}
      {msg && <div className="fill-msg">{msg}</div>}
    </div>
  );
}

function GreeksCard({ spot, onError }) {
  const [form, setForm] = useState({ strike: 100, days: 30, vol: 0.25, rate: 0.04, kind: "call" });
  const [greeks, setGreeks] = useState(null);
  const setField = (key, value) => setForm((state) => ({ ...state, [key]: value }));

  async function calc() {
    try {
      setGreeks(await api.computeGreeks({
        spot: Number(spot || form.strike),
        strike: Number(form.strike),
        t_years: Number(form.days) / 365,
        vol: Number(form.vol),
        rate: Number(form.rate),
        kind: form.kind,
      }));
    } catch (err) {
      onError(String(err).replace(/^Error:\s*/, ""));
    }
  }

  return (
    <div className="card">
      <h3>Option greeks</h3>
      <div className="ticket-row wrap">
        <label>Spot <input value={spot != null ? fmt(spot) : ""} disabled placeholder="load market" /></label>
        <label>Strike <input type="number" value={form.strike} onChange={(e) => setField("strike", e.target.value)} /></label>
        <label>Days <input type="number" value={form.days} onChange={(e) => setField("days", e.target.value)} /></label>
        <label>Vol <input type="number" step="0.01" value={form.vol} onChange={(e) => setField("vol", e.target.value)} /></label>
        <label>Rate <input type="number" step="0.01" value={form.rate} onChange={(e) => setField("rate", e.target.value)} /></label>
        <label>Type <select value={form.kind} onChange={(e) => setField("kind", e.target.value)}><option>call</option><option>put</option></select></label>
        <button onClick={calc} disabled={spot == null}>Compute</button>
      </div>
      {greeks && (
        <div className="greeks-grid">
          <GreekMetric label="Price" value={greeks.price} />
          <GreekMetric label="Delta" value={greeks.delta} />
          <GreekMetric label="Gamma" value={greeks.gamma} />
          <GreekMetric label="Vega (1%)" value={greeks.vega_per_pct} />
          <GreekMetric label="Theta (day)" value={greeks.theta_per_day} />
          <GreekMetric label="Rho (1%)" value={greeks.rho_per_pct} />
        </div>
      )}
    </div>
  );
}

function GreekMetric({ label, value }) {
  return <div className="greek"><span>{label}</span><b>{fmt(value, 4)}</b></div>;
}

function ForecastCard({ forecast }) {
  const pts = forecast.points || [];
  const all = pts.flatMap((point) => [point.lower, point.upper]);
  const min = Math.min(...all);
  const max = Math.max(...all);
  const rng = max - min || 1;
  const y = (value) => 90 - ((value - min) / rng) * 80;
  const x = (i) => 10 + (i / Math.max(1, pts.length - 1)) * 280;
  return (
    <div className="card">
      <h3>Forecast <span className="muted">({forecast.trend}, {forecast.method})</span></h3>
      <div className="forecast-disclaimer">⚠ {forecast.disclaimer}</div>
      {pts.length > 0 && (
        <svg viewBox="0 0 300 100" className="forecast-svg">
          <polyline fill="none" stroke="var(--muted)" strokeDasharray="3 3" strokeWidth="1" points={pts.map((point, i) => `${x(i)},${y(point.upper)}`).join(" ")} />
          <polyline fill="none" stroke="var(--muted)" strokeDasharray="3 3" strokeWidth="1" points={pts.map((point, i) => `${x(i)},${y(point.lower)}`).join(" ")} />
          <polyline fill="none" stroke="var(--accent)" strokeWidth="2" points={pts.map((point, i) => `${x(i)},${y(point.expected)}`).join(" ")} />
        </svg>
      )}
    </div>
  );
}

function buildSeriesInsight(symbol, rows, market, forecast, sample = false) {
  const prices = rows.map((row) => Number(row.price)).filter((value) => Number.isFinite(value));
  const series = prices.length ? prices : [market?.last || 0];
  const base = series[0] || 1;
  const max = Math.max(...series);
  const min = Math.min(...series);
  const sparkline = sparkPoints(series, 320, 140);
  const area = sparkArea(series, 320, 140);
  const returns = series.length > 1 ? series.slice(1).map((value, idx) => (value - series[idx]) / series[idx]) : [0];
  const heat = returns.slice(-18).map((value, idx) => ({
    cls: value > 0.003 ? "up" : value < -0.003 ? "down" : "flat",
    title: `${symbol} step ${idx + 1}: ${(value * 100).toFixed(2)}%`,
  }));
  const next = forecast?.points?.[forecast.points.length - 1] || null;
  const signal = forecast?.trend === "up" ? "up" : forecast?.trend === "down" ? "down" : "flat";
  const signalText = forecast?.trend === "up" ? "momentum up" : forecast?.trend === "down" ? "momentum down" : "flat";
  return {
    symbol,
    last: market?.last ?? series[series.length - 1],
    change: market?.change ?? (series[series.length - 1] - base),
    changePct: market?.change_pct ?? (((series[series.length - 1] - base) / base) * 100),
    high: market?.high ?? max,
    low: market?.low ?? min,
    vwap: market?.vwap ?? null,
    expectedNext: next?.expected ?? null,
    forecastSignal: signal,
    forecastLabel: next ? `step ${next.step}: ${fmt(next.expected)}` : sample ? "sample only" : "waiting",
    signal,
    signalText,
    sparkline,
    area,
    heat,
  };
}

async function fetchTradeTape(symbols, limit = 1200) {
  const uniqueSymbols = [...new Set((symbols || []).filter(Boolean).map((sym) => sym.toUpperCase()))];
  const targetMeta = await api.queryTargets();
  const rdbTargets = (targetMeta?.targets || []).map((target) => target.id).filter((id) => id.startsWith("rdb-"));
  const targets = rdbTargets.length ? rdbTargets : ["gateway"];
  const sourceLabel = rdbTargets.length ? `${rdbTargets.length} RDB shards` : "gateway";
  const query = uniqueSymbols.length === 1
    ? `select time, price, size from trade where sym=\`${uniqueSymbols[0]}`
    : `select time, sym, price, size from trade where sym in ${uniqueSymbols.map((sym) => `\`${sym}`).join("")}`;
  const res = await api.runQuery({ targets, query, limit });
  return { res, sourceLabel };
}

async function loadPortfolioDrilldown(positions) {
  const held = [...positions]
    .filter((pos) => Number(pos.qty) !== 0)
    .sort((a, b) => Math.abs(Number(b.market_value || 0)) - Math.abs(Number(a.market_value || 0)))
    .slice(0, 6);
  if (!held.length) return [];

  try {
    const { res } = await fetchTradeTape(held.map((pos) => pos.symbol), 1200);
    const cols = res.columns || [];
    const ti = cols.indexOf("time");
    const si = cols.indexOf("sym");
    const pi = cols.indexOf("price");
    const zi = cols.indexOf("size");
    const grouped = new Map();
    for (const row of res.rows || []) {
      const sym = si >= 0 ? row[si] : held[0]?.symbol;
      if (!sym) continue;
      const bucket = grouped.get(sym) || [];
      bucket.push({ time: ti >= 0 ? row[ti] : null, price: pi >= 0 ? row[pi] : null, size: zi >= 0 ? row[zi] : null });
      grouped.set(sym, bucket);
    }
    return await Promise.all(held.map(async (pos) => {
      const rows = grouped.get(pos.symbol) || [];
      const prices = rows.map((row) => row.price).filter((value) => value != null);
      const sizes = rows.map((row) => row.size).filter((value) => value != null);
      const summary = prices.length ? await api.marketSummary({ prices, sizes }) : null;
      const nextForecast = prices.length ? await api.forecast({ prices, horizon: 10 }) : null;
      const next = nextForecast?.points?.[nextForecast.points.length - 1] || null;
      const projectedMove = next && summary?.last != null ? next.expected - summary.last : 0;
      const projectedPnl = projectedMove * Number(pos.qty || 0);
      const signal = nextForecast?.trend === "up" ? "up" : nextForecast?.trend === "down" ? "down" : "flat";
      const signalText = nextForecast?.trend === "up" ? "momentum up" : nextForecast?.trend === "down" ? "momentum down" : rows.length ? "flat" : "no tape";
      return {
        symbol: pos.symbol,
        qty: pos.qty,
        weight_pct: pos.weight_pct,
        last: summary?.last ?? pos.last,
        projectedMove,
        projectedMovePct: summary?.last ? (projectedMove / summary.last) * 100 : 0,
        projectedPnl,
        signal,
        signalText,
      };
    }));
  } catch (err) {
    return held.map((pos) => ({
      symbol: pos.symbol,
      qty: pos.qty,
      weight_pct: pos.weight_pct,
      last: pos.last,
      projectedMove: 0,
      projectedMovePct: 0,
      projectedPnl: 0,
      signal: "flat",
      signalText: "no tape",
    }));
  }
}

async function loadPortfolioAnalytics(positions, selectedSymbols = []) {
  const positionMap = new Map((positions || []).map((pos) => [pos.symbol, pos]));
  const held = [...(positions || [])]
    .filter((pos) => Number(pos.qty) !== 0)
    .sort((a, b) => Math.abs(Number(b.market_value || 0)) - Math.abs(Number(a.market_value || 0)))
    .map((pos) => pos.symbol);
  const basket = [...new Set([...(selectedSymbols || []), ...held])].slice(0, 6);
  if (!basket.length) return null;

  try {
    const { res } = await fetchTradeTape(basket, 2400);
    const cols = res.columns || [];
    const ti = cols.indexOf("time");
    const si = cols.indexOf("sym");
    const pi = cols.indexOf("price");
    const zi = cols.indexOf("size");
    const grouped = new Map();
    for (const row of res.rows || []) {
      const sym = si >= 0 ? row[si] : basket[0];
      if (!sym) continue;
      const bucket = grouped.get(sym) || [];
      bucket.push({
        time: ti >= 0 ? row[ti] : null,
        price: pi >= 0 ? Number(row[pi]) : null,
        size: zi >= 0 ? Number(row[zi]) : null,
      });
      grouped.set(sym, bucket);
    }

    const members = await Promise.all(basket.map(async (sym) => {
      const rows = (grouped.get(sym) || []).filter((row) => Number.isFinite(row.price));
      rows.sort((left, right) => String(left.time || "").localeCompare(String(right.time || "")));
      const prices = rows.map((row) => row.price);
      const sizes = rows.map((row) => row.size).filter((value) => Number.isFinite(value));
      const summary = prices.length ? await api.marketSummary({ prices, sizes }) : null;
      const nextForecast = prices.length ? await api.forecast({ prices, horizon: 10 }) : null;
      const next = nextForecast?.points?.[nextForecast.points.length - 1] || null;
      const pos = positionMap.get(sym) || {};
      const projectedMove = next && summary?.last != null ? next.expected - summary.last : 0;
      return {
        symbol: sym,
        qty: Number(pos.qty || 0),
        last: summary?.last ?? prices[prices.length - 1] ?? null,
        changePct: summary?.change_pct ?? changePct(prices),
        signal: nextForecast?.trend === "up" ? "up" : nextForecast?.trend === "down" ? "down" : "flat",
        signalText: nextForecast?.trend === "up" ? "momentum up" : nextForecast?.trend === "down" ? "momentum down" : rows.length ? "flat" : "no tape",
        projectedPnl: projectedMove * Number(pos.qty || 0),
        correlationAverage: 0,
        indexSeries: normalizeSeries(prices),
        returns: seriesReturns(prices),
      };
    }));

    const correlationMatrix = buildCorrelationMatrix(members);
    const enrichedMembers = members.map((member) => {
      const matrixRow = correlationMatrix.find((row) => row.symbol === member.symbol);
      const peers = (matrixRow?.values || []).filter((cell) => cell.symbol !== member.symbol && Number.isFinite(cell.value));
      const correlationAverage = peers.length ? peers.reduce((sum, cell) => sum + cell.value, 0) / peers.length : 0;
      return { ...member, correlationAverage };
    });
    const pairs = correlationPairs(correlationMatrix);
    return {
      members: enrichedMembers,
      chartSeries: buildBasketChartSeries(enrichedMembers),
      correlationMatrix,
      strongestPair: pairs[0] || null,
      weakestPair: pairs[pairs.length - 1] || null,
      averageCorrelation: pairs.length ? pairs.reduce((sum, pair) => sum + pair.value, 0) / pairs.length : 0,
      projectedPnl: enrichedMembers.reduce((sum, member) => sum + Number(member.projectedPnl || 0), 0),
      advancers: enrichedMembers.filter((member) => Number(member.changePct || 0) >= 0).length,
    };
  } catch (err) {
    return null;
  }
}

function summarizePressure(snapshot) {
  const rows = (snapshot?.componentMetrics || []).filter((row) => {
    const queue = Number(row.tpQueue || 0);
    const lag = Number(row.tpSubLag || 0);
    return queue > 0 || lag > 0 || row.rdbConnected === false || row.wdbConnected === false;
  });
  if (!rows.length) return { elevated: false, rows: [], summary: "No active load shedding or subscriber pressure." };
  const labels = rows.map((row) => row.shard).join(", ");
  const maxQueue = Math.max(...rows.map((row) => Number(row.tpQueue || 0)));
  const maxLag = Math.max(...rows.map((row) => Number(row.tpSubLag || 0)));
  return {
    elevated: true,
    rows,
    summary: `${labels} under pressure: queue depth ${fmt(maxQueue, 0)}, subscriber lag ${fmt(maxLag, 0)}.`,
  };
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

function buildCorrelationMatrix(members) {
  return members.map((left) => ({
    symbol: left.symbol,
    values: members.map((right) => ({ symbol: right.symbol, value: correlation(left.returns, right.returns) })),
  }));
}

function correlationPairs(matrix) {
  const pairs = [];
  for (let i = 0; i < matrix.length; i += 1) {
    for (let j = i + 1; j < matrix.length; j += 1) {
      const value = matrix[i].values[j]?.value;
      if (!Number.isFinite(value)) continue;
      pairs.push({ label: `${matrix[i].symbol}/${matrix[j].symbol}`, value });
    }
  }
  return pairs.sort((left, right) => right.value - left.value);
}

function normalizeSeries(prices) {
  if (!prices.length) return [];
  const base = prices[0] || 1;
  return prices.map((price) => (price / base) * 100);
}

function seriesReturns(prices) {
  if (prices.length < 2) return [];
  return prices.slice(1).map((price, idx) => {
    const prev = prices[idx] || 1;
    return prev ? (price - prev) / prev : 0;
  });
}

function correlation(left, right) {
  const size = Math.min(left.length, right.length, 24);
  if (size < 2) return 0;
  const xs = left.slice(-size);
  const ys = right.slice(-size);
  const xMean = xs.reduce((sum, value) => sum + value, 0) / size;
  const yMean = ys.reduce((sum, value) => sum + value, 0) / size;
  let cov = 0;
  let xVar = 0;
  let yVar = 0;
  for (let idx = 0; idx < size; idx += 1) {
    const dx = xs[idx] - xMean;
    const dy = ys[idx] - yMean;
    cov += dx * dy;
    xVar += dx * dx;
    yVar += dy * dy;
  }
  if (!xVar || !yVar) return 0;
  return cov / Math.sqrt(xVar * yVar);
}

function corrClass(value) {
  if (value >= 0.7) return "corr-strong";
  if (value >= 0.25) return "corr-mid";
  if (value <= -0.35) return "corr-inverse";
  return "corr-flat";
}

function changePct(prices) {
  if (prices.length < 2) return 0;
  const first = prices[0] || 1;
  const last = prices[prices.length - 1];
  return first ? ((last - first) / first) * 100 : 0;
}

function explainQueryFailure(err) {
  const message = String(err).replace(/^Error:\s*/, "");
  if (/unreachable/i.test(message) || /502/.test(message)) {
    return "Gateway or shard RDB path is unreachable. Check Metrics for transport lag or broken downstream components.";
  }
  return message;
}

function paletteColor(index) {
  const colors = ["#4f46e5", "#0f766e", "#dc2626", "#d97706", "#2563eb", "#7c3aed"];
  return colors[index % colors.length];
}

function sparkPoints(values, width, height) {
  if (!values || values.length === 0) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return values.map((value, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = height - ((value - min) / range) * (height - 18) - 9;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

function sparkArea(values, width, height) {
  if (!values || values.length === 0) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values.map((value, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = height - ((value - min) / range) * (height - 18) - 9;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  return `0,${height} ${points.join(" ")} ${width},${height}`;
}

function PortfolioCard({ portfolio }) {
  if (!portfolio || !portfolio.positions?.length) return null;
  return (
    <div className="card">
      <h3>Portfolio</h3>
      <div className="metric-cards">
        <Metric label="Net exposure" value={fmt(portfolio.net_exposure)} />
        <Metric label="Gross" value={fmt(portfolio.gross_exposure)} />
        <Metric label="Unreal. P&L" value={fmt(portfolio.unrealized_pnl)} cls={portfolio.unrealized_pnl >= 0 ? "up" : "down"} />
        <Metric label="Realized" value={fmt(portfolio.realized_pnl)} cls={portfolio.realized_pnl >= 0 ? "up" : "down"} />
        <Metric label="Concentration" value={`${fmt(portfolio.concentration_pct)}%`} />
      </div>
      <table className="data-table" style={{ marginTop: "0.5rem" }}>
        <thead><tr><th>symbol</th><th>qty</th><th>avg</th><th>last</th><th>mkt val</th><th>P&L</th></tr></thead>
        <tbody>
          {portfolio.positions.map((pos) => (
            <tr key={pos.symbol}>
              <td>{pos.symbol}</td><td>{fmt(pos.qty)}</td><td>{fmt(pos.avg_price)}</td><td>{fmt(pos.last)}</td>
              <td>{fmt(pos.market_value)}</td><td className={pos.unrealized_pnl >= 0 ? "up" : "down"}>{fmt(pos.unrealized_pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OrdersBlotter({ orders }) {
  if (!orders?.length) return null;
  return (
    <div className="card">
      <h3>Orders</h3>
      <table className="data-table">
        <thead><tr><th>symbol</th><th>side</th><th>qty</th><th>type</th><th>status</th><th>route</th><th>fill</th></tr></thead>
        <tbody>
          {orders.slice(0, 25).map((order) => (
            <tr key={order.id}>
              <td>{order.symbol}</td><td className={order.side === "buy" ? "up" : "down"}>{order.side}</td>
              <td>{fmt(order.qty)}</td><td>{order.order_type}</td><td>{order.status}</td>
              <td><span className="paper-badge sm">{order.route}</span></td><td>{fmt(order.fill_price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmt(value, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}
