import { useEffect, useState } from "react";
import { api } from "../api.js";

// Admin portal for the C++ feed-handler engine (data-plane/feedhandler-cpp/)
// - a protocol/venue adapter platform (MoldUDP64+ITCH, SBE, FIX, WebSocket+
// JSON) rather than one parser per exchange. This page browses the provider
// catalog (control-api/app/feedhandler_catalog.py, kept in sync with that
// engine's own config/providers/ defaults) and lets a tenant admin activate
// a source with its config + credentials instead of hand-writing JSON.
//
// Credentials are Fernet-encrypted at rest (app/crypto.py) and never
// returned by list/get - only the decrypted "engine config" view (gated
// require_admin, same as every mutation here) ever shows a secret value,
// and only for as long as this modal stays open.
export default function FeedHandlers() {
  const [catalog, setCatalog] = useState([]);
  const [instances, setInstances] = useState([]);
  const [tickhouses, setTickhouses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activating, setActivating] = useState(null); // catalog entry being activated, or null
  const [viewingConfig, setViewingConfig] = useState(null); // {config, secrets} | null

  function refresh() {
    return api.listFeedHandlers().then(setInstances).catch((e) => setError(String(e)));
  }
  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.feedhandlerCatalog().then((r) => setCatalog(r.providers)),
      api.listTickhouses().then(setTickhouses).catch(() => {}), // optional context - a tenant with none yet still gets the full page
      refresh(),
    ])
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  async function toggleEnabled(instance) {
    try {
      await api.updateFeedHandler(instance.id, { ...instance, enabled: !instance.enabled });
      await refresh();
    } catch (e) { setError(String(e)); }
  }

  async function setTickhouse(instance, tickhouseId) {
    try {
      await api.updateFeedHandler(instance.id, { ...instance, tickhouse_id: tickhouseId || null });
      await refresh();
    } catch (e) { setError(String(e)); }
  }

  async function remove(instance) {
    if (!window.confirm(`Remove the ${instance.display_name} feed handler? This only removes it from the admin portal - stop the engine process separately if it's running.`)) return;
    try { await api.deleteFeedHandler(instance.id); await refresh(); }
    catch (e) { setError(String(e)); }
  }

  async function showEngineConfig(instance) {
    try { setViewingConfig({ instance, data: await api.feedHandlerEngineConfig(instance.id) }); }
    catch (e) { setError(String(e)); }
  }

  const activatedKeys = new Set(instances.map((i) => `${i.provider}/${i.feed}`));
  const ready = catalog.filter((e) => e.engine_support !== "catalog_only");
  const future = catalog.filter((e) => e.engine_support === "catalog_only");

  function ProviderRow({ entry }) {
    const key = `${entry.provider}/${entry.feed}`;
    const already = activatedKeys.has(key);
    const isFuture = entry.engine_support === "catalog_only";
    return (
      <div className="activity-row" key={key}>
        <div className="activity-main">
          <div className="activity-title">
            <strong>{entry.display_name}</strong>
            <span className="muted">{entry.protocol_family}</span>
            {entry.tier === "public" ? (
              <span className="live-pill on">no credentials needed</span>
            ) : (
              <span className="live-pill off">licensed / entitlement required</span>
            )}
            {isFuture && <span className="live-pill off">for future integration</span>}
          </div>
          <div className="activity-detail">{entry.coverage}</div>
          <div className="muted" style={{ fontSize: "0.78rem" }}>{entry.requires}</div>
        </div>
        <button className="chip" disabled={already} onClick={() => setActivating(entry)}>
          {already ? "Activated" : isFuture ? "Add to plan" : "Activate"}
        </button>
      </div>
    );
  }

  return (
    <div className="page feedhandlers-page">
      <h2>Feed handlers</h2>
      <p className="muted">
        Protocol and venue adapters for market-data ingestion - MoldUDP64/ITCH, SBE, FIX, and
        WebSocket+JSON, running as the C++ feed-handler engine alongside the tickerplants. Activate a
        source below with its configuration and credentials; publicly-accessible feeds (like Coinbase)
        need no credentials at all, direct exchange feeds (NASDAQ, CME) need a real market-data
        agreement and connectivity before they can go live. Entries marked "for future integration"
        have no working decoder in the engine yet (see each one's "requires" note) - they can be added
        to the plan disabled, but not enabled, until that engine work is done.
      </p>
      {error && <div className="error">{error}</div>}
      {loading && <div className="muted">Loading…</div>}

      {viewingConfig && (
        <EngineConfigModal instance={viewingConfig.instance} data={viewingConfig.data} onClose={() => setViewingConfig(null)} />
      )}

      {activating && (
        <ActivateForm
          entry={activating}
          tickhouses={tickhouses}
          onCancel={() => setActivating(null)}
          onSaved={async () => { setActivating(null); await refresh(); }}
          onError={setError}
        />
      )}

      {!loading && !activating && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h3>Available providers</h3>
          <div className="activity-feed">
            {ready.map((entry) => <ProviderRow entry={entry} key={`${entry.provider}/${entry.feed}`} />)}
          </div>
        </div>
      )}

      {!loading && !activating && future.length > 0 && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <h3>Planned for future integration</h3>
          <p className="muted" style={{ fontSize: "0.85rem" }}>
            Exchanges and vendors not yet wired up in the engine - listed so coverage can be planned
            and tracked ahead of connecting real data.
          </p>
          <div className="activity-feed">
            {future.map((entry) => <ProviderRow entry={entry} key={`${entry.provider}/${entry.feed}`} />)}
          </div>
        </div>
      )}

      {!loading && !activating && (
        <div className="card">
          <h3>Activated sources</h3>
          {instances.length === 0 && <p className="muted">No feed handlers activated yet.</p>}
          {instances.map((i) => {
            const linkedTickhouse = tickhouses.find((t) => t.id === i.tickhouse_id);
            return (
              <div key={i.id} className="form-row" style={{ alignItems: "center", padding: "0.4rem 0", borderTop: "1px solid var(--border)" }}>
                <div style={{ flex: 1, minWidth: "12rem" }}>
                  <strong>{i.display_name}</strong>{" "}
                  <span className={`live-pill ${i.enabled ? "on" : "off"}`}>{i.enabled ? "enabled" : "disabled"}</span>{" "}
                  {i.has_secrets && <span className="live-pill off">has credentials</span>}
                  <div className="muted" style={{ fontSize: "0.8rem" }}>
                    {i.provider}/{i.feed} · {i.config.venue_adapter} via {i.config.transport?.type}
                  </div>
                  <div className="muted" style={{ fontSize: "0.78rem" }}>
                    {linkedTickhouse ? `→ TickHouse "${linkedTickhouse.name}"` : "not attached to a TickHouse"}
                  </div>
                </div>
                <select value={i.tickhouse_id || ""} onChange={(e) => setTickhouse(i, e.target.value ? Number(e.target.value) : null)}>
                  <option value="">no TickHouse</option>
                  {tickhouses.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                <button className="chip" onClick={() => toggleEnabled(i)}>{i.enabled ? "Disable" : "Enable"}</button>
                <button className="chip" onClick={() => showEngineConfig(i)}>View config</button>
                <button className="chip" onClick={() => remove(i)}>Remove</button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ActivateForm({ entry, tickhouses, onCancel, onSaved, onError }) {
  const [secrets, setSecrets] = useState(() => Object.fromEntries(entry.credentials_required.map((f) => [f, ""])));
  const [tickhouseId, setTickhouseId] = useState("");
  const [saving, setSaving] = useState(false);
  const isFuture = entry.engine_support === "catalog_only";

  async function save() {
    const missing = entry.credentials_required.filter((f) => !secrets[f]?.trim());
    if (missing.length) { onError(`Missing required credential(s): ${missing.join(", ")}`); return; }
    setSaving(true);
    try {
      await api.createFeedHandler({
        provider: entry.provider, feed: entry.feed, display_name: entry.display_name,
        enabled: !isFuture, config: entry.default_config, secrets,
        tickhouse_id: tickhouseId ? Number(tickhouseId) : null,
      });
      onSaved();
    } catch (e) { onError(String(e).replace(/^Error:\s*/, "")); }
    finally { setSaving(false); }
  }

  return (
    <div className="card" style={{ marginBottom: "1rem" }}>
      <h3>{isFuture ? "Add to plan: " : "Activate "}{entry.display_name}</h3>
      <p className="muted">{entry.coverage}</p>
      <p className="muted" style={{ fontSize: "0.85rem" }}>{entry.requires}</p>
      {isFuture && (
        <div className="ops-banner warn">
          This venue has no working decoder in the engine yet - saving this creates it disabled, purely
          to track it as planned coverage. It can't be enabled until that engine work is done.
        </div>
      )}

      <div className="wizard-section-label">TickHouse (optional)</div>
      <div className="form-row wrap">
        <select value={tickhouseId} onChange={(e) => setTickhouseId(e.target.value)}>
          <option value="">not attached to a TickHouse yet</option>
          {tickhouses.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </div>

      {entry.credentials_required.length === 0 ? (
        <p className="muted">No credentials required for this source.</p>
      ) : (
        <>
          <div className="wizard-section-label">Credentials</div>
          <div className="form-row wrap">
            {entry.credentials_required.map((f) => (
              <input key={f} type={f.toLowerCase().includes("password") ? "password" : "text"} placeholder={f}
                     value={secrets[f] || ""} onChange={(e) => setSecrets({ ...secrets, [f]: e.target.value })} />
            ))}
          </div>
        </>
      )}

      {Object.keys(entry.default_config.transport || {}).some((k) => String(entry.default_config.transport[k]).startsWith("REPLACE")) && (
        <div className="ops-banner warn" style={{ marginTop: "0.75rem" }}>
          This provider's default transport coordinates are placeholders (multicast group/host/port) -
          activating starts it disabled until you edit the real values via "View config" / the engine's
          FH_CONFIG_JSON.
        </div>
      )}

      <div className="query-actions" style={{ marginTop: "0.75rem" }}>
        <button className="primary" disabled={saving} onClick={save}>
          {saving ? "Saving…" : isFuture ? "Add to plan" : "Activate"}
        </button>
        <button className="chip" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function EngineConfigModal({ instance, data, onClose }) {
  return (
    <div className="card" style={{ marginBottom: "1rem", border: "1px solid var(--accent)" }}>
      <div className="section-head">
        <h3>{instance.display_name} - engine config</h3>
        <button className="chip" onClick={onClose}>Close</button>
      </div>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        The fully-resolved config for a real engine deployment - save the "config" object as
        FH_CONFIG_JSON and the "secrets" object as FH_SECRETS_JSON_INLINE (see
        data-plane/feedhandler-cpp/main.cpp's live mode). Decrypted credentials are shown here only
        while this panel is open.
      </p>
      <pre className="q-editor" style={{ maxHeight: "20rem", overflow: "auto" }}>
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}
