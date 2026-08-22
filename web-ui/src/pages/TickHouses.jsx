import { useEffect, useState } from "react";
import { api } from "../api.js";
import StatusBadge from "../components/StatusBadge.jsx";
import SymbolPicker from "../components/SymbolPicker.jsx";
import CloudAutoProvision from "../components/CloudAutoProvision.jsx";

export default function TickHouses() {
  const [meta, setMeta] = useState(null);
  const [clusters, setClusters] = useState([]);
  const [agents, setAgents] = useState([]);
  const [infraProfiles, setInfraProfiles] = useState([]);
  const [feedCatalog, setFeedCatalog] = useState([]);
  const [error, setError] = useState("");
  // Two ways to get a TickHouse: the option-based wizard below (assumes a
  // cluster + enrolled agent already exist) or credentials-only auto
  // provisioning (builds the cluster itself via terraform+helm - see
  // CloudAutoProvision.jsx). Neither replaces the other; "quick" trades
  // control for not needing an existing cluster first.
  const [mode, setMode] = useState("wizard");

  async function refresh() {
    try { setClusters(await api.listTickhouses()); } catch (err) { setError(String(err)); }
  }
  useEffect(() => {
    api.tickhouseMeta().then(setMeta).catch((e) => setError(String(e)));
    api.listAgents().then(setAgents).catch(() => {});
    // Global infra profiles (Admin -> Infrastructure settings) - used below
    // to auto-fill this wizard's coordinate fields for whichever provider
    // is selected. Readable by any authenticated user (not just admins),
    // same as the endpoint itself.
    api.listInfraProfiles().then(setInfraProfiles).catch(() => {});
    // Feed-handler provider catalog (see Admin -> Feed handlers) - lets this
    // wizard offer "activate a market-data source into this TickHouse" as
    // part of creation, not a separate trip to another page afterward.
    api.feedhandlerCatalog().then((r) => setFeedCatalog(r.providers)).catch(() => {});
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="page tickhouses-page">
      <h2>TickHouses</h2>
      <p className="muted">
        Define a tick cluster declaratively &mdash; deployment target (AWS/Azure/GCP/on-prem) with its
        cloud &amp; Kubernetes config, a throughput/latency profile, and shards either by letter range
        (<code>a-d, e-h</code>) or by explicit symbols you pick. Component hardware is auto-tuned; review,
        then provision end-to-end through your agent.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="chip-list" style={{ marginBottom: "0.75rem" }}>
        <button className={`chip ${mode === "wizard" ? "generate" : ""}`} onClick={() => setMode("wizard")}>
          Option-based wizard
        </button>
        <button className={`chip ${mode === "quick" ? "generate" : ""}`} onClick={() => setMode("quick")}>
          Quick cloud deploy (credentials only)
        </button>
      </div>

      {mode === "wizard" && meta && (
        <CreateWizard meta={meta} infraProfiles={infraProfiles} feedCatalog={feedCatalog} onCreated={refresh} onError={setError} />
      )}
      {mode === "quick" && <CloudAutoProvision onRunStarted={refresh} />}

      <h3 style={{ marginTop: "1.5rem" }}>Defined clusters</h3>
      {clusters.length === 0 && <p className="muted">None yet.</p>}
      {clusters.map((c) => (
        <ClusterCard key={c.id} cluster={c} agents={agents} onChange={refresh} onError={setError} />
      ))}
    </div>
  );
}

function CreateWizard({ meta, infraProfiles, feedCatalog, onCreated, onError }) {
  const [form, setForm] = useState({
    name: "", location: meta.clouds[0], os: meta.os_types[0], profile: meta.profiles[0],
    shard_ranges: "a-m, n-z", idb: false, ldap_ref: "",
    sharding_policy: "letter-range",
  });
  const [config, setConfig] = useState({});
  // Optional feed handler activated into this TickHouse in the same create
  // call - "" means "none selected", matching form.location's own pattern
  // of an empty/unset string rather than null for a <select>'s value.
  const [feedProviderKey, setFeedProviderKey] = useState("");
  const [feedSecrets, setFeedSecrets] = useState({});
  // Which default profile (if any) config was last auto-filled from, for
  // this location - a field still equal to what that profile provided
  // shows "inherited from Global Settings"; editing it counts as an
  // explicit override (item 9/10's precedence: override beats profile).
  const [loadedProfile, setLoadedProfile] = useState(null);
  const [symShards, setSymShards] = useState([{ label: "shard-1", symbols: [] }]);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sla, setSla] = useState({ tick_to_trade_target_ms: "", peak_msgs_per_sec: "", symbol_count: "", cross_region: false });
  const [suggestion, setSuggestion] = useState(null);
  const [suggestBusy, setSuggestBusy] = useState(false);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const cfgFields = (meta.config_fields && meta.config_fields[form.location]) || [];

  function applyLocation(location) {
    set("location", location);
    const defaultProfile = infraProfiles.find(
      (p) => p.provider === location && p.is_default && p.enabled);
    setConfig(defaultProfile ? { ...defaultProfile.config } : {});
    setLoadedProfile(defaultProfile || null);
  }

  // Profiles arrive from a separate fetch that can resolve after this
  // wizard's first render - auto-fill once for whatever location is
  // already selected (the default, if the user hasn't touched the
  // dropdown yet) so a profile match isn't missed just because of fetch
  // ordering.
  useEffect(() => {
    if (loadedProfile || Object.keys(config).length > 0) return;
    const defaultProfile = infraProfiles.find(
      (p) => p.provider === form.location && p.is_default && p.enabled);
    if (defaultProfile) { setConfig({ ...defaultProfile.config }); setLoadedProfile(defaultProfile); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [infraProfiles]);

  async function doSuggest() {
    setSuggestBusy(true);
    try {
      setSuggestion(await api.suggestTickhouse({
        tick_to_trade_target_ms: sla.tick_to_trade_target_ms ? Number(sla.tick_to_trade_target_ms) : null,
        peak_msgs_per_sec: sla.peak_msgs_per_sec ? Number(sla.peak_msgs_per_sec) : null,
        symbol_count: sla.symbol_count ? Number(sla.symbol_count) : null,
        cross_region: sla.cross_region,
      }));
    } catch (err) { onError(String(err)); }
    finally { setSuggestBusy(false); }
  }
  function applySuggestedProfile() {
    if (suggestion) set("profile", suggestion.profile);
  }

  const feedEntry = feedCatalog.find((e) => `${e.provider}/${e.feed}` === feedProviderKey) || null;
  const feedMissingCreds = feedEntry
    ? feedEntry.credentials_required.filter((f) => !feedSecrets[f]?.trim())
    : [];

  function body() {
    const b = { ...form, target_config: config };
    if (form.sharding_policy === "explicit-symbols") {
      b.shard_symbols = symShards.filter((s) => s.symbols.length);
    }
    if (feedEntry) {
      b.feed_handler = {
        provider: feedEntry.provider, feed: feedEntry.feed, display_name: feedEntry.display_name,
        enabled: true, config: feedEntry.default_config, secrets: feedSecrets,
      };
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
    if (feedEntry && feedMissingCreds.length) {
      onError(`Missing required credential(s) for ${feedEntry.display_name}: ${feedMissingCreds.join(", ")}`);
      return;
    }
    setBusy(true);
    try {
      await api.createTickhouse(body());
      setPreview(null);
      setFeedProviderKey("");
      setFeedSecrets({});
      onCreated();
    }
    catch (err) { onError(String(err)); }
    finally { setBusy(false); }
  }

  return (
    <div className="card">
      <h3>Create a TickHouse</h3>

      <div className="wizard-section-label">
        Not sure what to pick? Tell us your target &mdash; a starting point to review, not an auto-deploy
      </div>
      <div className="form-row wrap">
        <input placeholder="tick-to-trade target, ms (e.g. 100)" type="number" min="0" style={{ minWidth: "14rem" }}
               value={sla.tick_to_trade_target_ms}
               onChange={(e) => setSla((s) => ({ ...s, tick_to_trade_target_ms: e.target.value }))} />
        <input placeholder="peak msgs/sec (e.g. 8000)" type="number" min="0" style={{ minWidth: "12rem" }}
               value={sla.peak_msgs_per_sec}
               onChange={(e) => setSla((s) => ({ ...s, peak_msgs_per_sec: e.target.value }))} />
        <input placeholder="symbol count (e.g. 3000)" type="number" min="0" style={{ minWidth: "11rem" }}
               value={sla.symbol_count}
               onChange={(e) => setSla((s) => ({ ...s, symbol_count: e.target.value }))} />
        <label><input type="checkbox" checked={sla.cross_region}
                      onChange={(e) => setSla((s) => ({ ...s, cross_region: e.target.checked }))} /> clients are cross-region</label>
        <button disabled={suggestBusy} onClick={doSuggest}>Suggest a blueprint</button>
      </div>

      {suggestion && (
        <div className="command-log">
          <div className="muted">
            Suggested profile: <strong>{suggestion.profile}</strong>, ~<strong>{suggestion.shard_count}</strong> shards.
            {" "}
            {suggestion.target_ms != null && (
              suggestion.target_likely_achievable
                ? <span> Your {suggestion.target_ms}ms target is above the estimated floor &mdash; plausible, pending real verification.</span>
                : <span className="error"> Your {suggestion.target_ms}ms target is below even the optimistic estimate &mdash; see caveats below.</span>
            )}
          </div>
          <table className="spec-table">
            <thead><tr><th>hop</th><th>estimated ms (low&ndash;high)</th></tr></thead>
            <tbody>
              {Object.entries(suggestion.latency_budget_ms).map(([hop, r]) => (
                <tr key={hop}><td>{hop.replace(/_/g, " ")}</td><td>{r.low}&ndash;{r.high}</td></tr>
              ))}
              <tr>
                <td><strong>total</strong></td>
                <td><strong>{suggestion.latency_budget_total_ms.low}&ndash;{suggestion.latency_budget_total_ms.high}</strong></td>
              </tr>
            </tbody>
          </table>
          <ul className="muted" style={{ fontSize: "0.78rem" }}>
            {suggestion.caveats.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
          <button onClick={applySuggestedProfile}>Use profile &ldquo;{suggestion.profile}&rdquo; below</button>
        </div>
      )}

      <div className="wizard-section-label">Cluster</div>
      <div className="form-row">
        <input placeholder="name, e.g. acme-emea" value={form.name} onChange={(e) => set("name", e.target.value)} />
        <select value={form.location} onChange={(e) => applyLocation(e.target.value)}>
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
          {loadedProfile && (
            <div className="muted" style={{ fontSize: "0.78rem", marginBottom: "0.25rem" }}>
              Loaded from Global Settings profile &ldquo;{loadedProfile.name}&rdquo; &mdash; edit any field below to override it.
            </div>
          )}
          <div className="form-row wrap">
            {cfgFields.map((f) => {
              const missing = !String(config[f] || "").trim();
              const inherited = loadedProfile && config[f] === loadedProfile.config[f] && !missing;
              return (
                <div key={f} style={{ display: "flex", flexDirection: "column" }}>
                  <input placeholder={`${f} *`} value={config[f] || ""}
                         title={`${f} is required to provision on ${form.location}`}
                         className={missing ? "field-required-missing" : ""}
                         onChange={(e) => setConfig((c) => ({ ...c, [f]: e.target.value }))} />
                  {inherited && <span className="muted" style={{ fontSize: "0.7rem" }}>inherited from Global Settings</span>}
                </div>
              );
            })}
          </div>
          <div className="muted" style={{ fontSize: "0.78rem" }}>
            Non-secret coordinates only. Credentials (keys, kubeconfig tokens) stay in the agent&rsquo;s secret store.
            These are the account/cluster the agent already running there will deploy into &mdash; this
            does not create a new AWS/Azure/GCP cluster for you; enroll an agent in an existing cluster first
            (see the Fleet page). Manage reusable profiles under Admin &rarr; Infrastructure settings.
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

      <div className="wizard-section-label">Feed handler (optional)</div>
      <div className="form-row wrap">
        <select value={feedProviderKey} onChange={(e) => { setFeedProviderKey(e.target.value); setFeedSecrets({}); }}>
          <option value="">none - attach one later from Admin &rarr; Feed handlers</option>
          {feedCatalog.filter((e) => e.engine_support !== "catalog_only").map((e) => (
            <option key={`${e.provider}/${e.feed}`} value={`${e.provider}/${e.feed}`}>{e.display_name}</option>
          ))}
        </select>
      </div>
      {feedEntry && (
        <>
          <p className="muted" style={{ fontSize: "0.8rem" }}>{feedEntry.coverage}</p>
          {feedEntry.credentials_required.length > 0 && (
            <div className="form-row wrap">
              {feedEntry.credentials_required.map((f) => (
                <input key={f} type={f.toLowerCase().includes("password") ? "password" : "text"} placeholder={f}
                       value={feedSecrets[f] || ""}
                       onChange={(e) => setFeedSecrets((s) => ({ ...s, [f]: e.target.value }))} />
              ))}
            </div>
          )}
          <div className="muted" style={{ fontSize: "0.78rem" }}>
            Activates {feedEntry.display_name} and links it to this TickHouse in the same step - see
            Admin &rarr; Feed handlers to review or edit its connection config afterward.
          </div>
        </>
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
