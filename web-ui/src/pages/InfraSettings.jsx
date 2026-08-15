import { useEffect, useState } from "react";
import { api } from "../api.js";

// Platform-admin management of reusable, NON-SECRET infrastructure
// coordinate bundles (control-api/app/routers/infra_profiles.py) - region,
// VPC/subnet, namespace, storage class, etc. Picking one while creating a
// TickHouse (see TickHouses.jsx) copies its values in once, so an admin
// doesn't retype the same region/VPC for every new cluster.
//
// Deliberately no credential fields anywhere on this page - this platform's
// fleet agent reaches its cloud/cluster with AMBIENT identity (its node's
// IAM role, its pod's kube service account), never credentials the control
// plane hands it. See app/models.py's InfraProfile docstring for the full
// rationale. Backend enforces platform_admin on every mutation regardless
// of whether this tab is visible (App.jsx hides it client-side only).
const PROVIDER_LABELS = { aws: "AWS", azure: "Azure", gcp: "Google Cloud", onprem: "On-premises" };

const emptyForm = (provider) => ({
  name: "", description: "", provider, config: {}, is_default: false, enabled: true,
});

export default function InfraSettings() {
  const [meta, setMeta] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null); // null = not editing, "new" = creating, or a profile id

  function refresh() {
    return api.listInfraProfiles().then(setProfiles).catch((e) => setError(String(e)));
  }
  useEffect(() => {
    setLoading(true);
    Promise.all([api.infraProfilesMeta().then(setMeta), refresh()])
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  async function remove(p) {
    if (!window.confirm(`Delete profile "${p.name}"? TickHouses already created from it keep their own copy of these values - this only removes it from future use.`)) return;
    try { await api.deleteInfraProfile(p.id); await refresh(); }
    catch (e) { setError(String(e)); }
  }

  const grouped = (meta?.providers || []).map((provider) => ({
    provider, items: profiles.filter((p) => p.provider === provider),
  }));

  return (
    <div className="page">
      <h2>Infrastructure settings</h2>
      <p className="muted">
        Reusable, named bundles of non-secret deployment coordinates (region, VPC/subnet, namespace,
        storage class) per provider — configure once, then TickHouse creation can auto-load the
        default profile for whichever provider is selected instead of retyping it every time.
        Credentials never live here: this platform's agents reach your cloud/cluster with their own
        ambient identity, not keys the control plane hands them — see the Fleet page.
      </p>
      {error && <div className="error">{error}</div>}
      {loading && <div className="muted">Loading…</div>}

      {!loading && meta && (
        <>
          {editing && (
            <ProfileForm
              meta={meta}
              initial={editing === "new" ? emptyForm(meta.providers[0]) : profiles.find((p) => p.id === editing)}
              onCancel={() => setEditing(null)}
              onSaved={async () => { setEditing(null); await refresh(); }}
              onError={setError}
            />
          )}

          {!editing && (
            <button className="primary" style={{ marginBottom: "1rem" }} onClick={() => setEditing("new")}>
              + New profile
            </button>
          )}

          {grouped.map(({ provider, items }) => (
            <div key={provider} className="card" style={{ marginBottom: "1rem" }}>
              <h3>{PROVIDER_LABELS[provider] || provider}</h3>
              {items.length === 0 && <p className="muted">No profiles yet.</p>}
              {items.map((p) => (
                <div key={p.id} className="form-row" style={{ alignItems: "center", padding: "0.4rem 0", borderTop: "1px solid var(--border)" }}>
                  <div style={{ flex: 1, minWidth: "12rem" }}>
                    <strong>{p.name}</strong>{" "}
                    {p.is_default && <span className="live-pill on">DEFAULT</span>}{" "}
                    {!p.enabled && <span className="live-pill off">disabled</span>}
                    <div className="muted" style={{ fontSize: "0.8rem" }}>{p.description || "no description"}</div>
                    <div className="muted" style={{ fontSize: "0.78rem" }}>
                      {Object.entries(p.config).map(([k, v]) => `${k}=${v}`).join(", ") || "no coordinates set"}
                    </div>
                  </div>
                  <button className="chip" onClick={() => setEditing(p.id)}>Edit</button>
                  <button className="chip" onClick={() => remove(p)}>Delete</button>
                </div>
              ))}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function ProfileForm({ meta, initial, onCancel, onSaved, onError }) {
  const [form, setForm] = useState(initial);
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const cfgFields = meta.config_fields[form.provider] || [];

  async function save() {
    if (!form.name.trim()) { onError("Name is required"); return; }
    setSaving(true);
    try {
      if (initial.id) await api.updateInfraProfile(initial.id, form);
      else await api.createInfraProfile(form);
      onSaved();
    } catch (e) { onError(String(e).replace(/^Error:\s*/, "")); }
    finally { setSaving(false); }
  }

  return (
    <div className="card" style={{ marginBottom: "1rem" }}>
      <h3>{initial.id ? `Edit ${initial.name}` : "New infrastructure profile"}</h3>
      <div className="form-row wrap">
        <input placeholder="name, e.g. AWS Production" value={form.name} onChange={(e) => set("name", e.target.value)} />
        <select value={form.provider} onChange={(e) => { set("provider", e.target.value); set("config", {}); }}>
          {meta.providers.map((p) => <option key={p} value={p}>{PROVIDER_LABELS[p] || p}</option>)}
        </select>
      </div>
      <div className="form-row wrap">
        <input placeholder="description (optional)" style={{ minWidth: "20rem" }}
               value={form.description} onChange={(e) => set("description", e.target.value)} />
      </div>

      {cfgFields.length > 0 && (
        <>
          <div className="wizard-section-label">{form.provider} coordinates</div>
          <div className="form-row wrap">
            {cfgFields.map((f) => (
              <input key={f} placeholder={f} value={form.config[f] || ""}
                     onChange={(e) => set("config", { ...form.config, [f]: e.target.value })} />
            ))}
          </div>
        </>
      )}

      <div className="form-row" style={{ marginTop: "0.5rem" }}>
        <label><input type="checkbox" checked={form.is_default}
                      onChange={(e) => set("is_default", e.target.checked)} /> Default for {form.provider}</label>
        <label><input type="checkbox" checked={form.enabled}
                      onChange={(e) => set("enabled", e.target.checked)} /> Enabled</label>
      </div>

      <div className="query-actions" style={{ marginTop: "0.75rem" }}>
        <button className="primary" disabled={saving} onClick={save}>{saving ? "Saving…" : "Save"}</button>
        <button className="chip" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
