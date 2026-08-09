import { useEffect, useState } from "react";
import { api } from "../api.js";

// Icon + tone per action family, so the feed reads at a glance instead of
// requiring the actor/action columns to be parsed row by row.
function iconFor(e) {
  const failed = e.outcome !== "success";
  if (failed) return { glyph: "✕", tone: "fail" };
  if (e.actor === "watchdog") return { glyph: "⟲", tone: "auto" };
  if (/create|provision|enroll/i.test(e.action || "")) return { glyph: "+", tone: "ok" };
  if (/delete|deprovision|suspend/i.test(e.action || "")) return { glyph: "−", tone: "fail" };
  return { glyph: "•", tone: "ok" };
}

function relTime(iso) {
  const d = new Date(iso);
  const diffSec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return d.toLocaleDateString();
}

export default function AuditLog() {
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all"); // all | watchdog | admin | failed

  async function refresh() {
    try {
      setEvents(await api.listAudit(200));
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  const autoHeals = events.filter((e) => e.actor === "watchdog").length;
  const failures = events.filter((e) => e.outcome !== "success").length;

  const filtered = events.filter((e) => {
    if (filter === "watchdog") return e.actor === "watchdog";
    if (filter === "admin") return e.actor !== "watchdog";
    if (filter === "failed") return e.outcome !== "success";
    return true;
  });

  return (
    <div className="page">
      <h2>Audit log</h2>
      <p className="muted">
        Every admin action and every watchdog detection/auto-heal outcome, newest first.
        This is the trail that answers "who did what, and did the self-healing actually work."
      </p>
      {error && <div className="error">{error}</div>}

      <div className="kpi-row">
        <div className="kpi">
          <div className="kpi-label">Total events</div>
          <div className="kpi-value">{events.length}</div>
          <div className="kpi-sub">last 200 tracked</div>
        </div>
        <div className="kpi ok">
          <div className="kpi-label">Auto-heals</div>
          <div className="kpi-value">{autoHeals}</div>
          <div className="kpi-sub">watchdog, unattended</div>
        </div>
        <div className={`kpi ${failures ? "bad" : ""}`}>
          <div className="kpi-label">Failed actions</div>
          <div className="kpi-value">{failures}</div>
          <div className="kpi-sub">{failures ? "review below" : "clean run"}</div>
        </div>
      </div>

      <div className="chip-list" style={{ margin: "0.9rem 0" }}>
        {[
          ["all", "All"],
          ["watchdog", "Watchdog only"],
          ["admin", "Admin actions"],
          ["failed", "Failed"],
        ].map(([id, label]) => (
          <button
            key={id}
            className="chip"
            style={filter === id ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined}
            onClick={() => setFilter(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="card" style={{ padding: "0.4rem 1rem" }}>
        {filtered.length === 0 ? (
          <p className="muted" style={{ padding: "0.75rem 0.2rem" }}>No events match this filter yet.</p>
        ) : (
          <div className="activity-feed">
            {filtered.map((e) => {
              const { glyph, tone } = iconFor(e);
              return (
                <div className="activity-row" key={e.id}>
                  <span className={`activity-icon ${tone}`}>{glyph}</span>
                  <div className="activity-main">
                    <div className="activity-title">
                      <span className={e.actor === "watchdog" ? "activity-actor-watchdog" : ""}>{e.actor}</span>
                      <span className="muted">{e.action}</span>
                      <span className="mono">{e.target}</span>
                    </div>
                    {e.detail && <div className="activity-detail">{e.detail}</div>}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.3rem" }}>
                    <span className={`badge ${e.outcome === "success" ? "outcome-ok" : "outcome-fail"}`}>{e.outcome}</span>
                    <span className="activity-time" title={new Date(e.timestamp).toLocaleString()}>{relTime(e.timestamp)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
