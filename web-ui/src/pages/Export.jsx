import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function Export() {
  const [sinks, setSinks] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.listExportSinks().then(setSinks).catch((err) => setError(String(err)));
  }, []);

  return (
    <div className="page">
      <h2>Data export</h2>
      <p className="muted">
        Pull data out of the kdb+ side &mdash; HDB history or a live shard&rsquo;s RDB/IDB &mdash; and land
        it in a cloud lakehouse/warehouse. Extraction to Arrow/Parquet runs offline; the warehouse loads
        use each vendor&rsquo;s real SDK and connect once you supply account credentials. Runs from the
        <code> export.runner</code> CLI (or, per-tenant, via the fleet agent where your kdb+ and cloud
        credentials live).
      </p>
      {error && <div className="error">{error}</div>}

      <div className="provider-grid">
        {sinks.map((s) => (
          <div className="card provider-card" key={s.name}>
            <div className="provider-header">
              <h3>{s.display_name}</h3>
              <span className={`tier-badge ${s.offline ? "tier-live" : "tier-licensed"}`}>
                {s.offline ? "offline" : "cloud creds"}
              </span>
            </div>
            <p className="muted">Needs: {s.requires}</p>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginTop: "1rem" }}>
        <h3>How to run</h3>
        <p className="muted">Source is whichever q process you point at &mdash; the gateway (recent, all
          shards), or a specific shard&rsquo;s RDB (today) / HDB (history, with a <code>--date</code>).</p>
        <pre className="token-box">{`# HDB history -> local Parquet (offline)
python -m export.runner --host hdb-s0 --port 5060 \\
    --table trade --date 2026.08.01 --symbols AAPL,MSFT \\
    --sink parquet --out /tmp/export

# gateway -> Snowflake (needs SNOWFLAKE_* env)
python -m export.runner --source gateway --host gateway --port 5050 \\
    --table trade --sink snowflake`}</pre>
      </div>
    </div>
  );
}
