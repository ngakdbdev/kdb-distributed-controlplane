import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { buildTimeForecast } from "../lib/timeForecast.js";
import { fetchTradeTape, fmt } from "../lib/tradingCore.js";

const POLL_MS = 12000;
const CONFIG_KEY = "tickforge_bot_config_v1";
const MAX_RISK_PCT = 1; // hard cap - the ask was "allowed risk is 1% of the capital", enforced here regardless of input
const LOG_LIMIT = 60;

function loadConfig() {
  try {
    const saved = JSON.parse(localStorage.getItem(CONFIG_KEY) || "null");
    if (saved) return saved;
  } catch { /* corrupt/old value - fall through to defaults */ }
  return { symbol: "AAPL", paperCapital: 10000, riskPct: 1, stopLossPct: 1.5 };
}

// A paper-trading bot, not a real one: it runs entirely in this browser tab
// (setInterval, no server-side job), trades against the same synthetic feed
// and paper OMS as the rest of this app, and forgets its position/log the
// moment the tab closes or reloads - only the config (symbol/capital/risk%)
// persists, in localStorage, on this machine. It is NOT connected to any
// bank account or broker - see the paper-capital note below for why.
//
// Strategy: simple momentum-following long-only. On each poll it re-reads
// the calendar-horizon forecast (lib/timeForecast.js) for the configured
// symbol; if flat and the trend is up, it opens a long sized so that a move
// to its stop-loss level would lose no more than riskPct% of the configured
// paper capital (classic fixed-fractional position sizing); if the trend
// flips down, or price trades through the stop, it closes the position.
export default function Bot() {
  const [config, setConfig] = useState(loadConfig);
  const [enabled, setEnabled] = useState(false);
  const [position, setPosition] = useState(null); // { qty, entryPrice, stopPrice, orderId }
  const [log, setLog] = useState([]);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const runningRef = useRef(false);
  const positionRef = useRef(null);

  useEffect(() => { localStorage.setItem(CONFIG_KEY, JSON.stringify(config)); }, [config]);
  useEffect(() => { positionRef.current = position; }, [position]);

  useEffect(() => {
    if (!enabled) { setStatus("idle"); return; }
    setStatus("watching");
    const tick = () => {
      if (runningRef.current) return;
      runningRef.current = true;
      evaluate().finally(() => { runningRef.current = false; });
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, config.symbol]);

  function appendLog(entry) {
    setLog((prev) => [{ ...entry, ts: new Date().toISOString() }, ...prev].slice(0, LOG_LIMIT));
  }

  async function evaluate() {
    const symbol = config.symbol.toUpperCase();
    const riskPct = Math.min(MAX_RISK_PCT, Math.max(0, Number(config.riskPct) || 0));
    const stopLossPct = Math.max(0.1, Number(config.stopLossPct) || 1.5);
    try {
      const { res } = await fetchTradeTape([symbol], 400);
      const cols = res.columns || [];
      const ti = cols.indexOf("time"), pi = cols.indexOf("price");
      const rows = (res.rows || []).map((row) => ({ time: ti >= 0 ? row[ti] : null, price: pi >= 0 ? row[pi] : null }));
      const forecast = buildTimeForecast(rows);
      const last = forecast.last;
      if (last == null) {
        appendLog({ type: "skip", reason: `no recent trades for ${symbol} yet` });
        return;
      }
      setStatus(`last check: ${forecast.trend} @ ${fmt(last)}`);

      const held = positionRef.current;
      if (!held) {
        if (forecast.trend === "up") {
          const stopPrice = last * (1 - stopLossPct / 100);
          const stopDistance = last - stopPrice;
          const riskAmount = (Number(config.paperCapital) || 0) * (riskPct / 100);
          const qty = Math.floor(riskAmount / stopDistance);
          if (qty < 1) {
            appendLog({ type: "skip", reason: `risk budget (${fmt(riskAmount)}) too small for a 1-share stop distance of ${fmt(stopDistance)} on ${symbol}` });
            return;
          }
          const order = await api.placeOrder({ symbol, side: "buy", qty, order_type: "market", ref_price: last });
          const opened = { qty: order.qty ?? qty, entryPrice: order.fill_price ?? last, stopPrice, orderId: order.id };
          setPosition(opened);
          appendLog({ type: "open", reason: `momentum up — bought ${opened.qty} @ ${fmt(opened.entryPrice)}, stop ${fmt(stopPrice)} (risking ${fmt(riskAmount)}, ${fmt(riskPct, 2)}% of ${fmt(config.paperCapital)} paper capital)` });
        } else {
          appendLog({ type: "hold", reason: `flat, trend is ${forecast.trend} — waiting for momentum up` });
        }
      } else {
        const stopHit = last <= held.stopPrice;
        const trendFlipped = forecast.trend === "down";
        if (stopHit || trendFlipped) {
          const order = await api.placeOrder({ symbol, side: "sell", qty: held.qty, order_type: "market", ref_price: last });
          const pnl = (order.fill_price ?? last) - held.entryPrice;
          setPosition(null);
          appendLog({
            type: pnl >= 0 ? "close-win" : "close-loss",
            reason: `${stopHit ? "stop-loss hit" : "trend flipped down"} — sold ${held.qty} @ ${fmt(order.fill_price ?? last)}, P&L ${fmt(pnl * held.qty)}`,
          });
        } else {
          appendLog({ type: "hold", reason: `long ${held.qty} @ ${fmt(held.entryPrice)}, stop ${fmt(held.stopPrice)}, last ${fmt(last)} — holding` });
        }
      }
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
      appendLog({ type: "error", reason: String(err).replace(/^Error:\s*/, "") });
    }
  }

  const configLocked = enabled || !!position;

  return (
    <div className="page">
      <h2>Trading bot <span className="paper-badge">PAPER</span></h2>
      <p className="muted">
        A momentum-following paper bot with a hard <strong>{MAX_RISK_PCT}% capital risk cap</strong> per
        trade. It runs client-side in this browser tab only — no server-side job, no real broker, no
        bank account. Closing this tab (or reloading) stops it and forgets its open-position memory;
        only your configuration below is remembered, on this machine.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="card">
        <div className="section-head"><h3>Paper capital &amp; risk policy</h3>
          <span className={`live-pill ${enabled ? "on" : "off"}`}>{enabled ? "● RUNNING" : "○ stopped"}</span>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          "Paper capital" is a number you set yourself — a virtual pot, not connected to any real bank
          account or brokerage. Position size is calculated so that a stop-loss hit never loses more
          than your risk % of this pot, capped at {MAX_RISK_PCT}% regardless of what you type below.
        </p>
        <div className="form-row wrap">
          <label className="muted">Symbol
            <input value={config.symbol} disabled={configLocked}
                   onChange={(e) => setConfig((c) => ({ ...c, symbol: e.target.value.toUpperCase() }))}
                   style={{ width: "6rem", textTransform: "uppercase", display: "block" }} />
          </label>
          <label className="muted">Paper capital
            <input type="number" min="0" value={config.paperCapital} disabled={configLocked}
                   onChange={(e) => setConfig((c) => ({ ...c, paperCapital: e.target.value }))}
                   style={{ width: "8rem", display: "block" }} />
          </label>
          <label className="muted">Risk per trade (max {MAX_RISK_PCT}%)
            <input type="number" min="0" max={MAX_RISK_PCT} step="0.1" value={config.riskPct} disabled={configLocked}
                   onChange={(e) => setConfig((c) => ({ ...c, riskPct: Math.min(MAX_RISK_PCT, Number(e.target.value) || 0) }))}
                   style={{ width: "6rem", display: "block" }} />
          </label>
          <label className="muted">Stop-loss distance %
            <input type="number" min="0.1" step="0.1" value={config.stopLossPct} disabled={configLocked}
                   onChange={(e) => setConfig((c) => ({ ...c, stopLossPct: e.target.value }))}
                   style={{ width: "6rem", display: "block" }} />
          </label>
        </div>
        <div style={{ marginTop: "0.75rem" }}>
          <button className="primary" style={{ background: enabled ? "var(--danger)" : "var(--success)" }}
                  onClick={() => setEnabled((v) => !v)}>
            {enabled ? "Stop bot" : "Start bot"}
          </button>
          <span className="muted" style={{ marginLeft: "0.75rem" }}>
            {configLocked && !enabled ? "Close the open position to edit config again." : `polls every ${POLL_MS / 1000}s while running`}
          </span>
        </div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="kpi-label">Risk per trade</div>
          <div className="kpi-value">{fmt((Number(config.paperCapital) || 0) * (Math.min(MAX_RISK_PCT, Number(config.riskPct) || 0) / 100))}</div>
          <div className="kpi-sub">{fmt(Math.min(MAX_RISK_PCT, Number(config.riskPct) || 0), 2)}% of {fmt(config.paperCapital)} paper capital</div>
        </div>
        <div className={`kpi ${position ? "ok" : ""}`}>
          <div className="kpi-label">Position</div>
          <div className="kpi-value">{position ? `${position.qty} sh` : "flat"}</div>
          <div className="kpi-sub">{position ? `entry ${fmt(position.entryPrice)} · stop ${fmt(position.stopPrice)}` : "no open bot position"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Status</div>
          <div className="kpi-value" style={{ fontSize: "1.1rem" }}>{status}</div>
          <div className="kpi-sub">{config.symbol}</div>
        </div>
      </div>

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
                  <div className="activity-title">{botTitle(entry.type)}</div>
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
  if (type === "close-loss") return "Closed — loss (stop or trend flip)";
  if (type === "error") return "Error";
  if (type === "skip") return "Skipped";
  return "Holding";
}
