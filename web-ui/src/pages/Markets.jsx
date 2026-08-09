import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";
import Sparkline from "../components/Sparkline.jsx";
import ForecastPanel from "../components/ForecastPanel.jsx";
import { bucketOHLC, Candlestick, DepthPanel, LiveTape } from "../components/TradingVisuals.jsx";
import { buildTimeForecast } from "../lib/timeForecast.js";
import { fetchTradeTape, fmt, summarizePressure } from "../lib/tradingCore.js";
import { loadWatchlist, saveWatchlist } from "../lib/watchlist.js";

const REFRESH_MS = 1000;
const BASKET_REFRESH_MS = 3000; // whole-basket summary is cheaper to refresh less often than the focused deep-dive
const DEFAULT_BASKET = ["AAPL", "MSFT"];

// The "watch and analyze" page - every symbol in your basket gets a live
// watchlist row (price, change, trend), and one of them is the "focus" for
// the full deep-dive (candlestick, tape/depth, calendar-horizon forecast).
// The basket is a shared watchlist (lib/watchlist.js) with Portfolio: add a
// symbol here and it shows up there too, automatically, next poll.
export default function Markets({ onNavigate, initial }) {
  const liveRunRef = useRef(false);
  const [syms, setSyms] = useState(() => {
    const stored = loadWatchlist();
    if (initial?.symbol) return [...new Set([initial.symbol, ...stored])].slice(0, 12);
    return stored.length ? stored : DEFAULT_BASKET;
  });
  const [focus, setFocus] = useState(() => initial?.symbol || syms[0] || "AAPL");
  const [basketRows, setBasketRows] = useState({}); // { SYM: [{time, price, size}] }
  const [positions, setPositions] = useState([]);
  const [market, setMarket] = useState(null);
  const [sample, setSample] = useState(false);
  const [pressure, setPressure] = useState(null);
  const [queryHealth, setQueryHealth] = useState({ status: "idle", message: "Waiting for query path…" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => { saveWatchlist(syms); }, [syms]);
  useEffect(() => { if (!syms.includes(focus)) setFocus(syms[0] || ""); }, [syms, focus]);

  useEffect(() => {
    const pullPressure = () => api.metricsSnapshot().then((snap) => setPressure(summarizePressure(snap))).catch(() => {});
    const pullPositions = () => api.getPositions().then((p) => setPositions((p?.positions || []).filter((pos) => Number(pos.qty) !== 0))).catch(() => {});
    pullPressure();
    pullPositions();
    const id = setInterval(pullPressure, REFRESH_MS);
    const posId = setInterval(pullPositions, 5000);
    return () => { clearInterval(id); clearInterval(posId); };
  }, []);

  // Whole-basket watchlist refresh: one query covers every symbol.
  useEffect(() => {
    if (sample || !syms.length) return;
    let closed = false;
    async function pull() {
      try {
        const { res, sourceLabel } = await fetchTradeTape(syms, 400);
        if (closed) return;
        setQueryHealth({ status: "ok", message: `Query path live via ${sourceLabel}.` });
        setBasketRows(groupBySymbol(res, syms));
      } catch (err) {
        if (!closed) setQueryHealth({ status: "bad", message: explainQueryFailure(err) });
      }
    }
    pull();
    const id = setInterval(pull, BASKET_REFRESH_MS);
    return () => { closed = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syms.join(","), sample]);

  // Focused-symbol deep-dive: full market summary + live 1s refresh.
  useEffect(() => {
    if (sample || !focus) return;
    let closed = false;
    async function pullFocus(quiet) {
      try {
        const rows = basketRowsRef(focus);
        const prices = rows.map((r) => r.price).filter((v) => v != null);
        if (!prices.length) { if (!quiet) setMarket(null); return; }
        const sizes = rows.map((r) => r.size);
        const summary = await api.marketSummary({ prices, sizes });
        if (!closed) setMarket(summary);
      } catch { /* best effort, keep last good summary */ }
    }
    function basketRowsRef(sym) { return basketRows[sym] || []; }
    pullFocus(false);
    const id = setInterval(() => { if (!liveRunRef.current) { liveRunRef.current = true; pullFocus(true).finally(() => { liveRunRef.current = false; }); } }, REFRESH_MS);
    return () => { closed = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, sample, basketRows]);

  async function loadBasket() {
    setLoading(true);
    setError("");
    setSample(false);
    try {
      const { res, sourceLabel } = await fetchTradeTape(syms, 400);
      setQueryHealth({ status: "ok", message: `Query path live via ${sourceLabel}.` });
      const grouped = groupBySymbol(res, syms);
      setBasketRows(grouped);
      if (!grouped[focus]?.length) setError(`no trades for ${focus} on the cluster yet — other basket symbols may still have data below.`);
    } catch (err) {
      setQueryHealth({ status: "bad", message: explainQueryFailure(err) });
      setError(`couldn't load basket data (${String(err).replace(/^Error:\s*/, "")}). Use Sample data to preview the panels instead.`);
    } finally {
      setLoading(false);
    }
  }

  async function loadSample() {
    setLoading(true);
    setError("");
    const prices = [180];
    for (let i = 1; i < 80; i += 1) prices.push(Math.max(1, +(prices[i - 1] * (1 + (Math.random() - 0.48) * 0.02)).toFixed(2)));
    const sizes = prices.map(() => Math.round(100 + Math.random() * 900));
    const now = Date.now();
    try {
      const rows = prices.map((price, i) => ({ time: new Date(now - (prices.length - i) * 15000).toISOString(), price, size: sizes[i] }));
      const summary = await api.marketSummary({ prices, sizes });
      setMarket(summary);
      setBasketRows({ [focus]: rows });
      setSample(true);
      setQueryHealth({ status: "ok", message: "Sample mode active (single symbol). Live query path not required for this preview." });
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setLoading(false);
    }
  }

  function addSymbols(list) {
    setSyms((prev) => [...new Set([...prev, ...list])].slice(0, 12));
  }

  const focusRows = basketRows[focus] || [];
  const forecast = buildTimeForecast(focusRows);
  const watchlist = syms.map((sym) => buildWatchRow(sym, basketRows[sym] || []));

  return (
    <div className="page">
      <h2>Markets</h2>
      <p className="muted">
        Every symbol in your basket gets a live watchlist row below; click one to open its full
        analysis. The basket is shared with Portfolio — add a symbol here and it shows up there too.
        <span className="live-cadence">Low-latency mode: refresh every second.</span>
      </p>

      {pressure?.elevated && (
        <div className="ops-banner warn">
          <div><strong>Backpressure is active.</strong> {pressure.summary} Slow subscribers are being shed to keep the feed moving.</div>
          <button className="primary" onClick={() => onNavigate?.("metrics")}>Open Metrics →</button>
        </div>
      )}

      <div className="card">
        <div className="form-row">
          <div style={{ flex: 1, minWidth: "16rem" }}>
            <SymbolPicker value={syms} onChange={(value) => setSyms(value.slice(0, 12))} placeholder="add a symbol to the basket…" />
          </div>
          <button className="primary" disabled={loading || !syms.length} onClick={loadBasket}>
            {loading ? "Loading…" : `Load basket (${syms.length})`}
          </button>
          <button disabled={loading} onClick={loadSample} title="Preview the panels on synthetic single-symbol data (no live cluster needed)">Sample data</button>
        </div>
        <div className={`desk-status ${queryHealth.status}`}><strong>Query path</strong><span>{queryHealth.message}</span></div>
        {error && <div className="error">{error}</div>}
        {positions.length > 0 && (
          <div style={{ marginTop: "0.6rem" }}>
            <span className="muted" style={{ fontSize: "0.8rem", marginRight: "0.5rem" }}>Add your positions:</span>
            <span className="chip-list" style={{ display: "inline-flex" }}>
              {positions.filter((pos) => !syms.includes(pos.symbol)).map((pos) => (
                <button key={pos.symbol} className="chip" onClick={() => addSymbols([pos.symbol])}>+ {pos.symbol}</button>
              ))}
            </span>
          </div>
        )}
      </div>

      {sample && <div className="sample-note">Showing <span className="sample-badge">SAMPLE</span> data for {focus} — illustrative only, not live market figures.</div>}

      <div className="card" style={{ padding: "0.4rem 1rem" }}>
        <h3>Watchlist</h3>
        {watchlist.length === 0 ? (
          <p className="muted" style={{ padding: "0.5rem 0" }}>Add a symbol above to start watching it.</p>
        ) : (
          <div>
            {watchlist.map((row) => (
              <button key={row.symbol} className="list-row" style={{ width: "100%", background: "none", border: "none", cursor: "pointer", textAlign: "left" }}
                      onClick={() => setFocus(row.symbol)}>
                <span className="list-row-icon" style={row.symbol === focus ? { background: "var(--accent)", color: "#fff" } : undefined}>{row.symbol.slice(0, 2)}</span>
                <div className="list-row-main">
                  <div className="list-row-title">{row.symbol}</div>
                  <div className="list-row-sub">{row.n ? `${row.n} recent prints` : "no data yet"}</div>
                </div>
                <div className="list-row-spark"><Sparkline data={row.series} width={72} height={28} /></div>
                <div className="list-row-value">
                  <b>{row.last != null ? fmt(row.last) : "—"}</b>
                  <span className={row.changePct >= 0 ? "up" : "down"}>{row.n ? `${row.changePct >= 0 ? "▲" : "▼"} ${fmt(Math.abs(row.changePct), 2)}%` : ""}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {market && (
        <>
          <div className="hero-stat">
            <div className="hero-stat-main">
              <div className="hero-stat-label">{focus} · last print</div>
              <div className="hero-stat-value">{fmt(market.last)}</div>
              <span className={`hero-stat-delta ${market.change >= 0 ? "up" : "down"}`}>
                {market.change >= 0 ? "▲" : "▼"} {fmt(market.change)} ({fmt(market.change_pct)}%)
              </span>
            </div>
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <Sparkline data={focusRows.map((r) => Number(r.price)).filter((v) => !Number.isNaN(v))} width={140} height={44} strokeWidth={2.5} />
              <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                <button className="primary" style={{ background: "var(--success)" }} onClick={() => onNavigate?.("orders", { symbol: focus, side: "buy" })}>Buy {focus}</button>
                <button className="primary" style={{ background: "var(--danger)" }} onClick={() => onNavigate?.("orders", { symbol: focus, side: "sell" })}>Sell {focus}</button>
              </div>
            </div>
          </div>
          <div className="metric-cards">
            <Metric label="High" value={fmt(market.high)} />
            <Metric label="Low" value={fmt(market.low)} />
            <Metric label="VWAP" value={fmt(market.vwap)} />
            <Metric label="Ann. vol" value={`${fmt(market.realized_vol_annualized * 100)}%`} />
          </div>
        </>
      )}

      {focusRows.length > 0 && (
        <div className="card">
          <div className="section-head"><h3>Symbol drilldown</h3><span className="muted">{focus}</span></div>
          <div className="drilldown-grid">
            <Candlestick bars={bucketOHLC(focusRows, 5000)} height={220} />
            <div className="drilldown-stats">
              <div className="drill-row"><span>Last print</span><b>{fmt(market?.last)}</b></div>
              <div className="drill-row"><span>Session change</span><b className={market?.change >= 0 ? "up" : "down"}>{fmt(market?.change)} ({fmt(market?.change_pct)}%)</b></div>
              <div className="drill-row"><span>Range</span><b>{fmt(market?.low)} - {fmt(market?.high)}</b></div>
              <div className="drill-row"><span>VWAP</span><b>{fmt(market?.vwap)}</b></div>
            </div>
          </div>
        </div>
      )}

      <ForecastPanel forecast={forecast} symbol={focus} />

      {focusRows.length > 0 && (
        <div className="trading-grid">
          <div className="card">
            <h3>Tape <span className="muted" style={{ fontWeight: 400 }}>{focus}</span></h3>
            <LiveTape rows={focusRows} maxRows={25} />
          </div>
          <div className="card">
            <h3>Depth <span className="muted" style={{ fontWeight: 400 }}>{focus}</span></h3>
            <DepthPanel rows={focusRows} levels={5} />
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, cls }) {
  return <div className="metric-card"><div className="metric-label">{label}</div><div className={`metric-value ${cls || ""}`}>{value}</div></div>;
}

function groupBySymbol(res, fallbackSymbols) {
  const cols = res.columns || [];
  const ti = cols.indexOf("time"), si = cols.indexOf("sym"), pi = cols.indexOf("price"), zi = cols.indexOf("size");
  const out = {};
  for (const sym of fallbackSymbols) out[sym] = [];
  for (const row of res.rows || []) {
    const sym = si >= 0 ? row[si] : fallbackSymbols[0];
    if (!sym) continue;
    (out[sym] = out[sym] || []).push({ time: ti >= 0 ? row[ti] : null, price: pi >= 0 ? Number(row[pi]) : null, size: zi >= 0 ? Number(row[zi]) : null });
  }
  return out;
}

function buildWatchRow(symbol, rows) {
  const prices = rows.map((r) => Number(r.price)).filter((v) => Number.isFinite(v));
  const last = prices.length ? prices[prices.length - 1] : null;
  const first = prices.length ? prices[0] : null;
  const changePct = first ? ((last - first) / first) * 100 : 0;
  return { symbol, series: prices, last, changePct, n: prices.length };
}

function explainQueryFailure(err) {
  const message = String(err).replace(/^Error:\s*/, "");
  if (/unreachable/i.test(message) || /502/.test(message)) {
    return "Gateway or shard RDB path is unreachable. Check Metrics for transport lag or broken downstream components.";
  }
  return message;
}
