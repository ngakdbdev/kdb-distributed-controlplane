import { useEffect, useState } from "react";
import { api } from "../api.js";
import StatusBadge from "../components/StatusBadge.jsx";

const ENVIRONMENTS = ["aws", "azure", "gcp", "onprem"];

export default function Fleet() {
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState("");
  const [newToken, setNewToken] = useState(null);

  async function refresh() {
    try {
      setAgents(await api.listAgents());
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="page">
      <h2>Fleet &amp; environments</h2>
      <p className="muted">
        Each environment is an agent running in your own AWS, Azure, GCP, or on-prem cluster. Register one
        to get a one-time enrollment token, install the agent there, then provision ticker plant components
        into it by choosing a shard count &mdash; the agent reconciles the data plane in place
        (<code>helm upgrade --set shardCount=N</code>) and reports back here.
      </p>
      {error && <div className="error">{error}</div>}

      <RegisterAgent
        onRegistered={(res) => { setNewToken(res); refresh(); }}
      />

      {newToken && (
        <div className="card" style={{ borderColor: "var(--accent, #3a7)" }}>
          <h3>Agent “{newToken.name}” registered ({newToken.environment})</h3>
          <p className="muted">
            One-time enrollment token &mdash; hand this to whoever installs the agent in the target cluster.
            It is single-use and expires the moment the agent enrolls.
          </p>
          <pre className="token-box">{newToken.enrollment_token}</pre>
          <button onClick={() => setNewToken(null)}>Dismiss</button>
        </div>
      )}

      <div className="agent-list">
        {agents.length === 0 && <p className="muted">No environments registered yet.</p>}
        {agents.map((a) => <AgentCard key={a.id} agent={a} onError={setError} />)}
      </div>
    </div>
  );
}

function RegisterAgent({ onRegistered }) {
  const [name, setName] = useState("");
  const [environment, setEnvironment] = useState("aws");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      onRegistered(await api.createAgent(name.trim(), environment));
      setName("");
    } catch (err) {
      onRegistered({ error: String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h3>Register an environment</h3>
      <div className="form-row">
        <input
          placeholder="name, e.g. acme-aws-prod"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select value={environment} onChange={(e) => setEnvironment(e.target.value)}>
          {ENVIRONMENTS.map((env) => <option key={env} value={env}>{env}</option>)}
        </select>
        <button className="primary" disabled={busy} onClick={submit}>Register</button>
      </div>
    </div>
  );
}

function AgentCard({ agent, onError }) {
  const [shardCount, setShardCount] = useState(2);
  const [commands, setCommands] = useState([]);
  const [busy, setBusy] = useState(false);

  async function refreshCommands() {
    try {
      setCommands(await api.listAgentCommands(agent.id, 8));
    } catch (err) {
      onError(String(err));
    }
  }

  useEffect(() => {
    refreshCommands();
    const id = setInterval(refreshCommands, 3000);
    return () => clearInterval(id);
  }, [agent.id]);

  async function provision() {
    setBusy(true);
    try {
      await api.provision(agent.id, Number(shardCount));
      await refreshCommands();
    } catch (err) {
      onError(String(err));
    } finally {
      setBusy(false);
    }
  }

  const online = agent.status === "online";

  return (
    <div className="card agent-card">
      <div className="agent-header">
        <h3>{agent.name}</h3>
        <span className={`env-badge env-${agent.environment}`}>{agent.environment}</span>
        <StatusBadge status={online ? "running" : "stopped"} />
      </div>

      <div className="form-row">
        <label>Shards:&nbsp;</label>
        <input
          type="number" min="1" max="26" value={shardCount}
          onChange={(e) => setShardCount(e.target.value)}
          style={{ width: "5rem" }}
        />
        <button className="primary" disabled={busy || !online} onClick={provision}>
          Provision ticker plant
        </button>
        {!online && <span className="muted">&nbsp;(agent offline &mdash; can’t dispatch)</span>}
      </div>

      <div className="command-log">
        <div className="muted">Recent provisioning jobs</div>
        {commands.length === 0 && <div className="muted">none yet</div>}
        {commands.map((c) => (
          <div className="command-row" key={c.id}>
            <span className="command-action">{c.action}</span>
            <StatusBadge status={jobStatus(c.status)} />
            <span className="muted">{c.result_detail || c.status}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// map command status onto the shared StatusBadge vocabulary
function jobStatus(status) {
  if (status === "success") return "running";
  if (status === "failure") return "stopped";
  return "pending";
}
