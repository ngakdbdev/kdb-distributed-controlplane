import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// The credentials-only counterpart to TickHouses.jsx's option-based
// CreateWizard: give it an AWS/Azure/GCP access key pair and it builds the
// entire cluster - network, managed Kubernetes, storage, then Vantik itself
// - via terraform + helm (control-api/app/cloud_provisioner.py). No
// existing cluster or enrolled agent required first, unlike the wizard
// above, which assumes both.
//
// CONFIRM_PHRASE must match cloud_provisioner.CONFIRM_PHRASE exactly - this
// creates real, billed cloud infrastructure, so the confirmation is a
// literal typed phrase, not a checkbox that's easy to click through
// without reading (same deliberate-friction pattern as live trading's
// ALPACA_LIVE_TRADING_ACK).
const CONFIRM_PHRASE = "I_UNDERSTAND_THIS_CREATES_BILLED_CLOUD_RESOURCES";
const CLUSTER_PROFILES = [
  { id: "ha", label: "HA (multi-AZ, higher cost)" },
  { id: "performance", label: "Performance (compute-optimized)" },
  { id: "cost_optimized", label: "Cost-optimized (single-AZ, smaller nodes)" },
];
const POLL_MS = 4000;

export default function CloudAutoProvision({ onRunStarted }) {
  const [provider, setProvider] = useState("aws");
  const [form, setForm] = useState({
    name: "", region: "", cluster_profile: "ha",
    access_key_id: "", secret_access_key: "",
    tenant_id: "", client_id: "", client_secret: "", subscription_id: "",
    project_id: "", service_account_json: "",
  });
  const [confirmText, setConfirmText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [run, setRun] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => () => clearInterval(pollRef.current), []);

  function set(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function startPolling(runId) {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r = await api.getCloudProvisionRun(runId);
        setRun(r);
        if (r.status === "complete" || r.status === "failed") clearInterval(pollRef.current);
      } catch { /* transient - next tick retries */ }
    }, POLL_MS);
  }

  async function submit() {
    setError("");
    if (confirmText !== CONFIRM_PHRASE) {
      setError(`Type the confirmation phrase exactly to proceed: ${CONFIRM_PHRASE}`);
      return;
    }
    setSubmitting(true);
    try {
      const body = { name: form.name, region: form.region, cluster_profile: form.cluster_profile,
                    confirm_ack: confirmText };
      let created;
      if (provider === "aws") {
        created = await api.cloudProvisionAws({ ...body, access_key_id: form.access_key_id,
                                               secret_access_key: form.secret_access_key });
      } else if (provider === "azure") {
        created = await api.cloudProvisionAzure({ ...body, tenant_id: form.tenant_id,
                                                  client_id: form.client_id, client_secret: form.client_secret,
                                                  subscription_id: form.subscription_id });
      } else {
        created = await api.cloudProvisionGcp({ ...body, project_id: form.project_id,
                                                service_account_json: form.service_account_json });
      }
      setRun(created);
      startPolling(created.id);
      onRunStarted?.();
      // Credentials never need to be held in this component's state after
      // submit - clear them so they don't linger in memory/devtools any
      // longer than necessary.
      setForm((f) => ({ ...f, access_key_id: "", secret_access_key: "", client_id: "",
                       client_secret: "", tenant_id: "", subscription_id: "", service_account_json: "" }));
      setConfirmText("");
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setSubmitting(false);
    }
  }

  const regionPlaceholder = provider === "azure" ? "eastus" : provider === "gcp" ? "us-central1" : "us-east-1";
  const canSubmit = form.name && form.region && confirmText === CONFIRM_PHRASE && !submitting;

  return (
    <div className="card">
      <div className="section-head"><h3>Quick cloud deploy</h3></div>
      <p className="muted" style={{ marginTop: 0 }}>
        Give this an AWS, Azure, or GCP access key and it builds the entire cluster end to end -
        network, managed Kubernetes, storage, then Vantik itself - with terraform and helm running
        server-side. No existing cluster or enrolled agent needed first (that's what the wizard above
        is for, once you already have one). This genuinely creates real, billed cloud resources and
        typically takes 10-20+ minutes for the managed Kubernetes control plane alone.
      </p>

      <div className="chip-list" style={{ marginBottom: "0.75rem" }}>
        {["aws", "azure", "gcp"].map((p) => (
          <button key={p} className={`chip ${provider === p ? "generate" : ""}`}
                  onClick={() => setProvider(p)} disabled={submitting}>
            {p.toUpperCase()}
          </button>
        ))}
      </div>

      <div className="form-row wrap">
        <label className="muted">Cluster name
          <input value={form.name} onChange={(e) => set("name", e.target.value)}
                 placeholder="acme-prod" style={{ display: "block" }} />
        </label>
        <label className="muted">{provider === "azure" ? "Location" : "Region"}
          <input value={form.region} onChange={(e) => set("region", e.target.value)}
                 placeholder={regionPlaceholder} style={{ display: "block" }} />
        </label>
        <label className="muted">Cluster profile
          <select value={form.cluster_profile} onChange={(e) => set("cluster_profile", e.target.value)}
                  style={{ display: "block" }}>
            {CLUSTER_PROFILES.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </label>
      </div>

      {provider === "aws" && (
        <div className="form-row wrap">
          <label className="muted">AWS access key ID
            <input value={form.access_key_id} onChange={(e) => set("access_key_id", e.target.value)}
                   autoComplete="off" style={{ display: "block", minWidth: "16rem" }} />
          </label>
          <label className="muted">AWS secret access key
            <input type="password" value={form.secret_access_key}
                   onChange={(e) => set("secret_access_key", e.target.value)}
                   autoComplete="off" style={{ display: "block", minWidth: "16rem" }} />
          </label>
        </div>
      )}
      {provider === "azure" && (
        <div className="form-row wrap">
          <label className="muted">Tenant ID
            <input value={form.tenant_id} onChange={(e) => set("tenant_id", e.target.value)}
                   autoComplete="off" style={{ display: "block", minWidth: "14rem" }} />
          </label>
          <label className="muted">Client ID
            <input value={form.client_id} onChange={(e) => set("client_id", e.target.value)}
                   autoComplete="off" style={{ display: "block", minWidth: "14rem" }} />
          </label>
          <label className="muted">Client secret
            <input type="password" value={form.client_secret} onChange={(e) => set("client_secret", e.target.value)}
                   autoComplete="off" style={{ display: "block", minWidth: "14rem" }} />
          </label>
          <label className="muted">Subscription ID
            <input value={form.subscription_id} onChange={(e) => set("subscription_id", e.target.value)}
                   autoComplete="off" style={{ display: "block", minWidth: "14rem" }} />
          </label>
        </div>
      )}
      {provider === "gcp" && (
        <div className="form-row wrap">
          <label className="muted">Project ID
            <input value={form.project_id} onChange={(e) => set("project_id", e.target.value)}
                   autoComplete="off" style={{ display: "block", minWidth: "14rem" }} />
          </label>
          <label className="muted" style={{ flexBasis: "100%" }}>Service account JSON (paste the full key file contents)
            <textarea value={form.service_account_json} onChange={(e) => set("service_account_json", e.target.value)}
                     autoComplete="off" rows={4} style={{ display: "block", width: "100%", fontFamily: "monospace" }} />
          </label>
        </div>
      )}

      <div style={{ marginTop: "0.75rem" }}>
        <label className="muted" style={{ display: "block", marginBottom: "0.25rem" }}>
          Type <code className="mono">{CONFIRM_PHRASE}</code> to confirm you understand this creates real,
          billed cloud resources
        </label>
        <input value={confirmText} onChange={(e) => setConfirmText(e.target.value)}
               placeholder={CONFIRM_PHRASE} style={{ width: "100%", fontFamily: "monospace" }} />
      </div>

      {error && <div className="error" style={{ marginTop: "0.5rem" }}>{error}</div>}

      <div style={{ marginTop: "0.75rem" }}>
        <button className="primary" onClick={submit} disabled={!canSubmit}>
          {submitting ? "Starting…" : "Deploy"}
        </button>
      </div>

      {run && <RunStatus run={run} />}
    </div>
  );
}

const STAGE_ORDER = ["pending", "planning", "applying", "installing", "provisioning_tickhouse", "complete"];

function RunStatus({ run }) {
  const failed = run.status === "failed";
  const stageIdx = STAGE_ORDER.indexOf(run.status);
  return (
    <div className="card" style={{ marginTop: "1rem", background: "var(--surface-2)" }}>
      <div className="section-head">
        <h4 style={{ margin: 0 }}>{run.name} — {run.provider.toUpperCase()}</h4>
        <span className={`live-pill ${failed ? "off" : run.status === "complete" ? "on" : "off"}`}>
          {failed ? "● FAILED" : run.status === "complete" ? "● COMPLETE" : `○ ${run.status}`}
        </span>
      </div>
      {!failed && (
        <div className="chip-list" style={{ marginBottom: "0.5rem" }}>
          {STAGE_ORDER.map((s, i) => (
            <span key={s} className={`chip ${i <= stageIdx ? "generate" : ""}`} style={{ pointerEvents: "none" }}>
              {s.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}
      {run.status_detail && <p className="muted" style={{ margin: "0.25rem 0" }}>{run.status_detail}</p>}
      {failed && run.error_detail && (
        <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: "0.75rem", maxHeight: "12rem", overflow: "auto" }}>
          {run.error_detail}
        </pre>
      )}
      {run.log_tail && (
        <details>
          <summary className="muted" style={{ cursor: "pointer" }}>Full log</summary>
          <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: "0.7rem", maxHeight: "16rem", overflow: "auto" }}>
            {run.log_tail}
          </pre>
        </details>
      )}
    </div>
  );
}
