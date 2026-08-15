// indicators.js - standard technical-indicator math over REAL OHLC bars
// (TradingVisuals.jsx's bucketOHLC output, itself built from real trade
// prints - see that file's own comment on why there's no charting library
// here). Pure functions, no rendering, no synthetic data: same "pure lib +
// dumb SVG component" split every other chart in this app already uses.
// Every function returns an array the SAME LENGTH as its input, with
// `null` in positions that don't have enough history yet to compute a
// value - callers render those as gaps, never a fabricated leading value.

/** Exponential moving average. Seeds with a plain average of the first
 * `period` values (the standard convention), then recurs forward. */
export function ema(values, period) {
  const out = new Array(values.length).fill(null);
  if (values.length < period) return out;
  const k = 2 / (period + 1);
  let seed = 0;
  for (let i = 0; i < period; i++) seed += values[i];
  seed /= period;
  out[period - 1] = seed;
  let prev = seed;
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

function sma(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

/** Bollinger Bands: {middle, upper, lower}, each aligned arrays. Standard
 * default (20-period SMA, 2 standard deviations) - the same defaults every
 * charting platform ships as its own default. */
export function bollingerBands(closes, period = 20, mult = 2) {
  const middle = sma(closes, period);
  const upper = new Array(closes.length).fill(null);
  const lower = new Array(closes.length).fill(null);
  for (let i = period - 1; i < closes.length; i++) {
    let variance = 0;
    for (let j = i - period + 1; j <= i; j++) variance += (closes[j] - middle[i]) ** 2;
    const sd = Math.sqrt(variance / period);
    upper[i] = middle[i] + mult * sd;
    lower[i] = middle[i] - mult * sd;
  }
  return { middle, upper, lower };
}

/** MACD: {macd, signal, histogram}, each aligned arrays. Standard defaults
 * (12/26/9). macd = EMA(fast) - EMA(slow); signal = EMA(macd, signalPeriod);
 * histogram = macd - signal. */
export function macd(closes, fast = 12, slow = 26, signalPeriod = 9) {
  const emaFast = ema(closes, fast);
  const emaSlow = ema(closes, slow);
  const macdLine = closes.map((_, i) =>
    emaFast[i] != null && emaSlow[i] != null ? emaFast[i] - emaSlow[i] : null);
  // EMA of the MACD line itself - only defined once macdLine has real
  // values, so feed ema() the compacted (non-null) series and re-align.
  const compact = [];
  const compactIdx = [];
  macdLine.forEach((v, i) => { if (v != null) { compact.push(v); compactIdx.push(i); } });
  const signalCompact = ema(compact, signalPeriod);
  const signal = new Array(closes.length).fill(null);
  signalCompact.forEach((v, j) => { if (v != null) signal[compactIdx[j]] = v; });
  const histogram = closes.map((_, i) =>
    macdLine[i] != null && signal[i] != null ? macdLine[i] - signal[i] : null);
  return { macd: macdLine, signal, histogram };
}
