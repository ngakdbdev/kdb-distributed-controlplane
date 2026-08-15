import { useEffect, useState } from "react";
import { api } from "../api.js";

// Admin (tenant_admin) management of logins within their own tenant
// (control-api/app/routers/users.py). Three assignable roles:
//   - Admin (tenant_admin)     - full tenant access, same as whoever's
//                                creating the account here.
//   - Functional User          - day-to-day trading/ops (Markets, Orders,
//                                Portfolio, Bot, Query); can trade by
//                                default.
//   - Quant Analyst            - research/analysis (Query, Predictive
//                                Signals); can't trade by default, but
//                                that's a toggle, not a hard wall.
// Backend enforces require_admin regardless of whether this tab is visible
// (App.jsx hides it client-side only, as a courtesy).
const ROLES = [
  { id: "tenant_admin", label: "Admin", hint: "Full access within this tenant, including this page." },
  { id: "functional_user", label: "Functional User", hint: "Trading & operations - Markets, Orders, Portfolio, Bot, Query. Can place trades by default." },
  { id: "quant_analyst", label: "Quant Analyst", hint: "Research & analysis - Query, Predictive Signals. Can't place trades by default." },
];
export default function Users() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  function refresh() {
    return api.listUsers().then(setUsers).catch((e) => setError(String(e)));
  }
  useEffect(() => { setLoading(true); refresh().finally(() => setLoading(false)); }, []);

  async function toggle(u, patch) {
    try { await api.updateUser(u.id, patch); await refresh(); }
    catch (e) { setError(String(e).replace(/^Error:\s*/, "")); }
  }

  return (
    <div className="page">
      <h2>Users</h2>
      <p className="muted">
        People with a login into this tenant, and what they can do. Admin has full access (including this
        page); Functional User covers day-to-day trading and monitoring; Quant Analyst covers research and
        analysis. Trading rights are a separate toggle per person, not locked to the role.
      </p>
      {error && <div className="error">{error}</div>}
      {loading && <div className="muted">Loading…</div>}

      {!loading && (
        <>
          {!creating && <button className="primary" style={{ marginBottom: "1rem" }} onClick={() => setCreating(true)}>+ New user</button>}
          {creating && (
            <CreateForm onCancel={() => setCreating(false)}
                       onCreated={async () => { setCreating(false); await refresh(); }}
                       onError={setError} />
          )}

          <div className="card">
            <table className="data-table">
              <thead><tr><th>email</th><th>role</th><th>can trade</th><th>active</th><th></th></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>{u.email}</td>
                    <td>
                      <select value={u.role} onChange={(e) => toggle(u, { role: e.target.value })}>
                        {ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                      </select>
                    </td>
                    <td>
                      <input type="checkbox" checked={u.can_trade}
                             onChange={(e) => toggle(u, { can_trade: e.target.checked })} />
                    </td>
                    <td>
                      <span className={`live-pill ${u.active ? "on" : "off"}`}>{u.active ? "active" : "deactivated"}</span>
                    </td>
                    <td>
                      <button className="chip" onClick={() => toggle(u, { active: !u.active })}>
                        {u.active ? "Deactivate" : "Reactivate"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function CreateForm({ onCancel, onCreated, onError }) {
  const [form, setForm] = useState({ email: "", password: "", role: "functional_user" });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function save() {
    if (!form.email.trim() || form.password.length < 8) {
      onError("Email is required and password must be at least 8 characters"); return;
    }
    setSaving(true);
    try { await api.createUser(form); onCreated(); }
    catch (e) { onError(String(e).replace(/^Error:\s*/, "")); }
    finally { setSaving(false); }
  }

  return (
    <div className="card" style={{ marginBottom: "1rem" }}>
      <h3>New user</h3>
      <div className="form-row wrap">
        <input placeholder="email" value={form.email} onChange={(e) => set("email", e.target.value)} />
        <input placeholder="temporary password (min 8 chars)" type="password" value={form.password}
               onChange={(e) => set("password", e.target.value)} />
        <select value={form.role} onChange={(e) => set("role", e.target.value)}>
          {ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
        </select>
      </div>
      <div className="muted" style={{ fontSize: "0.78rem" }}>{ROLES.find((r) => r.id === form.role)?.hint}</div>
      <div className="query-actions" style={{ marginTop: "0.75rem" }}>
        <button className="primary" disabled={saving} onClick={save}>{saving ? "Creating…" : "Create"}</button>
        <button className="chip" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
