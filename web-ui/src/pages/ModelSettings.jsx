import { useEffect, useState } from "react";
import { api } from "../api.js";

// Platform-admin control over which LLM backs the query workspace's
// natural-language-to-q / code-gen / analyze features (control-api/app/
// llm_runtime_config.py). Saving here takes effect on the NEXT request -
// no editing NL2Q_LLM_* env vars, no container restart. Backend enforces
// platform_admin regardless of whether this tab is visible (App.jsx hides
// it client-side only, as a courtesy).
const PROVIDERS = [
  { id: "none", label: "None (offline pattern-matcher only)" },
  { id: "openai_compatible", label: "OpenAI-compatible (Ollama / vLLM / OpenAI / Azure OpenAI)" },
  { id: "anthropic", label: "Anthropic (Claude)" },
];

export default function ModelSettings() {
  const [form, setForm] = useState({ provider: "none", model: "", api_key: "", base_url: "", timeout_sec: 20 });
  const [current, setCurrent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  function load() {
    setLoading(true);
    api.getLLMConfig()
      .then((d) => {
        setCurrent(d);
        setForm({ provider: d.provider, model: d.model, api_key: "", base_url: d.base_url,
                  timeout_sec: d.timeout_sec });
        setError("");
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function save(e) {
    e.preventDefault();
    setSaving(true); setMessage(""); setError("");
    try {
      const saved = await api.updateLLMConfig(form);
      setCurrent(saved);
      setForm((f) => ({ ...f, api_key: "" }));
      setMessage("Saved - takes effect on the next request, no restart needed.");
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ""));
    } finally {
      setSaving(false);
    }
  }

  const isLocal = form.provider === "openai_compatible" && /ollama|localhost|127\.0\.0\.1|host\.docker\.internal/i.test(form.base_url || "");

  return (
    <div className="page">
      <h2>Model settings</h2>
      <p className="muted">
        Controls which model backs the query workspace's "Generate q", "Generate code", and "Analyze"
        features. Switch between a self-hosted on-prem model (no network calls, no API key) and an
        external provider (higher quality, requires a key and a call out) at any time — takes effect
        immediately, no container restart.
      </p>

      {loading && <div className="muted">Loading…</div>}
      {error && <div className="error query-error">{error}</div>}

      {!loading && (
        <form className="query-side" style={{ maxWidth: 520 }} onSubmit={save}>
          {current && (
            <div className="query-result-meta muted" style={{ marginBottom: 10 }}>
              Currently active: <strong>{current.provider}</strong>
              {current.model && <> · {current.model}</>} · source: {current.source === "admin" ? "saved here" : "env var default"}
              {current.api_key_set && <> · API key set</>}
            </div>
          )}

          <label className="query-label">Provider</label>
          <select className="target-select" value={form.provider}
                  onChange={(e) => setForm((f) => ({ ...f, provider: e.target.value }))}>
            {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>

          {form.provider !== "none" && (
            <>
              <label className="query-label">Model</label>
              <input className="nl2q-input" style={{ width: "100%" }} value={form.model}
                     placeholder={form.provider === "anthropic" ? "claude-opus-5" : "qwen2.5-coder:3b"}
                     onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))} />

              {form.provider === "openai_compatible" && (
                <>
                  <label className="query-label">Base URL</label>
                  <input className="nl2q-input" style={{ width: "100%" }} value={form.base_url}
                         placeholder="http://ollama:11434/v1 (on-prem) or https://api.openai.com/v1 (external)"
                         onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))} />
                </>
              )}

              <label className="query-label">
                API key {current?.api_key_set && <span className="muted">(leave blank to keep the saved key)</span>}
              </label>
              <input className="nl2q-input" style={{ width: "100%" }} type="password" value={form.api_key}
                     placeholder={current?.api_key_set ? "•••••••• (unchanged)" : isLocal ? "not needed for a local model" : ""}
                     onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))} />

              <label className="query-label">Timeout (seconds)</label>
              <input className="nl2q-input" type="number" min="1" max="120" value={form.timeout_sec}
                     onChange={(e) => setForm((f) => ({ ...f, timeout_sec: Number(e.target.value) }))} />

              {isLocal && (
                <div className="muted" style={{ marginTop: 4 }}>
                  Pointed at a local/on-prem host — no external network call, no API key required.
                </div>
              )}
              {!isLocal && form.provider !== "none" && (
                <div className="query-warning" style={{ marginTop: 8 }}>
                  This provider makes an external network call per generation and needs a valid API key.
                </div>
              )}
            </>
          )}

          <div className="query-actions" style={{ marginTop: 12 }}>
            <button className="primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save"}</button>
            <button className="chip" type="button" onClick={load} disabled={loading}>Refresh</button>
          </div>
          {message && <div className="muted" style={{ marginTop: 8 }}>{message}</div>}
        </form>
      )}
    </div>
  );
}
