// Calendar-horizon forecast (next 10m / 15m / 30m / 1h), built client-side
// from real trade timestamps.
//
// The backend's /trading/forecast (control-api/app/market.py) projects over
// N "steps" of a raw price array with no time semantics at all - a step is
// literally the next raw trade print, so "horizon: 10" means "the next 10
// ticks," which could be a fraction of a second or several minutes away
// depending on feed rate. That's why the old forecast card didn't make
// sense: its x-axis had no real meaning.
//
// This bucketizes real trade prints into 1-minute bars first (using each
// bar's last print as its close, same idea as candlestick bucketing), then
// fits the same drift+volatility-over-time model the backend uses
// (geometric random walk: expected = last * exp(drift*h), band = z*vol*sqrt(h))
// but on genuine per-minute returns, so "+30m" actually means 30 real
// minutes. Deliberately mirrors the backend's math shape for consistency
// with the "Ann. vol" stat already shown elsewhere, just correctly time-scaled.
const HORIZONS_MIN = [10, 15, 30, 60];
const BUCKET_MS = 60_000;

export function buildTimeForecast(rows, { horizons = HORIZONS_MIN, z = 1.645 } = {}) {
  const clean = (rows || [])
    .filter((r) => r.time && r.price != null)
    .map((r) => ({ t: new Date(r.time).getTime(), price: Number(r.price) }))
    .filter((r) => Number.isFinite(r.t) && Number.isFinite(r.price))
    .sort((a, b) => a.t - b.t);

  if (clean.length < 3) {
    return {
      points: [], method: "insufficient-data", trend: "flat", sampledMinutes: 0,
      sufficientData: false, last: clean[clean.length - 1]?.price ?? null,
      disclaimer: "Not enough recent trade prints to forecast yet.",
    };
  }

  const bars = [];
  let bucketStart = Math.floor(clean[0].t / BUCKET_MS) * BUCKET_MS;
  let cur = null;
  for (const row of clean) {
    while (row.t >= bucketStart + BUCKET_MS) { bucketStart += BUCKET_MS; cur = null; }
    if (!cur) { cur = { t: bucketStart, close: row.price }; bars.push(cur); }
    cur.close = row.price;
  }
  const closes = bars.map((b) => b.close);
  const sampledMinutes = bars.length;
  const last = closes[closes.length - 1];

  if (closes.length < 3) {
    return {
      points: [], method: "insufficient-data", trend: "flat", sampledMinutes,
      sufficientData: false, last,
      disclaimer: "Recent prints span too little real time (under a few minutes) to project a calendar-time forecast yet.",
    };
  }

  const rets = [];
  for (let i = 1; i < closes.length; i += 1) {
    if (closes[i - 1] > 0) rets.push(Math.log(closes[i] / closes[i - 1]));
  }
  const n = rets.length;
  const driftPerMin = rets.reduce((a, b) => a + b, 0) / n;
  const variance = n > 1 ? rets.reduce((a, b) => a + (b - driftPerMin) ** 2, 0) / (n - 1) : 0;
  const volPerMin = Math.sqrt(variance);
  const trend = driftPerMin > 0.0002 ? "up" : driftPerMin < -0.0002 ? "down" : "flat";

  const points = horizons.map((h) => {
    const expected = last * Math.exp(driftPerMin * h);
    const spread = z * volPerMin * Math.sqrt(h);
    return {
      horizonMin: h,
      label: h < 60 ? `+${h}m` : `+${h / 60}h`,
      expected,
      lower: last * Math.exp(driftPerMin * h - spread),
      upper: last * Math.exp(driftPerMin * h + spread),
      deltaPct: ((expected - last) / last) * 100,
    };
  });

  const longestHorizon = Math.max(...horizons);
  const sufficientData = sampledMinutes >= longestHorizon / 3;

  return {
    points, method: "drift + volatility over 1-minute bars", trend, last,
    driftPerMin, volPerMin, sampledMinutes, sufficientData,
    disclaimer: sufficientData
      ? "Statistical projection from recent price drift and volatility over 1-minute bars — not advice, and the confidence band widens fast the further out you look."
      : `Based on only ~${sampledMinutes} minute(s) of sampled trades — anything past that is extrapolated well beyond the sampled window, treat longer horizons here as low-confidence.`,
  };
}
