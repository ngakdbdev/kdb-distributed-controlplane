import { api } from "../api.js";

// Shared helpers used across the trading pages (Markets, Orders, Portfolio,
// Bot) - pulled out of what used to be one 994-line Trading.jsx so each page
// can stay focused on one job.

export function fmt(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function changePct(prices) {
  if (prices.length < 2) return 0;
  const first = prices[0] || 1;
  const last = prices[prices.length - 1];
  return first ? ((last - first) / first) * 100 : 0;
}

/** Real trade prints for one or more symbols, routed across shard RDBs when possible. */
export async function fetchTradeTape(symbols, limit = 1200) {
  const uniqueSymbols = [...new Set((symbols || []).filter(Boolean).map((sym) => sym.toUpperCase()))];
  const targetMeta = await api.queryTargets();
  const rdbTargets = (targetMeta?.targets || []).map((target) => target.id).filter((id) => id.startsWith("rdb-"));
  const targets = rdbTargets.length ? rdbTargets : ["gateway"];
  const sourceLabel = rdbTargets.length ? `${rdbTargets.length} RDB shards` : "gateway";
  const query = uniqueSymbols.length === 1
    ? `select time, price, size from trade where sym=\`${uniqueSymbols[0]}`
    : `select time, sym, price, size from trade where sym in ${uniqueSymbols.map((sym) => `\`${sym}`).join("")}`;
  const res = await api.runQuery({ targets, query, limit });
  return { res, sourceLabel };
}

export function summarizePressure(snapshot) {
  const rows = (snapshot?.componentMetrics || []).filter((row) => {
    const queue = Number(row.tpQueue || 0);
    const lag = Number(row.tpSubLag || 0);
    return queue > 0 || lag > 0 || row.rdbConnected === false || row.wdbConnected === false;
  });
  if (!rows.length) return { elevated: false, rows: [], summary: "No active load shedding or subscriber pressure." };
  const labels = rows.map((row) => row.shard).join(", ");
  const maxQueue = Math.max(...rows.map((row) => Number(row.tpQueue || 0)));
  const maxLag = Math.max(...rows.map((row) => Number(row.tpSubLag || 0)));
  return {
    elevated: true,
    rows,
    summary: `${labels} under pressure: queue depth ${fmt(maxQueue, 0)}, subscriber lag ${fmt(maxLag, 0)}.`,
  };
}

export function normalizeSeries(prices) {
  if (!prices.length) return [];
  const base = prices[0] || 1;
  return prices.map((price) => (price / base) * 100);
}

export function seriesReturns(prices) {
  if (prices.length < 2) return [];
  return prices.slice(1).map((price, idx) => {
    const prev = prices[idx] || 1;
    return prev ? (price - prev) / prev : 0;
  });
}

export function correlation(left, right) {
  const size = Math.min(left.length, right.length, 24);
  if (size < 2) return 0;
  const xs = left.slice(-size);
  const ys = right.slice(-size);
  const xMean = xs.reduce((sum, value) => sum + value, 0) / size;
  const yMean = ys.reduce((sum, value) => sum + value, 0) / size;
  let cov = 0, xVar = 0, yVar = 0;
  for (let idx = 0; idx < size; idx += 1) {
    const dx = xs[idx] - xMean;
    const dy = ys[idx] - yMean;
    cov += dx * dy;
    xVar += dx * dx;
    yVar += dy * dy;
  }
  if (!xVar || !yVar) return 0;
  return cov / Math.sqrt(xVar * yVar);
}

export function buildCorrelationMatrix(members) {
  return members.map((left) => ({
    symbol: left.symbol,
    values: members.map((right) => ({ symbol: right.symbol, value: correlation(left.returns, right.returns) })),
  }));
}

export function correlationPairs(matrix) {
  const pairs = [];
  for (let i = 0; i < matrix.length; i += 1) {
    for (let j = i + 1; j < matrix.length; j += 1) {
      const value = matrix[i].values[j]?.value;
      if (!Number.isFinite(value)) continue;
      pairs.push({ label: `${matrix[i].symbol}/${matrix[j].symbol}`, value });
    }
  }
  return pairs.sort((left, right) => right.value - left.value);
}

export function corrClass(value) {
  if (value >= 0.7) return "corr-strong";
  if (value >= 0.25) return "corr-mid";
  if (value <= -0.35) return "corr-inverse";
  return "corr-flat";
}

export function paletteColor(index) {
  const colors = ["#4c8bff", "#2dd4bf", "#f6465d", "#f2a93c", "#8b7bff", "#16c784"];
  return colors[index % colors.length];
}
