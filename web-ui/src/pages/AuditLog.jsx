import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function AuditLog() {
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");

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

  return (
    <div className="page">
      <h2>Audit log</h2>
      <p className="muted">
        Every admin action and every watchdog detection/auto-heal outcome, newest first.
        This is the trail that answers "who did what, and did the self-healing actually work."
      </p>
      {error && <div className="error">{error}</div>}
      <table className="data-table">
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th><th>Outcome</th></tr></thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.timestamp).toLocaleString()}</td>
              <td>{e.actor}</td>
              <td>{e.action}</td>
              <td>{e.target}</td>
              <td className="muted">{e.detail}</td>
              <td>
                <span className={`badge ${e.outcome === "success" ? "outcome-ok" : "outcome-fail"}`}>
                  {e.outcome}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
