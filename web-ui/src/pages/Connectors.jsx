import { useEffect, useState } from "react";
import { api } from "../api.js";
import StatusBadge from "../components/StatusBadge.jsx";

export default function Connectors() {
  const [connectors, setConnectors] = useState([]);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState(null);

  async function refresh() {
    try {
      setConnectors(await api.listConnectors());
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, []);

  async function toggle(c) {
    setBusyId(c.id);
    try {
      await api.toggleConnector(c.id);
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="page">
      <h2>Feed connectors</h2>
      <p className="muted">
        These are synthetic generators shaped like real vendor feeds (B-PIPE equities, CRIMS-style risk data) -
        they demonstrate the connector pattern, not a live vendor integration. Real B-PIPE/CRIMS credentials
        would slot in behind the same on/off toggle.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="connector-grid">
        {connectors.map((c) => (
          <div className="card connector-card" key={c.id}>
            <div className="connector-header">
              <h3>{c.name}</h3>
              <StatusBadge status={c.live_status} />
            </div>
            <p className="muted">{c.description}</p>
            <div className="connector-meta">kind: {c.kind}</div>
            <button
              className={c.enabled ? "danger" : "primary"}
              disabled={busyId === c.id}
              onClick={() => toggle(c)}
            >
              {c.enabled ? "Disable" : "Enable"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
