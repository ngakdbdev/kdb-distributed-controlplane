import { useEffect, useState } from "react";
import { api } from "../api.js";
import StatusBadge from "../components/StatusBadge.jsx";
import SymbolPicker from "../components/SymbolPicker.jsx";

export default function TickHouses() {
  const [meta, setMeta] = useState(null);
  const [clusters, setClusters] = useState([]);
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState("");

  async function refresh() {
    try { setClusters(await api.listTickhouses()); } catch (err) { setError(String(err)); }
  }
  useEffect(() => {
    api.tickhouseMeta().then(setMeta).catch((e) => setError(String(e)));
    api.listAgents().then(setAgents).catch(() => {});
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="page">
      <h2>TickHouses</h2>
      <p className="muted">
        Define a tick cluster declaratively &mdash; deployment target (AWS/Azure/GCP/on-prem) with its
        cloud &amp; Kubernetes config, a throughput/latency profile, and shards either by letter range
        (<code>a-d, e-h</code>) or by explicit symbols you pick. Component hardware is auto-tuned; review,
        then provision end-to-end through your agent.
      </p>
      {error && <div className="error">{error}</div>}
      {meta && <CreateWizard meta={meta} onCreated={refresh} onError={setError} />}

      <h3 style={{ marginTop: "1.5rem" }}>Defined clusters</h3>
      {clusters.length === 0 && <p className="muted">None yet.</p>}
      {clusters.map((c) => (
        <ClusterCard key={c.id} cluster={c} agents={agents} onChange={refresh} onError={setError} />
      ))}
    </div>
  );
}

function CreateWizard({ meta, onCreated, onError }) {
  const [form, setForm] = useState({
    name: "", location: meta.clouds[0], os: meta.os_types[0], profile: meta.profiles[0],
    shard_ranges: "a-m, n-z", idb: false, ldap_ref: "",
    sharding_policy: "letter-range",
  });
  const [config, setConfig] = useState({});
  const [symShards, setSymShards] = useState([{ label: "shard-1", symbols: [] }]);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const cfgFields = (meta.config_fields && meta.config_fields[form.location]) || [];

  function body() {
    const b = { ...form, target_config: config };
    if (form.sharding_policy === "explicit-symbols") {
      b.shard_symbols = symShards.filter((s) => s.symbols.length);
    }
    return b;
  }

  async function doPreview() {
    setBusy(true);
    try { setPreview(await api.previewTickhouse(body())); }
    catch (err) { onError(String(err)); }
    finally { setBusy(false); }
  }
  async function doCreate() {
    setBusy(true);
    try { await api.createTickhouse(body()); setPreview(null); onCreated(); }
    catch (err) { onError(String(err)); }
    finally { setBusy(false); }
  }

  return (
    <div className="card">
      <h3>Create a TickHouse</h3>
      <div className="form-row">
        <input placeholder="name, e.g. acme-emea" value={form.name} onChange={(e) => set("name", e.target.value)} />
        <select value={form.location} onChange={(e) => { set("location", e.target.value); setConfig({}); }}>
          {meta.clouds.map((c) => <option key={c}>{c}</option>)}
        </select>
        <select value={form.os} onChange={(e) => set("os", e.target.value)}>
          {meta.os_types.map((o) => <option key={o}>{o}</option>)}
        </select>
        <select value={form.profile} onChange={(e) => set("profile", e.target.value)}>
          {meta.profiles.map((p) => <option key={p}>{p}</option>)}
        </select>
      </div>

      {cfgFields.length > 0 && (
        <>
          <div className="wizard-section-label">
            {form.location} / Kubernetes config <span className="required-marker">* all required</span>
          </div>
          <div className="form-row wrap">
            {cfgFields.map((f) => {
              const missing = !String(config[f] || "").trim();
              return (
                <input key={f} placeholder={`${f} *`} value={config[f] || ""}
                       title={`${f} is required to provision on ${form.location}`}
                       className={missing ? "field-required-missing" : ""}
                       onChange={(e) => setConfig((c) => ({ ...c, [f]: e.target.value }))} />
              );
            })}
          </div>
          <div className="muted" style={{ fontSize: "0.78rem" }}>
            Non-secret coordinates only. Credentials (keys, kubeconfig tokens) stay in the agent&rsquo;s secret store.
            These are the account/cluster the agent already running there will deploy into &mdash; this
            does not create a new AWS/Azure/GCP cluster for you; enroll an agent in an existing cluster first
            (see the Fleet page).
          </div>
        </>
      )}

      <div className="wizard-section-label">Sharding</div>
      <div className="form-row">
        <select value={form.sharding_policy} onChange={(e) => set("sharding_policy", e.target.value)}>
          {(meta.sharding_policies || ["letter-range"]).map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        {form.sharding_policy === "letter-range" ? (
          <input placeholder="a-d, e-h, i-j" value={form.shard_ranges}
                 onChange={(e) => set("shard_ranges", e.target.value)} style={{ minWidth: "16rem" }} />
        ) : (
          <span className="muted">assign symbols to each shard below</span>
        )}
        <label><input type="checkbox" checked={form.idb} onChange={(e) => set("idb", e.target.checked)} /> IDB</label>
        <input placeholder="LDAP binding (optional)" value={form.ldap_ref}
               onChange={(e) => set("ldap_ref", e.target.value)} />
      </div>

      {form.sharding_policy === "explicit-symbols" && (
        <div className="sym-shards">
          {symShards.map((s, i) => (
            <div className="sym-shard-row" key={i}>
              <input className="sym-shard-label" value={s.label}
                     onChange={(e) => setSymShards((cur) => cur.map((x, j) => j === i ? { ...x, label: e.target.value } : x))} />
              <SymbolPicker value={s.symbols}
                onChange={(v) => setSymShards((cur) => cur.map((x, j) => j === i ? { ...x, symbols: v } : x))} />
              <button className="chip" onClick={() => setSymShards((cur) => cur.filter((_, j) => j !== i))}>remove</button>
            </div>
          ))}
          <button className="chip" onClick={() => setSymShards((cur) => [...cur, { label: `shard-${cur.length + 1}`, symbols: [] }])}>
            + add shard
          </button>
        </div>
      )}

      <div className="form-row">
        <button disabled={busy || !form.name} onClick={doPreview}>Auto-tune &amp; review</button>
        <button className="primary" disabled={busy || !preview || (preview.problems || []).length} onClick={doCreate}>Create</button>
      </div>

      {preview && (
        <div className="command-log">
          {(preview.problems || []).length > 0 && <div className="error">{preview.problems.join("; ")}</div>}
          <div className="muted">Auto-tuned components ({preview.spec.shards.length} shards, {preview.spec.sharding_policy}):</div>
          <table className="spec-table">
            <thead><tr><th>component</th><th>instance</th><th>vCPU</th><th>mem</th><th>disk</th><th>nic</th></tr></thead>
            <tbody>
              {preview.spec.components.map((c) => (
                <tr key={c.type}>
                  <td>{c.type}</td>
                  <td>{c.hardware.instance_type || "(bare metal)"}</td>
                  <td>{c.hardware.vcpus}</td>
                  <td>{c.hardware.memory_gb}G</td>
                  <td>{c.hardware.disk_gb}G {c.hardware.disk_tier}</td>
                  <td>{c.hardware.nic}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ClusterCard({ cluster, agents, onChange, onError }) {
  const matching = agents.filter((a) => a.environment === cluster.location);
  const [agentId, setAgentId] = useState(matching[0]?.id || "");
  const [busy, setBusy] = useState(false);

  async function provision() {
    if (!agentId) return;
    setBusy(true);
    try { await api.provisionTickhouse(cluster.id, Number(agentId)); onChange(); }
    catch (err) { onError(String(err)); } finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true);
    try { await api.deleteTickhouse(cluster.id); onChange(); }
    catch (err) { onError(String(err)); } finally { setBusy(false); }
  }
  const st = { defined: "pending", provisioning: "pending", running: "running", failed: "stopped" }[cluster.status] || "pending";

  return (
    <div className="card">
      <div className="agent-header">
        <h3>{cluster.name}</h3>
        <span className={`env-badge env-${cluster.location}`}>{cluster.location}</span>
        <span className="tier-badge tier-live">{cluster.profile}</span>
        <StatusBadge status={st} />
        <span className="muted">{cluster.status}</span>
      </div>
      <div className="form-row">
        <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
          <option value="">{matching.length ? "select agent" : `no ${cluster.location} agent`}</option>
          {matching.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <button className="primary" disabled={busy || !agentId} onClick={provision}>Provision</button>
        <button disabled={busy} onClick={remove}>Delete</button>
      </div>
    </div>
  );
}
