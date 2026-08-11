import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";
import { buildTimeForecast } from "../lib/timeForecast.js";
import { fetchTradeTape, fmt, groupBySymbol } from "../lib/tradingCore.js";

const POLL_MS = 12000;
const CONFIG_KEY = "tickforge_bot_config_v1";
const MAX_RISK_PCT = 1; // hard cap - the ask was "allowed risk is 1% of the capital", enforced here regardless of input
const MAX_BASKET = 6; // manual mode - same cap Markets' basket picker uses
const MAX_POSITIONS_CAP = 10; // auto mode - upper bound on the maxPositions input
const UNIVERSE_SCAN_CAP = 30; // auto mode - how many distinct symbols get pulled tape for per screen
const LOG_LIMIT = 60;

function loadConfig() {
  try {
    const saved = JSON.parse(localStorage.getItem(CONFIG_KEY) || "null");
    if (saved) {
      // migrate an older single-symbol config (`symbol: "AAPL"`) to the basket shape
      if (!saved.symbols && saved.symbol) saved.symbols = [saved.symbol];
      if (!saved.mode) saved.mode = "manual";
      if (!saved.maxPositions) saved.maxPositions = 3;
      if (Array.isArray(saved.symbols) && saved.symbols.length) return saved;
    }
  } catch { /* corrupt/old value - fall through to defaults */ }
  return { mode: "manual", symbols: ["AAPL"], maxPositions: 3, paperCapital: 10000, riskPct: 1, stopLossPct: 1.5 };
}

async function fetchUniverseSymbols() {
  const res = await api.runQuery({ target: "gateway", query: "exec distinct sym from trade", limit: 200 });
  const idx = (res.columns || []).indexOf("value");
  return idx >= 0 ? (res.rows || []).map((r) => String(r[idx])) : [];
}

// A paper-trading bot, not a real one: it runs entirely in this browser tab
// (setInterval, no server-side job), trades against the same synthetic feed
// and paper OMS as the rest of this app, and forgets its open positions/log
// the moment the tab closes or reloads - only the config persists, in
// localStorage, on this machine. It is NOT connected to any bank account or
// broker - see the paper-capital note below for why.
//
// Two modes for deciding WHAT to trade:
//   - manual: you curate a basket (SymbolPicker, add/remove any time).
//   - auto: every poll, the bot reads the cluster's actual live symbol
//     universe (`exec distinct sym from trade` - real data, not a static
//     reference list), pulls a batched tape for it, ranks candidates by
//     real per-minute drift (lib/timeForecast.js), and considers opening
//     the top-ranked up-trending names, up to maxPositions concurrently.
// Either way, the actual strategy is identical per symbol: momentum-
// following, long-only, sized so a stop-loss hit costs no more than
// riskPct% of paper capital - aggregated across every position the bot
// currently has open, not a fresh 1% per symbol. Whatever the bot already
// holds keeps being monitored for its exit every tick regardless of mode or
// whether it's still top-ranked.
export default function Bot() {
  const [config, setConfig] = useState(loadConfig);
  const [enabled, setEnabled] = useState(false);
  const [positions, setPositions] = useState({}); // { SYM: { qty, entryPrice, stopPrice, orderId } }
  const [log, setLog] = useState([]);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [closingSymbol, setClosingSymbol] = useState(null);
  const [candidates, setCandidates] = useState([]); // last universe screen, auto mode only - informational
  const runningRef = useRef(false);
  const positionsRef = useRef({});

  useEffect(() => { localStorage.setItem(CONFIG_KEY, JSON.stringify(config)); }, [config]);
  useEffect(() => { positionsRef.current = positions; }, [positions]);

  useEffect(() => {
    if (!enabled) { setStatus("idle"); return; }
    setStatus("watching");
    const tick = () => {
      if (runningRef.current) return;
      runningRef.current = true;
      evaluateAll().finally(() => { runningRef.current = false; });
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, config.mode, (config.symbols || []).join(","), config.maxPositions]);

  function appendLog(entry) {
    setLog((prev) => [{ ...entry, ts: new Date().toISOString() }, ...prev].slice(0, LOG_LIMIT));
  }

  async function evaluateAll() {
    const heldSymbols = Object.keys(positionsRef.current);
    let symbols;
    if (config.mode === "auto") {
      try {
        const universe = (await fetchUniverseSymbols()).slice(0, UNIVERSE_SCAN_CAP);
        const ranked = await rankByMomentum(universe);
        setCandidates(ranked);
        const maxPositions = Math.min(MAX_POSITIONS_CAP, Math.max(1, Number(config.maxPositions) || 1));
        const openSlots = Math.max(0, maxPositions - heldSymbols.length);
        const picks = ranked.filter((r) => r.trend === "up" && !heldSymbols.includes(r.symbol)).slice(0, openSlots).map((r) => r.symbol);
        symbols = [...new Set([...heldSymbols, ...picks])];
        if (!symbols.length) { setStatus(`screened ${universe.length} symbols — nothing trending up`); return; }
      } catch (err) {
        setError(String(err).replace(/^Error:\s*/, ""));
        appendLog({ symbol: null, type: "error", reason: `universe screen failed: ${String(err).replace(/^Error:\s*/, "")}` });
        return;
      }
    } else {
      symbols = [...new Set((config.symbols || []).map((s) => s.toUpperCase()))];
      if (!symbols.length) { setStatus("no symbols in basket"); return; }
    }

    const riskPct = Math.min(MAX_RISK_PCT, Math.max(0, Number(config.riskPct) || 0));
    const stopLossPct = Math.max(0.1, Number(config.stopLossPct) || 1.5);
    const totalRiskCap = (Number(config.paperCapital) || 0) * (riskPct / 100);
    // a local working copy, updated synchronously as positions open/close
    // within this pass - positionsRef only catches up after React commits a
    // render, which is too late to stop two symbols in the SAME tick both
    // sizing against the same "available" budget and jointly overspending it.
    let working = { ...positionsRef.current };

    try {
      const { res } = await fetchTradeTape(symbols, 1200);
      const grouped = groupBySymbol(res, symbols);
      const statusParts = [];

      for (const symbol of symbols) {
        const rows = grouped[symbol] || [];
        const forecast = buildTimeForecast(rows);
        const last = forecast.last;
        if (last == null) {
          appendLog({ symbol, type: "skip", reason: `no recent trades for ${symbol} yet` });
          continue;
        }
        statusParts.push(`${symbol} ${forecast.trend} @ ${fmt(last)}`);

        const held = working[symbol];
        if (!held) {
          if (forecast.trend === "up") {
            const stopPrice = last * (1 - stopLossPct / 100);
            const stopDistance = last - stopPrice;
            const usedRisk = Object.values(working).reduce((sum, p) => sum + p.qty * (p.entryPrice - p.stopPrice), 0);
            const availableRisk = Math.max(0, totalRiskCap - usedRisk);
            const qty = Math.floor(availableRisk / stopDistance);
            if (qty < 1) {
              appendLog({
                symbol, type: "skip",
                reason: `risk budget exhausted — ${fmt(availableRisk)} available of ${fmt(totalRiskCap)} total cap (${Object.keys(working).length} position(s) already open), not enough for a 1-share stop distance of ${fmt(stopDistance)} on ${symbol}`,
              });
              continue;
            }
            const order = await api.placeOrder({ symbol, side: "buy", qty, order_type: "market", ref_price: last });
            const opened = { qty: order.qty ?? qty, entryPrice: order.fill_price ?? last, stopPrice, orderId: order.id };
            working = { ...working, [symbol]: opened };
            setPositions((prev) => ({ ...prev, [symbol]: opened }));
            appendLog({
              symbol, type: "open",
              reason: `${config.mode === "auto" ? "screened + " : ""}momentum up — bought ${opened.qty} @ ${fmt(opened.entryPrice)}, stop ${fmt(stopPrice)} (risking ${fmt(opened.qty * stopDistance)} of ${fmt(availableRisk)} available, ${fmt(totalRiskCap)} total cap)`,
            });
          } else {
            appendLog({ symbol, type: "hold", reason: `flat, trend is ${forecast.trend} — waiting for momentum up` });
          }
        } else {
          const stopHit = last <= held.stopPrice;
          const trendFlipped = forecast.trend === "down";
          if (stopHit || trendFlipped) {
            const order = await api.placeOrder({ symbol, side: "sell", qty: held.qty, order_type: "market", ref_price: last });
            const pnl = (order.fill_price ?? last) - held.entryPrice;
            const next = { ...working }; delete next[symbol]; working = next;
            setPositions((prev) => { const n = { ...prev }; delete n[symbol]; return n; });
            appendLog({
              symbol, type: pnl >= 0 ? "close-win" : "close-loss",
              reason: `${stopHit ? "stop-loss hit" : "trend flipped down"} — sold ${held.qty} @ ${fmt(order.fill_price ?? last)}, P&L ${fmt(pnl * held.qty)}`,
            });
          } else {
            appendLog({ symbol, type: "hold", reason: `long ${held.qty} @ ${fmt(held.entryPrice)}, stop ${fmt(held.stopPrice)}, last ${fmt(last)} — holding` });
          }
        }
      }
      setStatus(statusParts.join(" · ") || "watching");
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
      appendLog({ symbol: null, type: "error", reason: String(err).replace(/^Error:\s*/, "") });
    }
  }

  // "Stop bot" only pauses the poll loop - it deliberately does NOT force-
  // liquidate open positions (a stop button shouldn't dump positions at
  // whatever price happens to be current without being asked to). That
  // leaves a real gap if you stop while holding: nothing is watching their
  // stop-losses anymore - this is the explicit per-symbol way to flatten
  // from this page instead of detouring to Orders, or resuming the bot.
  async function closePositionManually(symbol) {
    const held = positions[symbol];
    if (!held) return;
    setClosingSymbol(symbol);
    try {
      const { res } = await fetchTradeTape([symbol], 50);
      const cols = res.columns || [];
      const pi = cols.indexOf("price");
      const rows = res.rows || [];
      const last = pi >= 0 && rows.length ? Number(rows[rows.length - 1][pi]) : held.entryPrice;
      const order = await api.placeOrder({ symbol, side: "sell", qty: held.qty, order_type: "market", ref_price: last });
      const pnl = (order.fill_price ?? last) - held.entryPrice;
      setPositions((prev) => { const n = { ...prev }; delete n[symbol]; return n; });
      appendLog({
        symbol, type: pnl >= 0 ? "close-win" : "close-loss",
        reason: `manually closed — sold ${held.qty} @ ${fmt(order.fill_price ?? last)}, P&L ${fmt(pnl * held.qty)}`,
      });
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setClosingSymbol(null);
    }
  }

  // Adding a symbol is always safe. Removing one the bot currently holds a
  // position in is not - that would orphan the position from monitoring the
  // same way stopping the bot does, so it's blocked here with an explanation
  // rather than silently letting the chip disappear; close the position
  // first (per-row button below), then remove it.
  function handleBasketChange(nextSymbols) {
    const capped = nextSymbols.slice(0, MAX_BASKET).map((s) => s.toUpperCase());
    const currentSymbols = config.symbols || [];
    const removed = currentSymbols.filter((s) => !capped.includes(s));
    const blocked = removed.filter((s) => positions[s]);
    const finalSymbols = blocked.length ? [...new Set([...capped, ...blocked])] : capped;
    if (blocked.length) {
      appendLog({ symbol: null, type: "skip", reason: `can't remove ${blocked.join(", ")} from the basket while it has an open position — close it first` });
    }
    setConfig((c) => ({ ...c, symbols: finalSymbols }));
  }

  function setMode(mode) {
    if (fieldsLocked) return;
    setConfig((c) => ({ ...c, mode }));
  }

  const openCount = Object.keys(positions).length;
  const riskPctUsed = Math.min(MAX_RISK_PCT, Math.max(0, Number(config.riskPct) || 0));
  const totalRiskCap = (Number(config.paperCapital) || 0) * (riskPctUsed / 100);
  const usedRisk = Object.values(positions).reduce((sum, p) => sum + p.qty * (p.entryPrice - p.stopPrice), 0);
  const availableRisk = Math.max(0, totalRiskCap - usedRisk);
  const fieldsLocked = enabled || openCount > 0;
  const canStart = config.mode === "auto" ? true : (config.symbols || []).length > 0;

  return (
    <div className="page">
      <h2>Trading bot <span className="paper-badge">PAPER</span></h2>
      <p className="muted">
        A momentum-following paper bot with a hard <strong>{MAX_RISK_PCT}% capital risk cap</strong>,
        shared across every open position (not a fresh 1% each). It runs client-side in this browser
        tab only — no server-side job, no real broker, no bank account. Closing this tab (or reloading)
        stops it and forgets its open-position memory; only your configuration below is remembered, on
        this machine.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="card">
        <div className="section-head"><h3>What it trades</h3>
          <span className={`live-pill ${enabled ? "on" : "off"}`}>{enabled ? "● RUNNING" : "○ stopped"}</span>
        </div>
        <div className="chip-list" style={{ marginBottom: "0.75rem" }}>
          <button className={`chip ${config.mode === "manual" ? "generate" : ""}`} disabled={fieldsLocked} onClick={() => setMode("manual")}>Manual basket</button>
          <button className={`chip ${config.mode === "auto" ? "generate" : ""}`} disabled={fieldsLocked} onClick={() => setMode("auto")}>Auto-screen universe</button>
        </div>

        {config.mode === "manual" ? (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              You choose the basket — add or remove symbols any time the bot isn't holding a position in
              them. A basket of one behaves just like a single-symbol bot. The bot only ever trades what's here.
            </p>
            <div style={{ minWidth: "16rem", marginBottom: "0.75rem" }}>
              <label className="muted" style={{ display: "block", marginBottom: "0.25rem" }}>Basket (max {MAX_BASKET})</label>
              <SymbolPicker value={config.symbols || []} onChange={handleBasketChange} placeholder="add a symbol…" />
            </div>
          </>
        ) : (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              Every poll, the bot reads the cluster's actual live symbol universe (not a static list),
              ranks candidates by real recent momentum, and considers opening the top up-trending names —
              up to the concurrent-position limit below. Whatever it already holds keeps being watched for
              its exit regardless of ranking.
            </p>
            <label className="muted" style={{ display: "block", marginBottom: "0.75rem" }}>Max concurrent positions
              <input type="number" min="1" max={MAX_POSITIONS_CAP} value={config.maxPositions} disabled={fieldsLocked}
                     onChange={(e) => setConfig((c) => ({ ...c, maxPositions: Math.min(MAX_POSITIONS_CAP, Math.max(1, Number(e.target.value) || 1)) }))}
                     style={{ width: "5rem", display: "block" }} />
            </label>
          </>
        )}

        <div className="form-row wrap">
          <label className="muted">Paper capital
            <input type="number" min="0" value={config.paperCapital} disabled={fieldsLocked}
                   onChange={(e) => setConfig((c) => ({ ...c, paperCapital: e.target.value }))}
                   style={{ width: "8rem", display: "block" }} />
          </label>
          <label className="muted">Risk (max {MAX_RISK_PCT}%)
            <input type="number" min="0" max={MAX_RISK_PCT} step="0.1" value={config.riskPct} disabled={fieldsLocked}
                   onChange={(e) => setConfig((c) => ({ ...c, riskPct: Math.min(MAX_RISK_PCT, Number(e.target.value) || 0) }))}
                   style={{ width: "6rem", display: "block" }} />
          </label>
          <label className="muted">Stop-loss distance %
            <input type="number" min="0.1" step="0.1" value={config.stopLossPct} disabled={fieldsLocked}
                   onChange={(e) => setConfig((c) => ({ ...c, stopLossPct: e.target.value }))}
                   style={{ width: "6rem", display: "block" }} />
          </label>
        </div>
        <div style={{ marginTop: "0.75rem" }}>
          <button className="primary" style={{ background: enabled ? "var(--danger)" : "var(--success)" }}
                  onClick={() => setEnabled((v) => !v)} disabled={!canStart}>
            {enabled ? "Stop bot" : "Start bot"}
          </button>
          <span className="muted" style={{ marginLeft: "0.75rem" }}>
            {!enabled && openCount > 0 ? "Stopped with position(s) still open — nothing is watching their stop-loss until you restart or close them manually below." : `polls every ${POLL_MS / 1000}s while running`}
          </span>
        </div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="kpi-label">Risk budget</div>
          <div className="kpi-value">{fmt(availableRisk)}<span className="kpi-unit">/ {fmt(totalRiskCap)}</span></div>
          <div className="kpi-sub">available of {fmt(riskPctUsed, 2)}% of {fmt(config.paperCapital)} paper capital</div>
        </div>
        <div className={`kpi ${openCount ? "ok" : ""}`}>
          <div className="kpi-label">Open positions</div>
          <div className="kpi-value">{openCount}{config.mode === "auto" ? `/${config.maxPositions}` : ""}</div>
          <div className="kpi-sub">{openCount ? Object.keys(positions).join(", ") : "flat"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Status</div>
          <div className="kpi-value" style={{ fontSize: "0.95rem" }}>{status}</div>
          <div className="kpi-sub">{config.mode === "auto" ? `auto-screening (top ${UNIVERSE_SCAN_CAP})` : (config.symbols || []).join(", ") || "no symbols"}</div>
        </div>
      </div>

      {config.mode === "auto" && candidates.length > 0 && (
        <div className="card" style={{ padding: "0.4rem 1rem" }}>
          <div className="section-head"><h3>Last screen</h3><span className="muted">ranked by real per-minute drift, most bullish first</span></div>
          <table className="data-table">
            <thead><tr><th>symbol</th><th>trend</th><th>last</th><th></th></tr></thead>
            <tbody>
              {candidates.slice(0, 10).map((c) => (
                <tr key={c.symbol}>
                  <td>{c.symbol}</td>
                  <td><span className={`signal-pill ${c.trend}`}>{c.trend}</span></td>
                  <td>{c.last != null ? fmt(c.last) : "—"}</td>
                  <td>{positions[c.symbol] ? <span className="status-pill ok">held</span> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openCount > 0 && (
        <div className="card" style={{ padding: "0.4rem 1rem" }}>
          <h3>Open positions</h3>
          <table className="data-table">
            <thead><tr><th>symbol</th><th>qty</th><th>entry</th><th>stop</th><th></th></tr></thead>
            <tbody>
              {Object.entries(positions).map(([symbol, p]) => (
                <tr key={symbol}>
                  <td>{symbol}</td><td>{fmt(p.qty)}</td><td>{fmt(p.entryPrice)}</td><td>{fmt(p.stopPrice)}</td>
                  <td>
                    <button className="chip" disabled={closingSymbol === symbol} onClick={() => closePositionManually(symbol)}>
                      {closingSymbol === symbol ? "Closing…" : "Close"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card" style={{ padding: "0.4rem 1rem" }}>
        <h3>Bot activity (this session)</h3>
        {log.length === 0 ? (
          <p className="muted" style={{ padding: "0.75rem 0.2rem" }}>Start the bot to see its decisions here — including the ones where it decides to do nothing.</p>
        ) : (
          <div className="activity-feed">
            {log.map((entry, i) => (
              <div className="activity-row" key={i}>
                <span className={`activity-icon ${botIconTone(entry.type)}`}>{botIcon(entry.type)}</span>
                <div className="activity-main">
                  <div className="activity-title">{entry.symbol && <span className="mono">{entry.symbol}</span>} {botTitle(entry.type)}</div>
                  <div className="activity-detail">{entry.reason}</div>
                </div>
                <span className="activity-time">{new Date(entry.ts).toLocaleTimeString()}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

async function rankByMomentum(universe) {
  if (!universe.length) return [];
  const { res } = await fetchTradeTape(universe, 1200);
  const grouped = groupBySymbol(res, universe);
  const ranked = universe.map((symbol) => {
    const forecast = buildTimeForecast(grouped[symbol] || []);
    return { symbol, trend: forecast.trend, driftPerMin: forecast.driftPerMin ?? 0, last: forecast.last };
  }).filter((r) => r.last != null);
  ranked.sort((a, b) => (b.driftPerMin || 0) - (a.driftPerMin || 0));
  return ranked;
}

function botIcon(type) {
  if (type === "open") return "+";
  if (type === "close-win") return "✓";
  if (type === "close-loss") return "✕";
  if (type === "error") return "!";
  if (type === "skip") return "•";
  return "…";
}
function botIconTone(type) {
  if (type === "open") return "auto";
  if (type === "close-win") return "ok";
  if (type === "close-loss" || type === "error") return "fail";
  return "";
}
function botTitle(type) {
  if (type === "open") return "Opened long";
  if (type === "close-win") return "Closed — profit";
  if (type === "close-loss") return "Closed — loss";
  if (type === "error") return "Error";
  if (type === "skip") return "Skipped";
  return "Holding";
}
