import { useEffect, useState } from "react";
import { api } from "../api.js";
import StatusBadge from "../components/StatusBadge.jsx";
import SymbolPicker from "../components/SymbolPicker.jsx";

export default function Connectors() {
  const [connectors, setConnectors] = useState([]);
  const [providers, setProviders] = useState([]);
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
    api.listProviders().then(setProviders).catch((err) => setError(String(err)));
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

  async function saveSymbols(c, symbols) {
    setBusyId(c.id);
    try {
      await api.setConnectorSymbols(c.id, symbols);
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
          <ConnectorCard key={c.id} connector={c} busy={busyId === c.id}
                        onToggle={() => toggle(c)} onSaveSymbols={(syms) => saveSymbols(c, syms)} />
        ))}
      </div>

      <h2 style={{ marginTop: "2rem" }}>Market data providers</h2>
      <p className="muted">
        Pluggable feed adapters that stream a real (or licensed) source into the sharded tickerplants,
        behind the same on/off model as the sims above. <strong>Live</strong> providers run today with an
        API key; <strong>licensed</strong> feeds connect once you plug in your data agreement and
        connectivity &mdash; that&rsquo;s the seam your own exchange feed lands on.
      </p>
      <div className="provider-grid">
        {providers.map((p) => (
          <div className="card provider-card" key={p.name}>
            <div className="provider-header">
              <h3>{p.display_name}</h3>
              <span className={`tier-badge tier-${p.tier}`}>{p.tier}</span>
            </div>
            <div className="provider-meta">{p.coverage}</div>
            <p className="muted">Needs: {p.requires}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function ConnectorCard({ connector: c, busy, onToggle, onSaveSymbols }) {
  const [symbols, setSymbols] = useState(c.symbols || []);
  const [dirty, setDirty] = useState(false);

  useEffect(() => { setSymbols(c.symbols || []); setDirty(false); }, [c.symbols]);

  function change(v) {
    setSymbols(v);
    setDirty(true);
  }

  return (
    <div className="card connector-card">
      <div className="connector-header">
        <h3>{c.name}</h3>
        <StatusBadge status={c.live_status} />
      </div>
      <p className="muted">{c.description}</p>
      <div className="connector-meta">kind: {c.kind}</div>

      <div className="wizard-section-label">Symbol group</div>
      <SymbolPicker value={symbols} onChange={change} placeholder="empty = full universe" />
      <div className="muted" style={{ fontSize: "0.78rem", marginTop: "0.3rem" }}>
        {symbols.length === 0
          ? "Feeding its full built-in universe (default)."
          : `Scoped to ${symbols.length} symbol${symbols.length === 1 ? "" : "s"}.`}
        {c.enabled && " Saving while enabled restarts the feed to apply it (a few seconds of gap)."}
      </div>

      <div className="form-row" style={{ marginTop: "0.6rem" }}>
        <button
          className={c.enabled ? "danger" : "primary"}
          disabled={busy}
          onClick={onToggle}
        >
          {c.enabled ? "Disable" : "Enable"}
        </button>
        <button disabled={busy || !dirty} onClick={() => onSaveSymbols(symbols)}>
          Save symbol group
        </button>
      </div>
    </div>
  );
}
