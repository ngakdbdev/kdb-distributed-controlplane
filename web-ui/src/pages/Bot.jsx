import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";
import { fmt } from "../lib/tradingCore.js";

const REFRESH_MS = 5000; // this page's own refresh of server state - separate
                          // from the server's own evaluation interval (12s,
                          // app/bot_scheduler.py's BOT_POLL_SEC)

// A paper-trading bot - server-side now: app/signal_engine.py's decision
// logic runs on an interval in the control-api process itself
// (app/bot_scheduler.py), not in this browser tab. This page is a thin
// client over it (control-api/app/routers/bot.py) - it configures the bot,
// and displays whatever the server has decided, but computes none of the
// strategy itself. Closing this tab does NOT stop the bot or lose its
// position/log memory; only the server-side toggle (Stop bot) does that.
//
// Two modes for deciding WHAT to trade:
//   - manual: you curate a basket (SymbolPicker, add/remove any time).
//   - auto: every server-side poll, the bot reads the cluster's actual live
//     symbol universe, ranks candidates by real per-minute drift, and
//     considers opening the top-ranked up-trending names. That screening
//     detail isn't a separate table here (it's server-computed, not
//     client-computed) - it shows up as reasoned entries in the activity
//     log below instead.
// Either way, the actual strategy is identical per symbol: momentum-
// following, long-only, sized so a stop-loss hit costs no more than
// riskPct% of paper capital - aggregated across every position the bot
// currently has open, not a fresh 1% per symbol.
export default function Bot() {
  const [config, setConfig] = useState(null);
  const [draft, setDraft] = useState(null); // editable copy of numeric fields, PUT on blur/change
  const [positions, setPositions] = useState([]);
  const [log, setLog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [closingSymbol, setClosingSymbol] = useState(null);
  const [saving, setSaving] = useState(false);
  // the bot places orders through the exact same _router() as a human's
  // order ticket (app/signal_engine.py -> place_market_order_internal) - so
  // its own real routing mode is whatever /trading/permission reports, not
  // always "paper". See Orders.jsx for the identical badge/banner pattern.
  const [perm, setPerm] = useState({ mode: "paper", live_routing: false });
  const pollRef = useRef(null);

  async function refresh() {
    try {
      const [c, p, l] = await Promise.all([api.getBotConfig(), api.getBotPositions(), api.getBotLog()]);
      setConfig(c);
      setDraft((prev) => (prev == null ? c : prev)); // don't clobber in-flight edits with a background refresh
      setPositions(p);
      setLog(l);
      setError("");
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { api.tradingPermission().then(setPerm).catch(() => {}); }, []);

  useEffect(() => {
    refresh();
    pollRef.current = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(pollRef.current);
  }, []);

  async function saveConfig(patch) {
    setSaving(true);
    try {
      const updated = await api.putBotConfig(patch);
      setConfig(updated);
      setDraft(updated);
      setError("");
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setSaving(false);
    }
  }

  async function closePositionManually(symbol) {
    setClosingSymbol(symbol);
    try {
      await api.closeBotPosition(symbol);
      await refresh();
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setClosingSymbol(null);
    }
  }

  // For a position Close can never succeed on (the broker rejects every
  // order for it - confirmed live with a demo-only symbol Alpaca doesn't
  // list) - drops it from local tracking without a broker order. Confirm
  // first since, unlike Close, this does NOT verify anything at the broker.
  async function forceClearPosition(symbol) {
    if (!window.confirm(
      `Force-clear ${symbol} from the bot's tracking WITHOUT placing a broker order?\n\n` +
      "Only do this if Close keeps failing (e.g. the broker doesn't recognize the symbol). " +
      "If a real position still exists at the broker, you'll need to close it there directly."
    )) return;
    setClosingSymbol(symbol);
    try {
      await api.forceClearBotPosition(symbol);
      await refresh();
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setClosingSymbol(null);
    }
  }

  // Full reset: force-clears every open position and empties the basket -
  // for when the bot is stuck (a held position blocking every basket edit,
  // per put_config's guard) and clearing symbols one at a time is more
  // friction than starting over.
  async function hardResetBot() {
    if (!window.confirm(
      "Hard reset the bot?\n\nThis force-clears every open position from tracking (without placing " +
      "broker orders - verify real broker state yourself if unsure), empties the basket, and stops the bot."
    )) return;
    setSaving(true);
    try {
      const updated = await api.hardResetBot();
      setConfig(updated);
      setDraft(updated);
      await refresh();
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setSaving(false);
    }
  }

  // Adding a symbol is always safe. Removing one the bot currently holds a
  // position in is not - the server rejects that (see routers/bot.py) so
  // the position stays monitored; surface that rejection rather than
  // silently letting the chip disappear from the basket.
  function handleBasketChange(nextSymbols) {
    const capped = nextSymbols.slice(0, config?.max_basket || 6).map((s) => s.toUpperCase());
    saveConfig({ symbols: capped });
  }

  const modeLabel = {
    paper: "PAPER", "alpaca-paper": "ALPACA PAPER", "alpaca-live": "⚠ ALPACA LIVE — REAL MONEY",
    "ibkr-paper": "IBKR PAPER", "ibkr-live": "⚠ IBKR LIVE — REAL MONEY",
    misconfigured: "⚠ MISCONFIGURED",
  }[perm.mode] || "PAPER";
  const loudBadge = perm.live_routing || perm.mode === "misconfigured";

  if (loading) {
    return <div className="page"><h2>Trading bot <span className={`paper-badge${loudBadge ? " live" : ""}`}>{modeLabel}</span></h2><p className="muted">Loading…</p></div>;
  }

  const openCount = positions.length;
  const totalRiskCap = (Number(config.paper_capital) || 0) * (config.risk_pct / 100);
  const usedRisk = positions.reduce((sum, p) => sum + p.qty * (p.entry_price - p.stop_price), 0);
  const availableRisk = Math.max(0, totalRiskCap - usedRisk);
  const fieldsLocked = config.enabled || openCount > 0;
  // Switching manual/auto mode is safe even with an open position - the
  // server has no restriction on it either (routers/bot.py's put_config
  // sets config.mode unconditionally), and existing positions keep being
  // watched for their exit regardless of mode (see the auto-mode copy
  // below). Only lock the mode TOGGLE while the bot is actively RUNNING
  // (mid-flight mode changes to a live loop are the genuinely confusing
  // case) - fieldsLocked above stays broader (also locks on a merely-held
  // leftover position) for the sizing fields, where changing risk_pct/
  // paper_capital while a position is open really would leave that
  // position's stop-loss sized against stale numbers.
  const modeLocked = config.enabled;
  const canStart = config.mode === "auto" ? true : (config.symbols || []).length > 0;
  const lastActivity = log[0]?.ts ? new Date(log[0].ts).toLocaleTimeString() : null;
  const modeCopy = {
    paper: "no real broker, no bank account - every fill is simulated.",
    "alpaca-paper": "orders route to a real Alpaca account's paper-trading simulation - real order mechanics against live prices, zero real money at risk.",
    "alpaca-live": "orders route to a real Alpaca account with REAL MONEY - every fill below is a real trade.",
    "ibkr-paper": "orders route to a real Interactive Brokers account's paper-trading simulation via a Client Portal Gateway - zero real money at risk.",
    "ibkr-live": "orders route to a real Interactive Brokers account with REAL MONEY - every fill below is a real trade.",
    misconfigured: `order routing is misconfigured (${perm.error || "both Alpaca and IBKR appear to be configured at once"}) - the bot cannot place orders until this is fixed.`,
  }[perm.mode] || "no real broker, no bank account - every fill is simulated.";

  return (
    <div className="page">
      <h2>Trading bot <span className={`paper-badge${loudBadge ? " live" : ""}`}>{modeLabel}</span></h2>
      <p className="muted">
        A momentum-following bot with a hard <strong>{config.max_risk_pct}% capital risk cap</strong>,
        shared across every open position (not a fresh 1% each). It runs server-side on a poll interval -
        {" "}{modeCopy} It keeps running (and watching its stop-losses) whether or not this page is open.
      </p>
      {perm.live_routing && (
        <div className="ops-banner err">
          <div>Live order routing is active for this deployment ({perm.mode === "ibkr-live" ? "IBKR_TRADING_MODE" : "ALPACA_TRADING_MODE"}=live).
          This bot places REAL trades with REAL money, autonomously, on every enabled poll cycle - review its
          config below before leaving it running.</div>
        </div>
      )}
      {perm.mode === "misconfigured" && (
        <div className="ops-banner err"><div>{perm.error}</div></div>
      )}
      {error && <div className="error">{error}</div>}

      <div className="card">
        <div className="section-head"><h3>What it trades</h3>
          <span className={`live-pill ${config.enabled ? "on" : "off"}`}>{config.enabled ? "● RUNNING" : "○ stopped"}</span>
        </div>
        <div className="chip-list" style={{ marginBottom: "0.75rem" }}>
          <button className={`chip ${config.mode === "manual" ? "generate" : ""}`} disabled={modeLocked}
                  onClick={() => saveConfig({ mode: "manual" })}>Manual basket</button>
          <button className={`chip ${config.mode === "auto" ? "generate" : ""}`} disabled={modeLocked}
                  onClick={() => saveConfig({ mode: "auto" })}>Auto-screen universe</button>
        </div>

        {config.mode === "manual" ? (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              You choose the basket — add or remove symbols any time the bot isn't holding a position in
              them. A basket of one behaves just like a single-symbol bot. The bot only ever trades what's here.
            </p>
            <div style={{ minWidth: "16rem", marginBottom: "0.75rem" }}>
              <label className="muted" style={{ display: "block", marginBottom: "0.25rem" }}>Basket (max {config.max_basket})</label>
              <SymbolPicker value={config.symbols || []} onChange={handleBasketChange} placeholder="add a symbol…" />
            </div>
          </>
        ) : (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              Every server-side poll, the bot reads the cluster's actual live symbol universe (not a static
              list), ranks candidates by real recent momentum, and considers opening the top up-trending
              names — up to the concurrent-position limit below. Whatever it already holds keeps being
              watched for its exit regardless of ranking; see the activity log for what the last screen found.
            </p>
            <label className="muted" style={{ display: "block", marginBottom: "0.75rem" }}>Max concurrent positions
              <input type="number" min="1" max={config.max_positions_cap} value={draft.max_positions} disabled={fieldsLocked}
                     onChange={(e) => setDraft((d) => ({ ...d, max_positions: Number(e.target.value) || 1 }))}
                     onBlur={() => saveConfig({ max_positions: draft.max_positions })}
                     style={{ width: "5rem", display: "block" }} />
            </label>
          </>
        )}

        <div className="form-row wrap">
          <label className="muted">Paper capital
            <input type="number" min="0" value={draft.paper_capital} disabled={fieldsLocked}
                   onChange={(e) => setDraft((d) => ({ ...d, paper_capital: e.target.value }))}
                   onBlur={() => saveConfig({ paper_capital: Number(draft.paper_capital) || 0 })}
                   style={{ width: "8rem", display: "block" }} />
          </label>
          <label className="muted">Risk (max {config.max_risk_pct}%)
            <input type="number" min="0" max={config.max_risk_pct} step="0.1" value={draft.risk_pct} disabled={fieldsLocked}
                   onChange={(e) => setDraft((d) => ({ ...d, risk_pct: e.target.value }))}
                   onBlur={() => saveConfig({ risk_pct: Number(draft.risk_pct) || 0 })}
                   style={{ width: "6rem", display: "block" }} />
          </label>
          <label className="muted">Stop-loss distance %
            <input type="number" min="0.1" step="0.1" value={draft.stop_loss_pct} disabled={fieldsLocked}
                   onChange={(e) => setDraft((d) => ({ ...d, stop_loss_pct: e.target.value }))}
                   onBlur={() => saveConfig({ stop_loss_pct: Number(draft.stop_loss_pct) || 1.5 })}
                   style={{ width: "6rem", display: "block" }} />
          </label>
        </div>
        <div style={{ marginTop: "0.75rem" }}>
          <button className="primary" style={{ background: config.enabled ? "var(--danger)" : "var(--success)" }}
                  onClick={() => saveConfig({ enabled: !config.enabled })} disabled={!canStart || saving}>
            {config.enabled ? "Stop bot" : "Start bot"}
          </button>{" "}
          <button className="chip" disabled={saving} onClick={hardResetBot}
                  title="Force-clears every open position and empties the basket - for when the bot is stuck">
            Hard reset
          </button>
          <span className="muted" style={{ marginLeft: "0.75rem" }}>
            {!config.enabled && openCount > 0
              ? "Stopped with position(s) still open — nothing is watching their stop-loss until you restart or close them manually below."
              : "evaluated server-side on its own interval, whether or not this page is open"}
          </span>
        </div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="kpi-label">Risk budget</div>
          <div className="kpi-value">{fmt(availableRisk)}<span className="kpi-unit">/ {fmt(totalRiskCap)}</span></div>
          <div className="kpi-sub">available of {fmt(config.risk_pct, 2)}% of {fmt(config.paper_capital)} paper capital</div>
        </div>
        <div className={`kpi ${openCount ? "ok" : ""}`}>
          <div className="kpi-label">Open positions</div>
          <div className="kpi-value">{openCount}{config.mode === "auto" ? `/${config.max_positions}` : ""}</div>
          <div className="kpi-sub">{openCount ? positions.map((p) => p.symbol).join(", ") : "flat"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Status</div>
          <div className="kpi-value" style={{ fontSize: "0.95rem" }}>{config.enabled ? "watching" : "idle"}</div>
          <div className="kpi-sub">{lastActivity ? `last activity ${lastActivity}` : (config.mode === "auto" ? "auto-screening" : (config.symbols || []).join(", ") || "no symbols")}</div>
        </div>
      </div>

      {openCount > 0 && (
        <div className="card" style={{ padding: "0.4rem 1rem" }}>
          <h3>Open positions</h3>
          <table className="data-table">
            <thead><tr><th>symbol</th><th>qty</th><th>entry</th><th>stop</th><th></th></tr></thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.symbol}>
                  <td>{p.symbol}</td><td>{fmt(p.qty)}</td><td>{fmt(p.entry_price)}</td><td>{fmt(p.stop_price)}</td>
                  <td>
                    <button className="chip" disabled={closingSymbol === p.symbol} onClick={() => closePositionManually(p.symbol)}>
                      {closingSymbol === p.symbol ? "Closing…" : "Close"}
                    </button>{" "}
                    <button className="chip" disabled={closingSymbol === p.symbol} onClick={() => forceClearPosition(p.symbol)}
                            title="Drops this position from tracking WITHOUT a broker order - use only if Close keeps failing">
                      Force clear
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card" style={{ padding: "0.4rem 1rem" }}>
        <h3>Bot activity</h3>
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
                <span className="activity-time">{entry.ts ? new Date(entry.ts).toLocaleTimeString() : ""}</span>
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
  if (type === "close-loss") return "Closed — loss";
  if (type === "error") return "Error";
  if (type === "skip") return "Skipped";
  return "Holding";
}
