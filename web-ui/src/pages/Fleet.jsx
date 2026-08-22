import { useEffect, useState } from "react";
import { api } from "../api.js";
import StatusBadge from "../components/StatusBadge.jsx";

const ENVIRONMENTS = ["aws", "azure", "gcp", "onprem"];

// Matches fleet_agent/README.md's "Running it" section exactly - these are
// the real env vars config.py reads (fleet_agent/config.py:33-36), not
// invented ones. AWS/Azure/GCP run against that cluster's own Kubernetes
// context via helm/kubectl (no cloud SDK, no key - see backends.py's
// HelmBackend); on-prem drives docker compose directly.
function fleetAgentInstallCommand(token) {
  const backend = token.environment === "onprem" ? "compose" : "helm";
  const controlPlaneUrl = `${window.location.origin}/api`;
  const lines = [
    `export CONTROL_PLANE_URL=${controlPlaneUrl}`,
    `export ENROLLMENT_TOKEN=${token.enrollment_token}`,
    `export AGENT_ENVIRONMENT=${token.environment}`,
    `export AGENT_BACKEND=${backend}`,
  ];
  if (backend === "helm") {
    lines.push("export HELM_RELEASE=kdb-control-plane", "export HELM_CHART=helm/kdb-control-plane");
  }
  lines.push("python -m fleet_agent  # from a checkout of this repo; see fleet_agent/README.md");
  return lines.join("\n");
}

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
    <div className="page fleet-page">
      <h2>Fleet &amp; environments</h2>
      <p className="muted">
        Each environment is an agent running <strong>in your own</strong> AWS, Azure, GCP, or on-prem cluster
        &mdash; this control plane never sees or stores your cloud credentials. Register one to get a
        one-time enrollment token, run the agent there with the copy-paste command below, then provision
        ticker plant components into it by choosing a shard count &mdash; the agent reconciles the data plane
        in place (<code>helm upgrade --set shardCount=N</code> on AWS/Azure/GCP, <code>docker compose</code>
        on-prem) and reports back here. The agent calls out to this control plane on a heartbeat; this
        control plane never dials in.
      </p>
      {error && <div className="error">{error}</div>}

      <RegisterAgent
        onRegistered={(res) => { setNewToken(res); refresh(); }}
      />

      {newToken && !newToken.error && (
        <div className="card highlight">
          <h3>Agent “{newToken.name}” registered ({newToken.environment})</h3>
          <p className="muted">
            One-time enrollment token &mdash; single-use, expires the moment the agent enrolls. Run this
            <strong> inside the target {newToken.environment === "onprem" ? "on-prem" : newToken.environment.toUpperCase()} cluster</strong>
            (a service account bound to that namespace, so <code>helm</code>/<code>kubectl</code> use the
            ambient cluster context &mdash; no cloud SDK or key needed here):
          </p>
          <pre className="token-box">{fleetAgentInstallCommand(newToken)}</pre>
          <button className="chip" onClick={() => navigator.clipboard?.writeText(fleetAgentInstallCommand(newToken))}>
            Copy command
          </button>
          <button onClick={() => setNewToken(null)}>Dismiss</button>
        </div>
      )}
      {newToken?.error && (
        <div className="card"><div className="error">{newToken.error}</div>
          <button onClick={() => setNewToken(null)}>Dismiss</button></div>
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
