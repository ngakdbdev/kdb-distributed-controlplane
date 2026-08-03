import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function Subscribers({ onNavigate }) {
  const [subscribers, setSubscribers] = useState([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ name: "", table: "trade", role: "read-only" });
  const [busy, setBusy] = useState(false);
  const [tpUp, setTpUp] = useState(null);   // are any tickerplants live?

  async function refresh() {
    try {
      setSubscribers(await api.listSubscribers());
    } catch (err) {
      setError(String(err));
    }
    try {
      const topo = await api.topologyStatus();
      const tps = Object.entries(topo).filter(([n]) => n.startsWith("tp-"));
      setTpUp(tps.some(([, st]) => st === "running"));
    } catch { setTpUp(false); }
  }

  useEffect(() => { refresh(); }, []);

  async function addSubscriber(e) {
    e.preventDefault();
    if (!form.name.trim()) return;
    setBusy(true);
    try {
      await api.addSubscriber(form);
      setForm({ name: "", table: "trade", role: "read-only" });
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id) {
    setBusy(true);
    try {
      await api.removeSubscriber(id);
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h2>Subscribers & entitlements</h2>
      <p className="muted">Who or what may subscribe to which table, and whether a live stream exists to subscribe to.</p>
      {tpUp === false && (
        <div className="ops-banner warn">
          <div><strong>No live tickerplants or data streams.</strong> Subscribers can be defined, but nothing will be delivered until a tickerplant is running and a feed is publishing.</div>
          <button className="primary" onClick={() => onNavigate?.("topology")}>Open Topology →</button>
        </div>
      )}
      {error && <div className="error">{error}</div>}

      <form className="card inline-form" onSubmit={addSubscriber}>
        <input
          placeholder="subscriber name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
        <select value={form.table} onChange={(e) => setForm({ ...form, table: e.target.value })}>
          <option value="trade">trade</option>
          <option value="risk">risk</option>
        </select>
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          <option value="read-only">read-only</option>
          <option value="read-write">read-write</option>
        </select>
        <button className="primary" type="submit" disabled={busy}>Add</button>
      </form>

      <table className="data-table">
        <thead><tr><th>Name</th><th>Table</th><th>Role</th><th>Active</th><th></th></tr></thead>
        <tbody>
          {subscribers.length === 0 && (
            <tr><td colSpan={5} className="muted" style={{ padding: "1rem" }}>No subscribers defined yet.</td></tr>
          )}
          {subscribers.map((s) => (
            <tr key={s.id}>
              <td>{s.name}</td>
              <td>{s.table}</td>
              <td>{s.role}</td>
              <td>{s.active ? "yes" : "no"}</td>
              <td><button className="danger" onClick={() => remove(s.id)} disabled={busy}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
