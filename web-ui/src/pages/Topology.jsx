import { useEffect, useState } from "react";
import { api } from "../api.js";
import StatusBadge from "../components/StatusBadge.jsx";

// Group the live process list into shards by the -s<N> suffix, so this reflects
// the ACTUAL running topology (any shard count) instead of a hardcoded layout.
function groupByShard(status) {
  const shards = {};
  const other = [];
  for (const svc of Object.keys(status)) {
    const m = svc.match(/-s(\d+)$/);
    if (m) (shards[m[1]] = shards[m[1]] || []).push(svc);
    else other.push(svc);
  }
  return { shards, other };
}

const TIER_ORDER = { tp: 0, wdb: 1, rdb: 2, idb: 3 };
const sortTier = (a, b) =>
  (TIER_ORDER[a.split("-")[0]] ?? 9) - (TIER_ORDER[b.split("-")[0]] ?? 9);

export default function Topology({ onNavigate }) {
  const [status, setStatus] = useState({});
  const [busyService, setBusyService] = useState(null);
  const [error, setError] = useState("");

  async function refresh() {
    try { setStatus(await api.topologyStatus()); }
    catch (err) { setError(String(err)); }
  }
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, []);

  async function act(service, fn) {
    setBusyService(service);
    try { await fn(service); await refresh(); }
    catch (err) { setError(String(err)); }
    finally { setBusyService(null); }
  }

  function ServiceRow({ svc }) {
    return (
      <div className="service-row" key={svc}>
        <span className="service-name">{svc}</span>
        <StatusBadge status={status[svc] || "unknown"} />
        <div className="service-actions">
          <button disabled={busyService === svc} onClick={() => act(svc, api.startService)}>Start</button>
          <button disabled={busyService === svc} onClick={() => act(svc, api.stopService)}>Stop</button>
          <button disabled={busyService === svc} onClick={() => act(svc, api.restartService)}>Restart</button>
          <button className="danger" disabled={busyService === svc}
                  title="Stops the container to demonstrate the watchdog's self-healing runbook"
                  onClick={() => act(svc, api.stopService)}>
            Kill (demo self-heal)
          </button>
        </div>
      </div>
    );
  }

  const { shards, other } = groupByShard(status);
  const shardIds = Object.keys(shards).sort();
  const nothing = Object.keys(status).length === 0;

  return (
    <div className="page">
      <h2>Topology</h2>
      <p className="muted">
        The individual <strong>processes</strong> of your running tick chain, one card per shard, each
        following the pattern <em>tickerplant → write-down DB → chained RDB → intraday DB</em>, plus the
        gateway and feeds. This is the live process view; to <strong>define or provision a cluster</strong>,
        use the <button className="link-btn inline" onClick={() => onNavigate?.("tickhouses")}>TickHouses</button> tab.
        Kill a process below and watch the watchdog restart it — the trail shows in the Audit log.
      </p>
      {error && <div className="error">{error}</div>}

      {nothing && (
        <div className="card empty-cta">
          <p>No processes are reporting yet. This view populates once a cluster is running —
             provision one from TickHouses, or start the local stack.</p>
          <button className="primary" onClick={() => onNavigate?.("tickhouses")}>Go to TickHouses</button>
        </div>
      )}

      {shardIds.length > 0 && (
        <div className="shard-grid">
          {shardIds.map((sid) => (
            <div className="shard-card" key={sid}>
              <h3>Shard s{sid}</h3>
              {shards[sid].slice().sort(sortTier).map((svc) => <ServiceRow svc={svc} key={svc} />)}
            </div>
          ))}
        </div>
      )}

      {other.length > 0 && (
        <div className="shard-card" style={{ marginTop: "1rem" }}>
          <h3>Gateway &amp; feeds</h3>
          {other.map((svc) => <ServiceRow svc={svc} key={svc} />)}
        </div>
      )}
    </div>
  );
}
