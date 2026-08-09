import { useId } from "react";

// Minimal inline sparkline for Trading212-style "big number + trend" rows.
// Pure SVG (no chart library) since it just needs to render fast inside
// small list rows and hero cards, not offer interactivity.
export default function Sparkline({ data = [], width = 96, height = 32, variant, strokeWidth = 2 }) {
  const gradId = useId();
  const values = data.filter((v) => typeof v === "number" && !Number.isNaN(v));
  const trend = variant || (values.length >= 2
    ? (values[values.length - 1] > values[0] ? "up" : values[values.length - 1] < values[0] ? "down" : "flat")
    : "flat");

  if (values.length < 2) {
    return <svg className="spark" width={width} height={height} aria-hidden="true" />;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = strokeWidth;
  const stepX = (width - pad * 2) / (values.length - 1);
  const points = values.map((v, i) => {
    const x = pad + i * stepX;
    const y = pad + (1 - (v - min) / range) * (height - pad * 2);
    return [x, y];
  });
  const linePath = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const areaPath = `${linePath} L${points[points.length - 1][0].toFixed(2)},${height} L${points[0][0].toFixed(2)},${height} Z`;
  const color = trend === "up" ? "var(--success)" : trend === "down" ? "var(--danger)" : "var(--muted)";

  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.28} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradId})`} stroke="none" />
      <path className={`spark-line ${trend}`} d={linePath} strokeWidth={strokeWidth} />
    </svg>
  );
}
