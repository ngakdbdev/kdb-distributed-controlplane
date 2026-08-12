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

  // Adding a symbol is always safe. Removing one the bot currently holds a
  // position in is not - the server rejects that (see routers/bot.py) so
  // the position stays monitored; surface that rejection rather than
  // silently letting the chip disappear from the basket.
  function handleBasketChange(nextSymbols) {
    const capped = nextSymbols.slice(0, config?.max_basket || 6).map((s) => s.toUpperCase());
    saveConfig({ symbols: capped });
  }

  if (loading) {
    return <div className="page"><h2>Trading bot <span className="paper-badge">PAPER</span></h2><p className="muted">Loading…</p></div>;
  }

  const openCount = positions.length;
  const totalRiskCap = (Number(config.paper_capital) || 0) * (config.risk_pct / 100);
  const usedRisk = positions.reduce((sum, p) => sum + p.qty * (p.entry_price - p.stop_price), 0);
  const availableRisk = Math.max(0, totalRiskCap - usedRisk);
  const fieldsLocked = config.enabled || openCount > 0;
  const canStart = config.mode === "auto" ? true : (config.symbols || []).length > 0;
  const lastActivity = log[0]?.ts ? new Date(log[0].ts).toLocaleTimeString() : null;

  return (
    <div className="page">
      <h2>Trading bot <span className="paper-badge">PAPER</span></h2>
      <p className="muted">
        A momentum-following paper bot with a hard <strong>{config.max_risk_pct}% capital risk cap</strong>,
        shared across every open position (not a fresh 1% each). It runs server-side on a poll interval -
        no real broker, no bank account - and keeps running (and watching its stop-losses) whether or not
        this page is open.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="card">
        <div className="section-head"><h3>What it trades</h3>
          <span className={`live-pill ${config.enabled ? "on" : "off"}`}>{config.enabled ? "● RUNNING" : "○ stopped"}</span>
        </div>
        <div className="chip-list" style={{ marginBottom: "0.75rem" }}>
          <button className={`chip ${config.mode === "manual" ? "generate" : ""}`} disabled={fieldsLocked}
                  onClick={() => saveConfig({ mode: "manual" })}>Manual basket</button>
          <button className={`chip ${config.mode === "auto" ? "generate" : ""}`} disabled={fieldsLocked}
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
