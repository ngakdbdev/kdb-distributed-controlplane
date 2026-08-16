import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";
import Sparkline from "../components/Sparkline.jsx";
import ForecastPanel from "../components/ForecastPanel.jsx";
import { bucketOHLC, Candlestick, DepthPanel, LiveTape, MacdPanel } from "../components/TradingVisuals.jsx";
import { buildTimeForecast } from "../lib/timeForecast.js";
import { fetchTradeTape, fmt, groupBySymbol, summarizePressure } from "../lib/tradingCore.js";
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
  const [indicators, setIndicators] = useState({ ema9: true, ema21: false, bb: false, macd: false });
  const [tab, setTab] = useState("overview");
  const [news, setNews] = useState([]);
  const [newsSources, setNewsSources] = useState({});

  useEffect(() => { saveWatchlist(syms); }, [syms]);
  useEffect(() => { if (!syms.includes(focus)) setFocus(syms[0] || ""); }, [syms, focus]);

  // News is minutes-scale by nature (Finnhub/Alpha Vantage, see
  // news_feed.py) - same 15s cadence as PredictiveSignals.jsx, not the
  // page's 1s live-trading refresh. Refetches whenever the focused symbol
  // changes so the News tab is always about the instrument you're looking at.
  useEffect(() => {
    if (!focus) return;
    let closed = false;
    const pull = () => api.newsFeed(focus, 20).then((r) => {
      if (closed) return;
      setNews(r.items || []);
      setNewsSources(r.sources || {});
    }).catch(() => {});
    pull();
    const id = setInterval(pull, 15000);
    return () => { closed = true; clearInterval(id); };
  }, [focus]);

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
    <div className="page markets-page">
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

      <div className="card market-watch-card" style={{ padding: "0.4rem 1rem" }}>
        <h3>Watchlist</h3>
        {watchlist.length === 0 ? (
          <p className="muted" style={{ padding: "0.5rem 0" }}>Add a symbol above to start watching it.</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table market-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Last</th>
                  <th>Change</th>
                  <th>Volume</th>
                  <th>Trend</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {watchlist.map((row) => (
                  <tr key={row.symbol} className={row.symbol === focus ? "is-selected" : ""} onClick={() => setFocus(row.symbol)}>
                    <td className="market-table-symbol">{row.symbol}</td>
                    <td className="tabular">{row.last != null ? fmt(row.last) : "—"}</td>
                    <td className={row.n ? (row.changePct >= 0 ? "up" : "down") : ""}>
                      {row.n ? `${row.changePct >= 0 ? "▲" : "▼"} ${fmt(Math.abs(row.changePct), 2)}%` : "—"}
                    </td>
                    <td className="tabular">{row.volume ? fmt(row.volume) : "—"}</td>
                    <td><Sparkline data={row.series} width={72} height={22} /></td>
                    <td><span className={`market-status-dot ${row.n ? "live" : "idle"}`} title={row.n ? `${row.n} recent prints` : "no data yet"} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
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
            <div className="market-hero-actions" style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <Sparkline data={focusRows.map((r) => Number(r.price)).filter((v) => !Number.isNaN(v))} width={140} height={44} strokeWidth={2.5} />
              <div className="market-trade-cta" style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                <button className="primary trade-cta buy" style={{ background: "var(--success)" }} onClick={() => onNavigate?.("orders", { symbol: focus, side: "buy" })}>Buy {focus}</button>
                <button className="primary trade-cta sell" style={{ background: "var(--danger)" }} onClick={() => onNavigate?.("orders", { symbol: focus, side: "sell" })}>Sell {focus}</button>
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

      {/* Instrument Workspace - one symbol, every real view of it, tabbed
          instead of stacked (chart/book/news/trades no longer compete for
          the same scroll position). Options/Fundamentals are honestly
          marked "soon" - there's no options-chain or fundamentals data
          source anywhere in this deployment (Orders' greeks card is a
          pricing calculator, not a chain), so those tabs say so instead
          of pretending. */}
      {focus && (
        <div className="card instrument-workspace">
          <div className="instrument-tabs" role="tablist" aria-label={`${focus} workspace`}>
            {WORKSPACE_TABS.map((t) => (
              <button key={t.id} role="tab" aria-selected={tab === t.id}
                      className={`instrument-tab ${tab === t.id ? "active" : ""}`}
                      onClick={() => setTab(t.id)}>
                {t.label}{t.soon && <span className="soon-pill">soon</span>}
              </button>
            ))}
          </div>

          <div className="instrument-tab-panel">
            {tab === "overview" && <ForecastPanel forecast={forecast} symbol={focus} />}

            {tab === "chart" && (
              focusRows.length === 0 ? <NoTabData symbol={focus} /> : (
                <>
                  <div className="indicator-row">
                    <button className={`chip ${indicators.ema9 ? "indicator-active" : ""}`}
                            onClick={() => setIndicators((i) => ({ ...i, ema9: !i.ema9 }))}>EMA 9</button>
                    <button className={`chip ${indicators.ema21 ? "indicator-active" : ""}`}
                            onClick={() => setIndicators((i) => ({ ...i, ema21: !i.ema21 }))}>EMA 21</button>
                    <button className={`chip ${indicators.bb ? "indicator-active" : ""}`}
                            onClick={() => setIndicators((i) => ({ ...i, bb: !i.bb }))}>BB 20 2</button>
                    <button className={`chip ${indicators.macd ? "indicator-active" : ""}`}
                            onClick={() => setIndicators((i) => ({ ...i, macd: !i.macd }))}>MACD 12 26 9</button>
                  </div>
                  <div className="drilldown-grid">
                    <div>
                      <Candlestick bars={bucketOHLC(focusRows, 5000)} height={220} showBollinger={indicators.bb}
                                  emaPeriods={[indicators.ema9 && 9, indicators.ema21 && 21].filter(Boolean)} />
                      {indicators.macd && (
                        <>
                          <div className="macd-label">MACD (12, 26, close, 9 EMA)</div>
                          <MacdPanel bars={bucketOHLC(focusRows, 5000)} height={90} />
                        </>
                      )}
                    </div>
                    <div className="drilldown-stats">
                      <div className="drill-row"><span>Last print</span><b>{fmt(market?.last)}</b></div>
                      <div className="drill-row"><span>Session change</span><b className={market?.change >= 0 ? "up" : "down"}>{fmt(market?.change)} ({fmt(market?.change_pct)}%)</b></div>
                      <div className="drill-row"><span>Range</span><b>{fmt(market?.low)} - {fmt(market?.high)}</b></div>
                      <div className="drill-row"><span>VWAP</span><b>{fmt(market?.vwap)}</b></div>
                    </div>
                  </div>
                </>
              )
            )}

            {tab === "orderbook" && (
              focusRows.length === 0 ? <NoTabData symbol={focus} /> : <DepthPanel rows={focusRows} levels={5} />
            )}

            {tab === "news" && <NewsTab news={news} sources={newsSources} symbol={focus} />}

            {tab === "trades" && (
              focusRows.length === 0 ? <NoTabData symbol={focus} /> : <LiveTape rows={focusRows} maxRows={25} />
            )}

            {tab === "options" && (
              <ComingSoon text={`No options-chain data source is configured for this deployment. There's a Black-Scholes greeks calculator (manual strike/vol/rate entry) on the Orders page you can use in the meantime.`}
                          action={{ label: "Open Orders →", onClick: () => onNavigate?.("orders", { symbol: focus }) }} />
            )}

            {tab === "fundamentals" && (
              <ComingSoon text="No fundamentals data source (P/E, market cap, EPS, ...) is configured for this deployment." />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const WORKSPACE_TABS = [
  { id: "overview", label: "Overview" },
  { id: "chart", label: "Chart" },
  { id: "orderbook", label: "Order Book" },
  { id: "news", label: "News" },
  { id: "trades", label: "Trades" },
  { id: "options", label: "Options", soon: true },
  { id: "fundamentals", label: "Fundamentals", soon: true },
];

function NoTabData({ symbol }) {
  return <p className="muted" style={{ padding: "0.5rem 0" }}>No trade prints for {symbol} on the cluster yet.</p>;
}

function ComingSoon({ text, action }) {
  return (
    <div className="coming-soon">
      <p className="muted" style={{ margin: 0 }}>{text}</p>
      {action && <button className="chip" onClick={action.onClick} style={{ marginTop: "0.6rem" }}>{action.label}</button>}
    </div>
  );
}

function NewsTab({ news, sources, symbol }) {
  const noSource = sources && !sources.finnhub && !sources.alphavantage;
  if (noSource) {
    return <ComingSoon text="No news source is configured for this deployment (set FINNHUB_API_KEY and/or ALPHAVANTAGE_API_KEY)." />;
  }
  if (!news.length) {
    return <p className="muted" style={{ padding: "0.5rem 0" }}>No recent news for {symbol}.</p>;
  }
  return (
    <div className="news-feed">
      {news.map((a, i) => (
        <a className="news-item" key={i} href={a.url || "#"} target="_blank" rel="noreferrer">
          <div className="news-item-head">
            <span className={`news-sentiment-pill ${sentimentTone(a.sentiment_score)}`}>
              {a.sentiment_score > 0 ? "▲" : a.sentiment_score < 0 ? "▼" : "•"} {fmt(a.sentiment_score, 2)}
            </span>
            <span className="news-region">{a.region}</span>
          </div>
          <div className="news-headline">{a.headline}</div>
          <div className="muted news-source">{a.source} · {(a.symbols || []).join(", ")}</div>
        </a>
      ))}
    </div>
  );
}

function sentimentTone(score) {
  return score > 0.15 ? "up" : score < -0.15 ? "down" : "";
}

function Metric({ label, value, cls }) {
  return <div className="metric-card"><div className="metric-label">{label}</div><div className={`metric-value ${cls || ""}`}>{value}</div></div>;
}

function buildWatchRow(symbol, rows) {
  const prices = rows.map((r) => Number(r.price)).filter((v) => Number.isFinite(v));
  const last = prices.length ? prices[prices.length - 1] : null;
  const first = prices.length ? prices[0] : null;
  const changePct = first ? ((last - first) / first) * 100 : 0;
  const volume = rows.reduce((sum, r) => sum + (Number(r.size) || 0), 0);
  return { symbol, series: prices, last, changePct, volume, n: prices.length };
}

function explainQueryFailure(err) {
  const message = String(err).replace(/^Error:\s*/, "");
  if (/unreachable/i.test(message) || /502/.test(message)) {
    return "Gateway or shard RDB path is unreachable. Check Metrics for transport lag or broken downstream components.";
  }
  return message;
}
