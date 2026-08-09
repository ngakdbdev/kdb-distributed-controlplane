import { fmt } from "../lib/tradingCore.js";

// Self-explanatory replacement for the old bare-SVG forecast card: labeled
// axes, a "now" anchor point, a shaded confidence cone instead of two
// unlabeled dashed lines, a legend, and four at-a-glance horizon chips
// (10m/15m/30m/1h) so the headline numbers don't require reading the chart
// at all.
export default function ForecastPanel({ forecast, symbol }) {
  if (!forecast || forecast.method === "insufficient-data" || !forecast.points?.length) {
    return (
      <div className="card">
        <div className="section-head"><h3>Forecast</h3></div>
        <p className="muted">{forecast?.disclaimer || `Waiting for enough ${symbol || ""} trade prints to forecast.`}</p>
      </div>
    );
  }

  const { points, last, trend, sufficientData, disclaimer } = forecast;
  const allVals = [last, ...points.flatMap((p) => [p.lower, p.upper])];
  const min = Math.min(...allVals);
  const max = Math.max(...allVals);
  const pad = (max - min) * 0.12 || last * 0.01 || 1;
  const yMin = min - pad, yMax = max + pad;
  const W = 560, H = 200, L = 46, R = 16, T = 14, B = 26;
  const plotW = W - L - R, plotH = H - T - B;
  const xs = points.map((_, i) => L + ((i + 1) / points.length) * plotW);
  const x0 = L;
  const y = (v) => T + (1 - (v - yMin) / (yMax - yMin)) * plotH;

  const expectedPath = `M${x0},${y(last)} ` + points.map((p, i) => `L${xs[i]},${y(p.expected)}`).join(" ");
  const upperPath = points.map((p, i) => `${xs[i]},${y(p.upper)}`).join(" ");
  const lowerPath = points.map((p, i) => `${xs[i]},${y(p.lower)}`).join(" ");
  const bandPath = `M${x0},${y(last)} L${upperPath} L${points.slice().reverse().map((p, i) =>
    `${xs[points.length - 1 - i]},${y(p.lower)}`).join(" L")} L${x0},${y(last)} Z`;

  return (
    <div className="card">
      <div className="section-head">
        <h3>Forecast{symbol ? <span className="muted" style={{ fontWeight: 400 }}> · {symbol}</span> : null}</h3>
        <span className={`signal-pill ${trend}`}>{trend === "up" ? "momentum up" : trend === "down" ? "momentum down" : "flat"}</span>
      </div>

      <div className="horizon-chip-row">
        {points.map((p) => (
          <div className="horizon-chip" key={p.horizonMin}>
            <div className="horizon-chip-label">{p.label}</div>
            <div className={`horizon-chip-value ${p.deltaPct >= 0 ? "up" : "down"}`}>{fmt(p.expected)}</div>
            <div className={`horizon-chip-delta ${p.deltaPct >= 0 ? "up" : "down"}`}>
              {p.deltaPct >= 0 ? "▲" : "▼"} {fmt(Math.abs(p.deltaPct), 2)}%
            </div>
            <div className="horizon-chip-range">{fmt(p.lower)}–{fmt(p.upper)}</div>
          </div>
        ))}
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="forecast-cone" role="img" aria-label="Price forecast cone">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={L} x2={W - R} y1={T + f * plotH} y2={T + f * plotH} stroke="var(--border)" strokeDasharray="3 3" />
            <text x={4} y={T + f * plotH + 3} fontSize="9" fill="var(--muted)">{fmt(yMax - f * (yMax - yMin))}</text>
          </g>
        ))}
        <path d={bandPath} fill="var(--accent-weak)" stroke="none" />
        <path d={expectedPath} fill="none" stroke="var(--accent)" strokeWidth="2" />
        <circle cx={x0} cy={y(last)} r="3.5" fill="var(--text)" />
        <text x={x0} y={H - 6} fontSize="9" fill="var(--muted)" textAnchor="start">now</text>
        {points.map((p, i) => (
          <text key={p.horizonMin} x={xs[i]} y={H - 6} fontSize="9" fill="var(--muted)" textAnchor="middle">{p.label}</text>
        ))}
      </svg>

      <div className="forecast-legend">
        <span><i className="legend-swatch line" /> expected path</span>
        <span><i className="legend-swatch band" /> ~90% confidence range</span>
      </div>

      <div className={`forecast-disclaimer ${sufficientData ? "" : "low-confidence"}`}>
        ⚠ {disclaimer}
      </div>
    </div>
  );
}
