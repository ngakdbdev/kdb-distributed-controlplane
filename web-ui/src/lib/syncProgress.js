// Honest per-shard sync/catch-up status for the Autoscaling page.
//
// What's real vs. fabricated here matters, so read this before changing it:
// rdb.q has NO tickerplant-log replay at all - a (re)started RDB does a
// one-time silent warm-load from wdb's scratch files (row count discarded,
// never exposed), then just re-subscribes to the TP and waits for live
// ticks. There is no "replayed N of M records" counter anywhere server-side,
// no per-shard percentage, nothing to poll for that. wdb's `wdbLastWatermark`
// sawtooths by design (it's a durability flush cutoff, not a backlog) and is
// shown as exactly that, never as progress.
//
// The one real, honest "is it catching up" signal that exists is the
// `rdb_to_gateway` transitLag stage - the age of the RDB's newest row vs
// wall clock. web-ui/src/components/RecoveryWatch.jsx already extrapolates
// an ETA from a shrinking trend in that number; this module reuses the exact
// same method (rolling lag history -> linear shrink-rate -> null if flat or
// growing) scoped per-shard for this page, without touching RecoveryWatch.jsx.
const LAG_HISTORY = 5;
const CATCHING_UP_LAG_MS = 2000; // above this, "live" staleness counts as "catching up"

const TIERS = ["tp", "wdb", "rdb", "idb", "hdb"];

/** shardIds: e.g. ["s0","s1"]. historyMap: a Map you own (useRef), keyed by shard id, persisted across polls. */
export function classifyShardSync(shardId, { topo, componentMetrics, transitLag, historyMap }) {
  const states = TIERS.map((t) => topo[`${t}-${shardId}`] || "not_found");
  const containersUp = states.every((s) => s === "running");
  const anyContainerSeen = states.some((s) => s !== "not_found");

  const comp = (componentMetrics || []).find((r) => String(r.shard) === shardId);
  const rdbConnected = comp?.rdbConnected ?? null;
  const wdbConnected = comp?.wdbConnected ?? null;
  const wdbLastWatermark = comp?.wdbLastWatermark ?? null;
  const rdbReconnects = comp?.rdbReconnects ?? null;
  const wdbReconnects = comp?.wdbReconnects ?? null;

  const lagRow = (transitLag || []).find((r) => r.stage === "rdb_to_gateway" && String(r.shard) === shardId);
  const lagMs = lagRow && Number.isFinite(Number(lagRow.lagMs)) ? Number(lagRow.lagMs) : null;

  let eta = null;
  if (lagMs != null) {
    const hist = historyMap.get(shardId) || [];
    const next = [...hist, { t: Date.now(), lag: lagMs }].slice(-LAG_HISTORY);
    historyMap.set(shardId, next);
    if (next.length >= 3) {
      const first = next[0], last = next[next.length - 1];
      const elapsedSec = (last.t - first.t) / 1000;
      const lagDrop = first.lag - last.lag;
      if (elapsedSec > 1 && lagDrop > 0) {
        eta = Math.max(0, Math.round(last.lag / (lagDrop / elapsedSec)));
      }
    }
  } else {
    historyMap.delete(shardId);
  }

  let stage, label, detail;
  if (!anyContainerSeen) {
    stage = -1; label = "Not present"; detail = "no processes reporting for this shard";
  } else if (!containersUp) {
    stage = 0; label = "Provisioning"; detail = `${states.filter((s) => s === "running").length}/${TIERS.length} processes up`;
  } else if (rdbConnected === false || wdbConnected === false) {
    stage = 1; label = "Connecting";
    detail = rdbConnected === false && wdbConnected === false ? "RDB and WDB reconnecting to tickerplant"
      : rdbConnected === false ? "RDB reconnecting to tickerplant" : "WDB reconnecting to tickerplant";
  } else if (lagMs != null && lagMs > CATCHING_UP_LAG_MS) {
    stage = 2; label = "Catching up"; detail = `RDB ${(lagMs / 1000).toFixed(1)}s behind live`;
  } else {
    stage = 3; label = "Live"; detail = "receiving live ticks, low latency";
  }

  return {
    shardId, stage, label, detail, containersUp, states,
    rdbConnected, wdbConnected, rdbReconnects, wdbReconnects, wdbLastWatermark, lagMs, eta,
  };
}

export const SYNC_STAGES = ["Provisioning", "Connecting", "Catching up", "Live"];
