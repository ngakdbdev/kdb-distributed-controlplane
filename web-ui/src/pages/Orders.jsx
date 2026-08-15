import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { fetchTradeTape, fmt } from "../lib/tradingCore.js";

const REFRESH_MS = 1000;

// The actual "place a trade" page - order ticket, the option-greeks
// calculator (a sizing tool that belongs next to order entry, not buried in
// the analysis page), and the live orders blotter. Deep-linked from Markets'
// Buy/Sell buttons via `initial={{symbol, side}}`; App.jsx clears the params
// after the first render so a later in-page symbol change doesn't get
// stomped by a stale deep-link.
export default function Orders({ initial, onNavigate }) {
  const [perm, setPerm] = useState({ can_trade: false, mode: "paper" });
  const [symbol, setSymbol] = useState(initial?.symbol || "AAPL");
  const [last, setLast] = useState(null);
  const [orders, setOrders] = useState([]);
  const [positions, setPositions] = useState([]);
  const [error, setError] = useState("");
  const priceRef = useRef(null);

  useEffect(() => { api.tradingPermission().then(setPerm).catch(() => {}); }, []);

  useEffect(() => {
    refreshBlotter();
    const pullPositions = () => api.getPositions().then((p) => setPositions((p?.positions || []).filter((pos) => Number(pos.qty) !== 0))).catch(() => {});
    pullPositions();
    const id = setInterval(refreshBlotter, REFRESH_MS);
    const posId = setInterval(pullPositions, 5000);
    return () => { clearInterval(id); clearInterval(posId); };
  }, []);

  useEffect(() => {
    let closed = false;
    async function pull() {
      try {
        const { res } = await fetchTradeTape([symbol], 50);
        const cols = res.columns || [];
        const pi = cols.indexOf("price");
        const rows = res.rows || [];
        const price = pi >= 0 && rows.length ? Number(rows[rows.length - 1][pi]) : null;
        if (!closed && Number.isFinite(price)) { setLast(price); priceRef.current = price; }
      } catch { /* best effort - order ticket just won't have a reference price yet */ }
    }
    pull();
    const id = setInterval(pull, REFRESH_MS);
    return () => { closed = true; clearInterval(id); };
  }, [symbol]);

  async function refreshBlotter() {
    try { setOrders(await api.listOrders()); } catch { /* best effort */ }
  }

  const modeLabel = {
    paper: "PAPER", "alpaca-paper": "ALPACA PAPER", "alpaca-live": "⚠ ALPACA LIVE — REAL MONEY",
    "ibkr-paper": "IBKR PAPER", "ibkr-live": "⚠ IBKR LIVE — REAL MONEY",
    misconfigured: "⚠ MISCONFIGURED",
  }[perm.mode] || "PAPER";
  const modeCopy = {
    paper: "Place and manage paper orders — simulated fills only, nothing leaves this box.",
    "alpaca-paper": "Orders route to a real Alpaca account's paper-trading simulation — real order mechanics against live prices, zero real money at risk.",
    "alpaca-live": "Orders route to a real Alpaca account with REAL MONEY. This is not a drill — every fill here is a real trade.",
    "ibkr-paper": "Orders route to a real Interactive Brokers account's paper-trading simulation via a Client Portal Gateway — zero real money at risk.",
    "ibkr-live": "Orders route to a real Interactive Brokers account with REAL MONEY. This is not a drill — every fill here is a real trade.",
    misconfigured: `Order routing is misconfigured: ${perm.error || "both Alpaca and IBKR appear to be configured at once"}. Orders are refused until this is fixed.`,
  }[perm.mode] || "Place and manage paper orders — simulated fills only, nothing leaves this box.";

  const loudBadge = perm.live_routing || perm.mode === "misconfigured";
  return (
    <div className="page">
      <h2>Orders <span className={`paper-badge${loudBadge ? " live" : ""}`}>{modeLabel}</span></h2>
      <p className="muted">
        {modeCopy} {perm.can_trade ? "" : "You don't have trading permission; ask an admin to grant it."}
      </p>
      {perm.live_routing && (
        <div className="ops-banner err">
          <div>Live order routing is active for this deployment ({perm.mode === "ibkr-live" ? "IBKR_TRADING_MODE" : "ALPACA_TRADING_MODE"}=live).
          Every order placed here trades real money in a real brokerage account.</div>
        </div>
      )}
      {perm.mode === "misconfigured" && (
        <div className="ops-banner err">
          <div>{perm.error}</div>
        </div>
      )}
      {error && <div className="error">{error}</div>}

      <div className="card">
        <div className="form-row">
          <label className="muted" style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
            Symbol
            <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} style={{ width: "8rem", textTransform: "uppercase" }} />
          </label>
          <span className="muted">{last != null ? `last ~${fmt(last)}` : "waiting for a reference price…"}</span>
          <button className="link-btn" onClick={() => onNavigate?.("markets", { symbol })}>Open in Markets →</button>
        </div>
        {positions.length > 0 && (
          <div style={{ marginTop: "0.6rem" }}>
            <span className="muted" style={{ fontSize: "0.8rem", marginRight: "0.5rem" }}>Your positions:</span>
            <span className="chip-list" style={{ display: "inline-flex" }}>
              {positions.map((pos) => (
                <button key={pos.symbol} className={`chip ${pos.symbol === symbol ? "generate" : ""}`} onClick={() => setSymbol(pos.symbol)}>{pos.symbol}</button>
              ))}
            </span>
          </div>
        )}
      </div>

      <div className="trading-grid">
        {perm.can_trade && (
          <OrderTicket symbol={symbol} last={last} initialSide={initial?.side} onDone={refreshBlotter} onError={setError} />
        )}
        <GreeksCard spot={last} onError={setError} />
      </div>

      <OrdersBlotter orders={orders} onCancel={refreshBlotter} />
    </div>
  );
}

function OrderTicket({ symbol, last, initialSide, onDone, onError }) {
  const [side, setSide] = useState(initialSide === "sell" ? "sell" : "buy");
  const [qty, setQty] = useState(100);
  const [type, setType] = useState("market");
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => { if (initialSide === "sell" || initialSide === "buy") setSide(initialSide); }, [initialSide, symbol]);

  async function submit() {
    setBusy(true);
    setMsg("");
    try {
      const order = await api.placeOrder({
        symbol, side, qty: Number(qty), order_type: type,
        limit_price: type === "limit" ? Number(limit) : null,
        ref_price: last != null ? Number(last) : null,
      });
      setMsg(order.status === "new"
        ? `Working (paper) ${order.side} ${order.qty} ${order.symbol} @ ${order.limit_price} — resting until the market crosses it`
        : `Filled (paper) ${order.side} ${order.qty} ${order.symbol} @ ${order.fill_price}`);
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
      {type === "market" && last == null && <div className="muted">Waiting for a reference price…</div>}
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
        spot: Number(spot || form.strike), strike: Number(form.strike), t_years: Number(form.days) / 365,
        vol: Number(form.vol), rate: Number(form.rate), kind: form.kind,
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

function OrdersBlotter({ orders, onCancel }) {
  const [busyId, setBusyId] = useState(null);
  if (!orders?.length) {
    return <div className="card"><p className="muted" style={{ margin: 0 }}>No orders yet — place one above.</p></div>;
  }

  async function cancel(id) {
    setBusyId(id);
    try { await api.cancelOrder(id); onCancel?.(); }
    catch { /* likely already filled/matched - next refresh shows the real status */ }
    finally { setBusyId(null); }
  }

  return (
    <div className="card">
      <h3>Orders</h3>
      <table className="data-table">
        <thead><tr><th>symbol</th><th>side</th><th>qty</th><th>type</th><th>status</th><th>route</th><th>fill</th><th></th></tr></thead>
        <tbody>
          {orders.slice(0, 25).map((order) => (
            <tr key={order.id}>
              <td>{order.symbol}</td><td className={order.side === "buy" ? "up" : "down"}>{order.side}</td>
              <td>{fmt(order.qty)}</td><td>{order.order_type}</td>
              <td><span className={`status-badge status-${order.status}`}>{order.status}</span></td>
              <td><span className="paper-badge sm">{order.route}</span></td>
              <td>{order.status === "new" ? `working @ ${fmt(order.limit_price)}` : fmt(order.fill_price)}</td>
              <td>{order.status === "new" && (
                <button className="chip" disabled={busyId === order.id} onClick={() => cancel(order.id)}>Cancel</button>
              )}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
