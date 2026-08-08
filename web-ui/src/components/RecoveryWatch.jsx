import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// Global, non-blocking notice for a tier that's down/reconnecting or
// visibly catching up after a restart - mounted once in App.jsx so it's
// visible regardless of which page is open, and rendered as a small fixed
// stack in one corner so it never covers the sidebar or a page's own
// widgets. Polls independently of whatever page happens to be mounted
// (Metrics.jsx polls its own, more detailed stream when you're on that
// page - this is deliberately separate and lighter-weight).
//
// Honesty matters more than a number here: an ETA is only shown when it's
// grounded in an actual shrinking trend in the RDB catch-up lag - see
// docker-compose service status + .gw.componentMetrics (rdb/wdbConnected)
// for how "down/reconnecting" is detected, neither of which today (as of
// this feature) exposes a real catch-up-progress signal for IDB or the
// tickerplant itself, so those show elapsed time only, never a guessed ETA.
const POLL_MS = 5000;
const ETA_LAG_HISTORY = 5;
const RECOVERED_DISPLAY_MS = 6000;

function tierLabelFor(serviceName) {
  const m = serviceName.match(/^(rdb|idb|wdb|tp|hdb)-(.+)$/);
  if (!m) return null;
  const names = { rdb: "RDB", idb: "IDB", wdb: "WDB", tp: "Tickerplant", hdb: "HDB" };
  return { tier: m[1], shard: m[2], label: `${names[m[1]] || m[1].toUpperCase()} (${m[2]})` };
}

export default function RecoveryWatch() {
  const [issues, setIssues] = useState([]); // [{key, label, kind, since, eta, recovered}]
  const issuesRef = useRef(new Map());
  const lagHistoryRef = useRef(new Map()); // key -> [{t, lag}]

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const [topo, snap] = await Promise.all([api.topologyStatus(), api.metricsSnapshot()]);
        if (cancelled) return;
        reconcile(topo, snap);
      } catch {
        // control-api itself unreachable - not this component's job to
        // report that; other pages already surface connectivity failures.
      }
    }

    function reconcile(topo, snap) {
      const now = Date.now();
      const seenKeys = new Set();
      const map = issuesRef.current;

      // 1. container-level: any managed service visibly restarting/exited
      for (const [service, status] of Object.entries(topo || {})) {
        if (status !== "restarting" && status !== "exited") continue;
        const info = tierLabelFor(service);
        if (!info) continue; // only surface data-plane tiers here, not control-api/web-ui/watchdog
        const key = `container:${service}`;
        seenKeys.add(key);
        if (!map.has(key)) {
          map.set(key, { key, label: info.label, tier: info.tier, shard: info.shard,
                         kind: status === "restarting" ? "restarting" : "down", since: now, recovered: false });
        } else {
          map.get(key).kind = status === "restarting" ? "restarting" : "down";
        }
      }

      // 2. gateway-reported connectivity: rdb/wdb can show disconnected
      // even while the container itself is "running" (e.g. reconnecting
      // after a network blip, not a restart)
      for (const row of snap?.componentMetrics || []) {
        for (const tier of ["rdb", "wdb"]) {
          if (row[`${tier}Connected`] !== false) continue;
          const key = `link:${tier}-${row.shard}`;
          seenKeys.add(key);
          if (!map.has(key)) {
            map.set(key, { key, label: `${tier.toUpperCase()} (${row.shard})`, tier, shard: row.shard,
                           kind: "reconnecting", since: now, recovered: false });
          }
        }
      }

      // 3. RDB catch-up ETA - only where we actually have a decreasing-lag
      // trend to extrapolate from; never fabricated for tiers with no
      // comparable signal (see file header).
      const rdbLagByShard = new Map();
      for (const row of snap?.transitLag || []) {
        if (row.stage === "rdb_to_gateway" && typeof row.lagMs === "number") {
          rdbLagByShard.set(row.shard, row.lagMs);
        }
      }
      for (const issue of map.values()) {
        if (issue.recovered || issue.tier !== "rdb") continue;
        const lag = rdbLagByShard.get(issue.shard);
        if (lag == null) continue;
        const histKey = issue.key;
        const hist = lagHistoryRef.current.get(histKey) || [];
        hist.push({ t: now, lag });
        while (hist.length > ETA_LAG_HISTORY) hist.shift();
        lagHistoryRef.current.set(histKey, hist);
        if (hist.length >= 3) {
          const first = hist[0], last = hist[hist.length - 1];
          const elapsedSec = (last.t - first.t) / 1000;
          const lagDrop = first.lag - last.lag;
          if (elapsedSec > 1 && lagDrop > 0) {
            const shrinkRatePerSec = lagDrop / elapsedSec;
            issue.eta = Math.max(0, Math.round((last.lag / shrinkRatePerSec)));
          } else {
            issue.eta = null; // flat or growing - don't guess
          }
        }
      }

      // 4. anything tracked that's no longer flagged just recovered
      for (const issue of map.values()) {
        if (!seenKeys.has(issue.key) && !issue.recovered) {
          issue.recovered = true;
          issue.recoveredAt = now;
          lagHistoryRef.current.delete(issue.key);
        }
      }
      // drop recovered issues after they've been shown briefly
      for (const [key, issue] of [...map.entries()]) {
        if (issue.recovered && now - issue.recoveredAt > RECOVERED_DISPLAY_MS) {
          map.delete(key);
        }
      }

      setIssues([...map.values()].sort((a, b) => a.since - b.since));
    }

    tick();
    const id = setInterval(tick, POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (issues.length === 0) return null;

  return (
    <div className="recovery-stack" role="status" aria-live="polite">
      {issues.map((issue) => (
        <div key={issue.key} className={`recovery-card ${issue.recovered ? "recovered" : issue.kind}`}>
          <div className="recovery-card-head">
            <span className={`recovery-dot ${issue.recovered ? "recovered" : issue.kind}`} />
            <strong>{issue.label}</strong>
          </div>
          <div className="recovery-card-body">
            {issue.recovered ? (
              "Recovered"
            ) : (
              <>
                {issue.kind === "restarting" && "Container restarting"}
                {issue.kind === "down" && "Container down"}
                {issue.kind === "reconnecting" && "Reconnecting to tickerplant"}
                {" · up "}{elapsed(Date.now() - issue.since)}
                {issue.tier === "rdb" && (
                  <div className="recovery-eta">
                    {issue.eta != null ? `catching up - ~${elapsed(issue.eta * 1000)} remaining` : "catching up - estimating…"}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function elapsed(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}
