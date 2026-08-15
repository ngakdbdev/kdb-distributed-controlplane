import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import Sparkline from "../components/Sparkline.jsx";
import { classifyShardSync, SYNC_STAGES } from "../lib/syncProgress.js";
import { fmt } from "../lib/tradingCore.js";

const POLL_MS = 3000;
const POLICY_KEY = "vantik_autoscale_policy_v1";
const SAMPLE_WINDOW = 5;      // consecutive samples that must agree before recommending a change
const COOLDOWN_MS = 5 * 60 * 1000;

function loadPolicy() {
  try {
    const saved = JSON.parse(localStorage.getItem(POLICY_KEY) || "null");
    if (saved) return saved;
  } catch { /* corrupt/old value - fall through to defaults */ }
  return {
    targetMsgsPerShard: 400, scaleUpPct: 80, scaleDownPct: 20, minShards: 1, maxShards: 8, autoApply: false,
    // gateway is a separate, horizontally-scalable tier from shard count (see
    // helm values.yaml's gateway.* comment) - its own target/bounds, applied
    // via the same provision command with shardCount left unchanged.
    gatewayTargetReqsPerSec: 20, gatewayMinReplicas: 1, gatewayMaxReplicas: 6,
  };
}

// there's no live "how many gateway replicas are running right now" signal
// this page can read (no endpoint reports it), so it's operator-entered -
// defaults to 1 (this chart's own default, see values.yaml gateway.replicaCount)
// and persists across visits so re-entering it every time isn't necessary.
const GW_REPLICAS_KEY = "vantik_autoscale_current_gateway_replicas";

// Reads REAL live metrics (/metrics/snapshot, already used by Overview/
// Metrics) and REAL current topology (/topology/status, already used by
// Topology), computes a scale recommendation entirely client-side (same
// pattern Alerts.jsx already uses for synthesized alerts - no new backend
// code, so nothing about the existing, tested endpoints changes), and wires
// "Apply" to the fleet provisioning API that already exists and is already
// used by the Fleet page. Auto-apply defaults OFF: changing shard count is a
// real topology change (new shards start empty, existing routing shifts),
// not something to fire unattended without an operator opting in.
export default function Autoscale() {
  const [policy, setPolicy] = useState(loadPolicy);
  const [topo, setTopo] = useState({});
  const [rateHistory, setRateHistory] = useState([]);
  const [samples, setSamples] = useState([]); // rolling utilization% window
  const [agents, setAgents] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [applying, setApplying] = useState(false);
  const [lastApplied, setLastApplied] = useState(() => Number(localStorage.getItem("vantik_autoscale_last_applied") || 0));
  const [error, setError] = useState("");
  const [syncStatuses, setSyncStatuses] = useState([]);
  const [gwReqHistory, setGwReqHistory] = useState([]);
  const [gwReqRate, setGwReqRate] = useState(0);
  const [currentGatewayReplicas, setCurrentGatewayReplicas] = useState(
    () => Number(localStorage.getItem(GW_REPLICAS_KEY) || 1));
  const [gwApplying, setGwApplying] = useState(false);
  const prevRef = useRef(null);
  const lagHistoryRef = useRef(new Map());

  useEffect(() => { localStorage.setItem(POLICY_KEY, JSON.stringify(policy)); }, [policy]);
  useEffect(() => { localStorage.setItem(GW_REPLICAS_KEY, String(currentGatewayReplicas)); }, [currentGatewayReplicas]);
  useEffect(() => { api.listAgents().then(setAgents).catch(() => setAgents([])); }, []);

  useEffect(() => {
    let closed = false;
    async function pull() {
      try {
        const [snap, status, qhist] = await Promise.all([
          api.metricsSnapshot(), api.topologyStatus(), api.queryHistory(100).catch(() => ({ entries: [] })),
        ]);
        if (closed) return;
        setTopo(status);
        // gateway request rate: real query-path load, counted from actual
        // executions in the last 10s window (query_profile.py's history -
        // every gateway-routed query records here) rather than a synthetic
        // signal. Window-based, not diffed against a running total, so it's
        // unaffected by control-api restarts resetting query_profile's
        // in-memory counter (documented in query_profile.py itself).
        const nowIso = Date.now();
        const recentReqs = (qhist.entries || []).filter((e) => {
          const t = Date.parse(e.timestamp);
          return Number.isFinite(t) && nowIso - t <= 10000;
        });
        const reqRate = recentReqs.length / 10;
        setGwReqRate(reqRate);
        setGwReqHistory((prev) => [...prev, reqRate].slice(-40));
        const total = (snap.componentMetrics || []).reduce((sum, row) => sum + (Number(row.tpRecv) || 0), 0);
        const now = Date.now();
        let rate = 0;
        if (prevRef.current && total >= prevRef.current.total) {
          const dt = (now - prevRef.current.time) / 1000;
          if (dt > 0) rate = (total - prevRef.current.total) / dt;
        }
        prevRef.current = { total, time: now };
        const shardIds = [...new Set(Object.keys(status).map((n) => (n.match(/-(s\d+)$/) || [])[1]).filter(Boolean))].sort();
        const shardCount = shardIds.length || 1;
        const perShard = rate / shardCount;
        const utilPct = (perShard / Math.max(1, policy.targetMsgsPerShard)) * 100;
        setRateHistory((prev) => [...prev, rate].slice(-40));
        setSamples((prev) => [...prev, { utilPct, shardCount, rate, perShard }].slice(-SAMPLE_WINDOW));
        setSyncStatuses(shardIds.map((sid) => classifyShardSync(sid, {
          topo: status, componentMetrics: snap.componentMetrics, transitLag: snap.transitLag, historyMap: lagHistoryRef.current,
        })));
      } catch { /* best effort - keep last good snapshot */ }
    }
    pull();
    const id = setInterval(pull, POLL_MS);
    return () => { closed = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [policy.targetMsgsPerShard]);

  const svc = Object.entries(topo);
  const shardCount = new Set(svc.map(([n]) => (n.match(/-s(\d+)$/) || [])[1]).filter((x) => x != null)).size || 0;
  const latest = samples[samples.length - 1];
  const cooldownRemaining = Math.max(0, COOLDOWN_MS - (Date.now() - lastApplied));
  const inCooldown = cooldownRemaining > 0;

  const rec = computeRecommendation(samples, shardCount, policy);
  const gwRec = computeGatewayRecommendation(gwReqRate, currentGatewayReplicas, policy);

  useEffect(() => {
    if (!policy.autoApply || rec.action === "hold" || inCooldown || applying || !selectedAgent) return;
    apply(rec.targetShardCount, `auto-apply: ${rec.reason}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rec.action, rec.targetShardCount, policy.autoApply, inCooldown, selectedAgent]);

  async function apply(targetShardCount, note) {
    if (!selectedAgent) { setError("Pick an environment/agent to apply to first."); return; }
    setApplying(true);
    setError("");
    try {
      await api.provision(Number(selectedAgent), targetShardCount, note);
      const now = Date.now();
      setLastApplied(now);
      localStorage.setItem("vantik_autoscale_last_applied", String(now));
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setApplying(false);
    }
  }

  async function applyGateway(targetReplicas, note) {
    if (!selectedAgent) { setError("Pick an environment/agent to apply to first."); return; }
    setGwApplying(true);
    setError("");
    try {
      // shardCount is passed UNCHANGED (current value) - only gatewayReplicas
      // is a real change here; the backend treats this as "reconcile the
      // gateway tier, leave the data plane's shard count exactly as is" (see
      // fleet_agent/provisioner.py - a provided gatewayReplicas is never
      // treated as a shard-count no-op, even when shardCount matches).
      await api.provision(Number(selectedAgent), shardCount || 1, note, targetReplicas);
      setCurrentGatewayReplicas(targetReplicas);
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setGwApplying(false);
    }
  }

  return (
    <div className="page">
      <h2>Autoscaling</h2>
      <p className="muted">
        Recommends a shard count from live ingest volume against the policy below, and applies it via
        the same fleet-provisioning path the Fleet page uses. Shard count is a real topology change —
        new shards start empty, nothing rebalances existing data — so auto-apply defaults off.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="hero-stat">
        <div className="hero-stat-main">
          <div className="hero-stat-label">Cluster ingest, per shard</div>
          <div className="hero-stat-value">{latest ? fmt(latest.perShard, 0) : "0"}<span className="kpi-unit">msg/s/shard</span></div>
          <span className={`hero-stat-delta ${latest && latest.utilPct >= policy.scaleUpPct ? "down" : "up"}`}>
            {latest ? `${fmt(latest.utilPct, 0)}% of ${policy.targetMsgsPerShard} msg/s target` : "waiting for data"}
          </span>
        </div>
        <div className="hero-stat-spark"><Sparkline data={rateHistory} width={160} height={48} strokeWidth={2.5} /></div>
      </div>

      <div className="kpi-row">
        <div className="kpi"><div className="kpi-label">Current shards</div><div className="kpi-value">{shardCount || "—"}</div><div className="kpi-sub">{svc.length} processes</div></div>
        <div className={`kpi ${rec.action === "hold" ? "" : rec.action === "scale_up" ? "bad" : "ok"}`}>
          <div className="kpi-label">Recommendation</div>
          <div className="kpi-value" style={{ fontSize: "1.4rem" }}>{recLabel(rec)}</div>
          <div className="kpi-sub">{samples.length}/{SAMPLE_WINDOW} samples agree</div>
        </div>
        <div className="kpi"><div className="kpi-label">Cooldown</div><div className="kpi-value" style={{ fontSize: "1.2rem" }}>{inCooldown ? `${Math.ceil(cooldownRemaining / 1000)}s` : "ready"}</div><div className="kpi-sub">5 min between applies</div></div>
      </div>

      <div className="card">
        <div className="section-head"><h3>Recommendation</h3></div>
        <p className="muted" style={{ marginTop: 0 }}>{rec.reason}</p>
        <div className="form-row">
          <label className="muted">Apply to environment
            <select value={selectedAgent} onChange={(e) => setSelectedAgent(e.target.value)} style={{ display: "block", marginTop: "0.2rem" }}>
              <option value="">— select a registered agent —</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.environment}) — {a.status}</option>)}
            </select>
          </label>
          <button className="primary" disabled={rec.action === "hold" || inCooldown || applying || !selectedAgent}
                  onClick={() => apply(rec.targetShardCount, `manual apply: ${rec.reason}`)}>
            {applying ? "Applying…" : `Apply → ${rec.targetShardCount ?? shardCount} shards`}
          </button>
        </div>
        {agents.length === 0 && (
          <p className="muted">No environments registered yet — register one on the <strong>Fleet</strong> page to enable Apply. The recommendation above is still live and real, it just has nowhere to send the command to yet.</p>
        )}
      </div>

      <div className="card">
        <div className="section-head"><h3>Gateway</h3></div>
        <p className="muted" style={{ marginTop: 0 }}>
          The gateway is a stateless router, not a shard — it scales independently of shard count (see the
          Helm chart&rsquo;s <span className="mono">gateway.replicaCount</span>/<span className="mono">gateway.autoscaling</span>).
          Recommendation below is driven by real query-path request volume (the last 10s of actual executions,
          same data the Query workspace&rsquo;s history tab shows) against a target requests/sec per replica —
          there&rsquo;s no live &ldquo;how many gateway replicas exist right now&rdquo; signal this page can read,
          so enter your last-known count once and it&rsquo;s remembered.
        </p>
        <div className="hero-stat" style={{ marginBottom: "0.8rem" }}>
          <div className="hero-stat-main">
            <div className="hero-stat-label">Gateway-routed queries</div>
            <div className="hero-stat-value">{fmt(gwReqRate, 1)}<span className="kpi-unit">req/s</span></div>
            <span className="hero-stat-delta">{fmt(gwReqRate / Math.max(1, currentGatewayReplicas), 1)} req/s per replica</span>
          </div>
          <div className="hero-stat-spark"><Sparkline data={gwReqHistory} width={160} height={48} strokeWidth={2.5} /></div>
        </div>
        <div className="form-row wrap">
          <label className="muted">Current gateway replicas (last known)
            <input type="number" min="1" value={currentGatewayReplicas} style={{ width: "5rem", display: "block" }}
                   onChange={(e) => setCurrentGatewayReplicas(Math.max(1, Number(e.target.value) || 1))} />
          </label>
          <label className="muted">Target req/s per replica
            <input type="number" min="1" value={policy.gatewayTargetReqsPerSec} style={{ width: "6rem", display: "block" }}
                   onChange={(e) => setPolicy((p) => ({ ...p, gatewayTargetReqsPerSec: Number(e.target.value) || 1 }))} />
          </label>
          <label className="muted">Min replicas
            <input type="number" min="1" value={policy.gatewayMinReplicas} style={{ width: "5rem", display: "block" }}
                   onChange={(e) => setPolicy((p) => ({ ...p, gatewayMinReplicas: Number(e.target.value) || 1 }))} />
          </label>
          <label className="muted">Max replicas
            <input type="number" min="1" max="26" value={policy.gatewayMaxReplicas} style={{ width: "5rem", display: "block" }}
                   onChange={(e) => setPolicy((p) => ({ ...p, gatewayMaxReplicas: Number(e.target.value) || 1 }))} />
          </label>
        </div>
        <p className="muted" style={{ marginTop: "0.6rem" }}>{gwRec.reason}</p>
        <button className="primary" disabled={gwRec.action === "hold" || gwApplying || !selectedAgent}
                onClick={() => applyGateway(gwRec.targetReplicas, `gateway apply: ${gwRec.reason}`)}>
          {gwApplying ? "Applying…" : `Apply → ${gwRec.targetReplicas} gateway replicas`}
        </button>
      </div>

      <div className="card">
        <h3>Policy</h3>
        <div className="form-row wrap">
          <label className="muted">Target msg/s per shard
            <input type="number" min="1" value={policy.targetMsgsPerShard} style={{ width: "6rem", display: "block" }}
                   onChange={(e) => setPolicy((p) => ({ ...p, targetMsgsPerShard: Number(e.target.value) || 1 }))} />
          </label>
          <label className="muted">Scale up above %
            <input type="number" min="1" max="100" value={policy.scaleUpPct} style={{ width: "5rem", display: "block" }}
                   onChange={(e) => setPolicy((p) => ({ ...p, scaleUpPct: Number(e.target.value) || 0 }))} />
          </label>
          <label className="muted">Scale down below %
            <input type="number" min="0" max="99" value={policy.scaleDownPct} style={{ width: "5rem", display: "block" }}
                   onChange={(e) => setPolicy((p) => ({ ...p, scaleDownPct: Number(e.target.value) || 0 }))} />
          </label>
          <label className="muted">Min shards
            <input type="number" min="1" value={policy.minShards} style={{ width: "5rem", display: "block" }}
                   onChange={(e) => setPolicy((p) => ({ ...p, minShards: Number(e.target.value) || 1 }))} />
          </label>
          <label className="muted">Max shards
            <input type="number" min="1" max="26" value={policy.maxShards} style={{ width: "5rem", display: "block" }}
                   onChange={(e) => setPolicy((p) => ({ ...p, maxShards: Number(e.target.value) || 1 }))} />
          </label>
        </div>
        <label className="muted" style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginTop: "0.6rem" }}>
          <input type="checkbox" checked={policy.autoApply} onChange={(e) => setPolicy((p) => ({ ...p, autoApply: e.target.checked }))} />
          Auto-apply recommendations (requires an environment selected above; still respects the 5-minute cooldown)
        </label>
        {policy.autoApply && <div className="ops-banner warn" style={{ marginTop: "0.6rem" }}><div>Auto-apply is on — this will change real shard count unattended once {SAMPLE_WINDOW} consecutive samples agree. Turn it off if you just want visibility.</div></div>}
      </div>

      <div className="card">
        <div className="section-head"><h3>Shard sync status</h3></div>
        <p className="muted" style={{ marginTop: 0 }}>
          A new or restarted shard's RDB has no log to replay — it warm-loads once from WDB's scratch
          file, then simply resubscribes to its tickerplant and waits for live ticks, so "sync" here
          means reaching four real, observed milestones, not a fabricated percentage. WDB's watermark
          below is a durability checkpoint (it saw-tooths up to the flush interval by design), not a
          catch-up signal — shown as exactly that.
        </p>
        {syncStatuses.length === 0 ? <p className="muted">No shards reporting yet.</p> : (
          <div className="sync-grid">
            {syncStatuses.map((s) => (
              <div className="sync-card" key={s.shardId}>
                <div className="sync-card-head"><strong>{s.shardId}</strong><span className={`tp-pill ${s.stage === 3 ? "ok" : s.stage <= 0 ? "bad" : "warn"}`}>{s.label}</span></div>
                <div className="sync-steps">
                  {SYNC_STAGES.map((_, i) => (
                    <span key={i} className={`sync-step ${i < s.stage || (i === s.stage && s.stage === SYNC_STAGES.length - 1) ? "done" : i === s.stage ? "active" : ""}`} />
                  ))}
                </div>
                <div className="sync-step-labels">{SYNC_STAGES.map((label) => <span key={label}>{label}</span>)}</div>
                <div className="sync-detail">{s.detail}</div>
                <div className="sync-meta">
                  {s.stage === 2 && (
                    <span className="sync-eta">
                      {s.eta != null ? `~${s.eta < 60 ? `${s.eta}s` : `${Math.round(s.eta / 60)}m`} to live, based on shrinking lag trend` : "estimating time to live…"}
                    </span>
                  )}
                  {s.wdbLastWatermark && <span>WDB last durable flush: <span className="mono">{s.wdbLastWatermark}</span></span>}
                  {(s.rdbReconnects > 0 || s.wdbReconnects > 0) && <span>Reconnect attempts — RDB: {s.rdbReconnects ?? 0}, WDB: {s.wdbReconnects ?? 0}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function computeRecommendation(samples, shardCount, policy) {
  if (samples.length < SAMPLE_WINDOW) {
    return { action: "hold", targetShardCount: shardCount, reason: `Collecting samples (${samples.length}/${SAMPLE_WINDOW}) before recommending anything.` };
  }
  const allAbove = samples.every((s) => s.utilPct >= policy.scaleUpPct);
  const allBelow = samples.every((s) => s.utilPct >= 0 && s.utilPct <= policy.scaleDownPct);
  const avgUtil = samples.reduce((sum, s) => sum + s.utilPct, 0) / samples.length;
  const avgRate = samples.reduce((sum, s) => sum + s.rate, 0) / samples.length;

  if (allAbove && shardCount < policy.maxShards) {
    const target = Math.min(policy.maxShards, Math.max(shardCount + 1, Math.ceil(avgRate / policy.targetMsgsPerShard)));
    return { action: "scale_up", targetShardCount: target,
      reason: `Sustained ${fmt(avgUtil, 0)}% of target for the last ${SAMPLE_WINDOW} samples — recommend scaling ${shardCount} → ${target} shards.` };
  }
  if (allBelow && shardCount > policy.minShards) {
    const target = Math.max(policy.minShards, shardCount - 1);
    return { action: "scale_down", targetShardCount: target,
      reason: `Sustained only ${fmt(avgUtil, 0)}% of target for the last ${SAMPLE_WINDOW} samples — recommend scaling ${shardCount} → ${target} shards to cut idle cost.` };
  }
  return { action: "hold", targetShardCount: shardCount,
    reason: `${fmt(avgUtil, 0)}% of target, within the ${policy.scaleDownPct}–${policy.scaleUpPct}% hold band — no change recommended.` };
}

function computeGatewayRecommendation(reqRate, currentReplicas, policy) {
  const target = Math.min(
    policy.gatewayMaxReplicas,
    Math.max(policy.gatewayMinReplicas, Math.ceil(reqRate / policy.gatewayTargetReqsPerSec) || policy.gatewayMinReplicas)
  );
  if (target === currentReplicas) {
    return { action: "hold", targetReplicas: currentReplicas,
      reason: `${fmt(reqRate, 1)} req/s across ${currentReplicas} replica${currentReplicas === 1 ? "" : "s"} — within target, no change recommended.` };
  }
  const action = target > currentReplicas ? "scale_up" : "scale_down";
  return { action, targetReplicas: target,
    reason: `${fmt(reqRate, 1)} req/s ÷ ${policy.gatewayTargetReqsPerSec} target/replica → recommend ${currentReplicas} → ${target} gateway replicas.` };
}

function recLabel(rec) {
  if (rec.action === "scale_up") return `Scale up → ${rec.targetShardCount}`;
  if (rec.action === "scale_down") return `Scale down → ${rec.targetShardCount}`;
  return "Hold";
}
