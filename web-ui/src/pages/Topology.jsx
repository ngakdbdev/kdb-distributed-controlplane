import { useEffect, useState } from "react";
import { api } from "../api.js";
import StatusBadge from "../components/StatusBadge.jsx";

const SHARDS = [
  {
    label: "Shard A-M",
    services: ["tp-a-m", "wdb-a-m", "rdb-a-m", "idb-a-m"],
  },
  {
    label: "Shard N-Z",
    services: ["tp-n-z", "wdb-n-z", "rdb-n-z", "idb-n-z"],
  },
];

const OTHER = ["gateway", "bpipe-sim", "crims-sim"];

export default function Topology() {
  const [status, setStatus] = useState({});
  const [busyService, setBusyService] = useState(null);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      setStatus(await api.topologyStatus());
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, []);

  async function act(service, fn) {
    setBusyService(service);
    try {
      await fn(service);
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusyService(null);
    }
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
          <button
            className="danger"
            disabled={busyService === svc}
            title="Stops the container to demonstrate the watchdog's self-healing runbook"
            onClick={() => act(svc, api.stopService)}
          >
            Kill (demo self-heal)
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <h2>Topology</h2>
      <p className="muted">
        Two symbol-range shards, each following the Tick-X pattern: tickerplant → write-down DB → chained RDB → intraday DB.
        Kill a process below and watch the watchdog detect and restart it - check the Audit log tab for the trail.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="shard-grid">
        {SHARDS.map((shard) => (
          <div className="shard-card" key={shard.label}>
            <h3>{shard.label}</h3>
            {shard.services.map((svc) => <ServiceRow svc={svc} key={svc} />)}
          </div>
        ))}
      </div>
      <div className="shard-card" style={{ marginTop: "1rem" }}>
        <h3>Gateway & feeds</h3>
        {OTHER.map((svc) => <ServiceRow svc={svc} key={svc} />)}
      </div>
    </div>
  );
}
