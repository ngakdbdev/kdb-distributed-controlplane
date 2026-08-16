import { useEffect, useState } from "react";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";
import { fetchTradeTape, fmt, groupBySymbol } from "../lib/tradingCore.js";
import { loadWatchlist, saveWatchlist } from "../lib/watchlist.js";

const REFRESH_MS = 1000;
const DEFAULT_BASKET = ["AAPL", "MSFT"];

// The actual "place a trade" page - a symbol basket (shared with Markets/
// Portfolio via lib/watchlist.js, same SymbolPicker component - add a
// symbol here and it shows up there too, not a page-local text field), an
// order ticket, the option-greeks calculator (a sizing tool that belongs
// next to order entry, not buried in the analysis page), and the live
// orders blotter. The ticket and greeks calculator both operate on
// whichever basket symbol is "focused" (clicked). Deep-linked from
// Markets' Buy/Sell buttons via `initial={{symbol, side}}` - App.jsx
// clears the params after the first render so a later in-page focus
// change doesn't get stomped by a stale deep-link.
export default function Orders({ initial, onNavigate }) {
  const [perm, setPerm] = useState({ can_trade: false, mode: "paper" });
  const [syms, setSyms] = useState(() => {
    const stored = loadWatchlist();
    if (initial?.symbol) return [...new Set([initial.symbol, ...stored])].slice(0, 12);
    return stored.length ? stored : DEFAULT_BASKET;
  });
  const [focus, setFocus] = useState(() => initial?.symbol || syms[0] || "AAPL");
  const [basketRows, setBasketRows] = useState({}); // { SYM: [{time, price, size}] }
  const [orders, setOrders] = useState([]);
  const [positions, setPositions] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => { api.tradingPermission().then(setPerm).catch(() => {}); }, []);
  useEffect(() => { saveWatchlist(syms); }, [syms]);
  useEffect(() => { if (!syms.includes(focus)) setFocus(syms[0] || ""); }, [syms, focus]);

  useEffect(() => {
    refreshBlotter();
    const pullPositions = () => api.getPositions().then((p) => setPositions((p?.positions || []).filter((pos) => Number(pos.qty) !== 0))).catch(() => {});
    pullPositions();
    const id = setInterval(refreshBlotter, REFRESH_MS);
    const posId = setInterval(pullPositions, 5000);
    return () => { clearInterval(id); clearInterval(posId); };
  }, []);

  // One batched query covers the whole basket - the same pattern Markets'
  // watchlist uses, so adding symbols here doesn't multiply query load.
  useEffect(() => {
    if (!syms.length) return;
    let closed = false;
    async function pull() {
      try {
        const { res } = await fetchTradeTape(syms, 50);
        if (!closed) setBasketRows(groupBySymbol(res, syms));
      } catch { /* best effort - order ticket just won't have a reference price yet */ }
    }
    pull();
    const id = setInterval(pull, REFRESH_MS);
    return () => { closed = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syms.join(",")]);

  async function refreshBlotter() {
    try { setOrders(await api.listOrders()); } catch { /* best effort */ }
  }

  function addSymbols(list) {
    setSyms((prev) => [...new Set([...prev, ...list])].slice(0, 12));
  }

  const focusRows = basketRows[focus] || [];
  const last = focusRows.length ? focusRows[focusRows.length - 1].price : null;
  const basket = syms.map((sym) => {
    const rows = basketRows[sym] || [];
    const price = rows.length ? rows[rows.length - 1].price : null;
    const first = rows.length ? rows[0].price : null;
    const changePct = first ? ((price - first) / first) * 100 : 0;
    return { symbol: sym, price, changePct, n: rows.length };
  });

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
    <div className="page orders-page">
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

      <div className="card orders-basket-card">
        <div className="form-row">
          <div style={{ flex: 1, minWidth: "16rem" }}>
            <SymbolPicker value={syms} onChange={(value) => setSyms(value.slice(0, 12))} placeholder="add a symbol to the basket…" />
          </div>
          <button className="link-btn" onClick={() => onNavigate?.("markets", { symbol: focus })}>Open in Markets →</button>
        </div>
        {positions.length > 0 && (
          <div style={{ marginTop: "0.6rem" }}>
            <span className="muted" style={{ fontSize: "0.8rem", marginRight: "0.5rem" }}>Your positions:</span>
            <span className="chip-list" style={{ display: "inline-flex" }}>
              {positions.filter((pos) => !syms.includes(pos.symbol)).map((pos) => (
                <button key={pos.symbol} className="chip" onClick={() => addSymbols([pos.symbol])}>+ {pos.symbol}</button>
              ))}
            </span>
          </div>
        )}
        {basket.length === 0 ? (
          <p className="muted" style={{ marginTop: "0.6rem" }}>Add a symbol above to build a basket - the ticket and greeks calculator below trade whichever one you click.</p>
        ) : (
          <div className="chip-list orders-basket-chips" style={{ marginTop: "0.6rem" }}>
            {basket.map((row) => (
              <button key={row.symbol} className={`chip ${row.symbol === focus ? "generate" : ""}`} onClick={() => setFocus(row.symbol)}>
                {row.symbol} {row.price != null ? fmt(row.price) : "…"}
                {row.n > 0 && (
                  <span className={row.changePct >= 0 ? "up" : "down"} style={{ marginLeft: "0.35rem" }}>
                    {row.changePct >= 0 ? "▲" : "▼"} {fmt(Math.abs(row.changePct), 2)}%
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="trading-grid orders-grid">
        {perm.can_trade && focus && (
          <OrderTicket symbol={focus} last={last} initialSide={initial?.side} onDone={refreshBlotter} onError={setError} />
        )}
        {focus && <GreeksCard key={focus} symbol={focus} spot={last} onError={setError} />}
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
    <div className="card order-ticket premium-ticket">
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

function GreeksCard({ symbol, spot, onError }) {
  const [form, setForm] = useState({ strike: 100, days: 30, vol: 0.25, rate: 0.04, kind: "call" });
  const [strikeTouched, setStrikeTouched] = useState(false);
  const [greeks, setGreeks] = useState(null);
  const [busy, setBusy] = useState(false);
  const setField = (key, value) => {
    if (key === "strike") setStrikeTouched(true);
    setForm((state) => ({ ...state, [key]: value }));
  };

  // Basket switching a "focus" symbol changes spot the moment you click a
  // different chip - snapping strike to the new at-the-money level (unless
  // you've deliberately picked your own strike) means the calculator
  // reflects the symbol you're actually looking at instead of a stale
  // strike left over from whichever one you priced last.
  useEffect(() => {
    if (spot != null && !strikeTouched) setForm((state) => ({ ...state, strike: Math.round(spot) }));
  }, [spot, strikeTouched]);

  useEffect(() => {
    if (spot == null) { setGreeks(null); return; }
    let closed = false;
    const id = setTimeout(async () => {
      setBusy(true);
      try {
        const result = await api.computeGreeks({
          spot: Number(spot), strike: Number(form.strike), t_years: Number(form.days) / 365,
          vol: Number(form.vol), rate: Number(form.rate), kind: form.kind,
        });
        if (!closed) setGreeks(result);
      } catch (err) {
        if (!closed) onError(String(err).replace(/^Error:\s*/, ""));
      } finally {
        if (!closed) setBusy(false);
      }
    }, 350); // debounced - recomputes as you type/switch focus, not on every keystroke
    return () => { closed = true; clearTimeout(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spot, form.strike, form.days, form.vol, form.rate, form.kind]);

  return (
    <div className="card orders-blotter-card">
      <h3>Option greeks {symbol && <span className="muted" style={{ fontWeight: 400 }}>{symbol}</span>}</h3>
      <div className="ticket-row wrap">
        <label>Spot <input value={spot != null ? fmt(spot) : ""} disabled placeholder="load market" /></label>
        <label>Strike <input type="number" value={form.strike} onChange={(e) => setField("strike", e.target.value)} /></label>
        <label>Days <input type="number" value={form.days} onChange={(e) => setField("days", e.target.value)} /></label>
        <label>Vol <input type="number" step="0.01" value={form.vol} onChange={(e) => setField("vol", e.target.value)} /></label>
        <label>Rate <input type="number" step="0.01" value={form.rate} onChange={(e) => setField("rate", e.target.value)} /></label>
        <label>Type <select value={form.kind} onChange={(e) => setField("kind", e.target.value)}><option>call</option><option>put</option></select></label>
        {busy && <span className="muted">computing…</span>}
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
      {spot == null && <p className="muted">Waiting for a reference price for {symbol || "this symbol"}…</p>}
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
