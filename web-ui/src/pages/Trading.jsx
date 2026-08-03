import { useEffect, useState } from "react";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";

export default function Trading() {
  const [perm, setPerm] = useState({ can_trade: false, mode: "paper" });
  const [syms, setSyms] = useState(["AAPL"]);
  const symbol = syms[0] || "AAPL";
  const [market, setMarket] = useState(null);
  const [forecast, setForecast] = useState(null);
  const [sample, setSample] = useState(false);
  const [orders, setOrders] = useState([]);
  const [portfolio, setPortfolio] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => { api.tradingPermission().then(setPerm).catch(() => {}); }, []);
  useEffect(() => { refreshBlotter(); }, []);

  async function refreshBlotter() {
    try {
      const os = await api.listOrders();
      setOrders(os);
      // value positions at last-known fill prices as a rough mark
      const marks = os.filter((o) => o.fill_price).map((o) => `${o.symbol}:${o.fill_price}`).join(",");
      setPortfolio(await api.getPositions(marks));
    } catch (err) { /* blotter is best-effort */ }
  }

  async function loadMarket() {
    setLoading(true); setError(""); setMarket(null); setForecast(null); setSample(false);
    try {
      // pull recent trades for the symbol from the cluster via the query API
      const res = await api.runQuery({
        targets: ["gateway"],
        query: `select price, size from trade where sym=\`${symbol}`,
        limit: 500,
      });
      const cols = res.columns || [];
      const pi = cols.indexOf("price"), si = cols.indexOf("size");
      const prices = (res.rows || []).map((r) => r[pi]).filter((v) => v != null);
      const sizes = si >= 0 ? (res.rows || []).map((r) => r[si]) : null;
      if (!prices.length) { setError(`no trades for ${symbol} on the cluster`); return; }
      setMarket(await api.marketSummary({ prices, sizes }));
      setForecast(await api.forecast({ prices, horizon: 10 }));
    } catch (err) {
      setError(`couldn't load market data (${String(err).replace(/^Error:\s*/, "")}). ` +
               `Load a live cluster with trades for ${symbol}, or use Sample data to preview the panels.`);
    } finally {
      setLoading(false);
    }
  }

  async function loadSample() {
    // synthetic random walk so the metrics/greeks/forecast panels are visible
    // without a live cluster. Clearly flagged as SAMPLE in the UI.
    setLoading(true); setError(""); setMarket(null); setForecast(null);
    const prices = [180];
    for (let i = 1; i < 80; i++) prices.push(Math.max(1, +(prices[i - 1] * (1 + (Math.random() - 0.48) * 0.02)).toFixed(2)));
    const sizes = prices.map(() => Math.round(100 + Math.random() * 900));
    try {
      setMarket(await api.marketSummary({ prices, sizes }));
      setForecast(await api.forecast({ prices, horizon: 10 }));
      setSample(true);
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setLoading(false);
    }
  }

  const last = market?.last;

  return (
    <div className="page">
      <h2>Trading terminal <span className="paper-badge">PAPER</span></h2>
      <p className="muted">
        Choose a symbol to see market movement, portfolio metrics, an illustrative forecast, and option
        greeks{perm.can_trade ? ", and place paper orders" : ""}. Orders run in <strong>paper mode</strong> —
        no live market routing. {perm.can_trade ? "" : "You don't have trading permission; ask an admin to grant it."}
      </p>

      <div className="card">
        <div className="form-row">
          <div style={{ flex: 1, minWidth: "16rem" }}>
            <SymbolPicker value={syms} onChange={(v) => setSyms(v.slice(-1))} placeholder="choose a symbol…" />
          </div>
          <button className="primary" disabled={loading} onClick={loadMarket}>
            {loading ? "Loading…" : `Load ${symbol}`}
          </button>
          <button disabled={loading} onClick={loadSample} title="Preview the panels on synthetic data (no live cluster needed)">
            Sample data
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      {sample && <div className="sample-note">Showing <span className="sample-badge">SAMPLE</span> data — illustrative only, not live market figures.</div>}
      {market && (
        <div className="metric-cards">
          <Metric label="Last" value={fmt(market.last)} />
          <Metric label="Change" value={`${fmt(market.change)} (${fmt(market.change_pct)}%)`}
                  cls={market.change >= 0 ? "up" : "down"} />
          <Metric label="High" value={fmt(market.high)} />
          <Metric label="Low" value={fmt(market.low)} />
          <Metric label="VWAP" value={fmt(market.vwap)} />
          <Metric label="Ann. vol" value={`${fmt(market.realized_vol_annualized * 100)}%`} />
        </div>
      )}

      <div className="trading-grid">
        {perm.can_trade && <OrderTicket symbol={symbol} last={last} onDone={refreshBlotter} onError={setError} />}
        <GreeksCard spot={last} onError={setError} />
      </div>

      {forecast && <ForecastCard forecast={forecast} />}

      <PortfolioCard portfolio={portfolio} />
      <OrdersBlotter orders={orders} />
    </div>
  );
}

function Metric({ label, value, cls }) {
  return <div className="metric-card"><div className="metric-label">{label}</div>
    <div className={`metric-value ${cls || ""}`}>{value}</div></div>;
}

function OrderTicket({ symbol, last, onDone, onError }) {
  const [side, setSide] = useState("buy");
  const [qty, setQty] = useState(100);
  const [type, setType] = useState("market");
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function submit() {
    setBusy(true); setMsg("");
    try {
      const o = await api.placeOrder({
        symbol, side, qty: Number(qty), order_type: type,
        limit_price: type === "limit" ? Number(limit) : null,
        ref_price: last != null ? Number(last) : null,
      });
      setMsg(`Filled (paper) ${o.side} ${o.qty} ${o.symbol} @ ${o.fill_price}`);
      onDone();
    } catch (err) { onError(String(err).replace(/^Error:\s*/, "")); }
    finally { setBusy(false); }
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
  const [f, setF] = useState({ strike: 100, days: 30, vol: 0.25, rate: 0.04, kind: "call" });
  const [g, setG] = useState(null);
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }));

  async function calc() {
    try {
      setG(await api.computeGreeks({
        spot: Number(spot || f.strike), strike: Number(f.strike),
        t_years: Number(f.days) / 365, vol: Number(f.vol), rate: Number(f.rate), kind: f.kind,
      }));
    } catch (err) { onError(String(err).replace(/^Error:\s*/, "")); }
  }

  return (
    <div className="card">
      <h3>Option greeks</h3>
      <div className="ticket-row wrap">
        <label>Spot <input value={spot != null ? fmt(spot) : ""} disabled placeholder="load market" /></label>
        <label>Strike <input type="number" value={f.strike} onChange={(e) => set("strike", e.target.value)} /></label>
        <label>Days <input type="number" value={f.days} onChange={(e) => set("days", e.target.value)} /></label>
        <label>Vol <input type="number" step="0.01" value={f.vol} onChange={(e) => set("vol", e.target.value)} /></label>
        <label>Rate <input type="number" step="0.01" value={f.rate} onChange={(e) => set("rate", e.target.value)} /></label>
        <label>Type <select value={f.kind} onChange={(e) => set("kind", e.target.value)}>
          <option>call</option><option>put</option></select></label>
        <button onClick={calc} disabled={spot == null}>Compute</button>
      </div>
      {g && (
        <div className="greeks-grid">
          <G l="Price" v={g.price} /><G l="Delta" v={g.delta} /><G l="Gamma" v={g.gamma} />
          <G l="Vega (1%)" v={g.vega_per_pct} /><G l="Theta (day)" v={g.theta_per_day} /><G l="Rho (1%)" v={g.rho_per_pct} />
        </div>
      )}
    </div>
  );
}
function G({ l, v }) { return <div className="greek"><span>{l}</span><b>{fmt(v, 4)}</b></div>; }

function ForecastCard({ forecast }) {
  const pts = forecast.points || [];
  const all = pts.flatMap((p) => [p.lower, p.upper]);
  const min = Math.min(...all), max = Math.max(...all), rng = max - min || 1;
  const y = (v) => 90 - ((v - min) / rng) * 80;
  const x = (i) => 10 + (i / Math.max(1, pts.length - 1)) * 280;
  return (
    <div className="card">
      <h3>Forecast <span className="muted">({forecast.trend}, {forecast.method})</span></h3>
      <div className="forecast-disclaimer">⚠ {forecast.disclaimer}</div>
      {pts.length > 0 && (
        <svg viewBox="0 0 300 100" className="forecast-svg">
          <polyline fill="none" stroke="var(--muted)" strokeDasharray="3 3" strokeWidth="1"
                    points={pts.map((p, i) => `${x(i)},${y(p.upper)}`).join(" ")} />
          <polyline fill="none" stroke="var(--muted)" strokeDasharray="3 3" strokeWidth="1"
                    points={pts.map((p, i) => `${x(i)},${y(p.lower)}`).join(" ")} />
          <polyline fill="none" stroke="var(--accent)" strokeWidth="2"
                    points={pts.map((p, i) => `${x(i)},${y(p.expected)}`).join(" ")} />
        </svg>
      )}
    </div>
  );
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
          {portfolio.positions.map((p) => (
            <tr key={p.symbol}>
              <td>{p.symbol}</td><td>{fmt(p.qty)}</td><td>{fmt(p.avg_price)}</td><td>{fmt(p.last)}</td>
              <td>{fmt(p.market_value)}</td>
              <td className={p.unrealized_pnl >= 0 ? "up" : "down"}>{fmt(p.unrealized_pnl)}</td>
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
          {orders.slice(0, 25).map((o) => (
            <tr key={o.id}>
              <td>{o.symbol}</td><td className={o.side === "buy" ? "up" : "down"}>{o.side}</td>
              <td>{fmt(o.qty)}</td><td>{o.order_type}</td><td>{o.status}</td>
              <td><span className="paper-badge sm">{o.route}</span></td><td>{fmt(o.fill_price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmt(v, dp = 2) {
  if (v == null || Number.isNaN(v)) return "—";
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: dp });
}
