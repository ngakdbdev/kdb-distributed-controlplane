import { useMemo } from "react";
import { bollingerBands, ema, macd } from "../lib/indicators.js";

// Trading-grade visuals built on REAL trade prints (time/price/size from the
// `trade` table) - no recharts here, hand-rolled SVG, because recharts has no
// native candlestick and forcing one through its Bar/shape API reads worse
// than just drawing it. Kept intentionally small and dependency-free.

/** rows: [{time: ISOString, price, size}] (any order) -> ascending OHLCV bars. */
export function bucketOHLC(rows, bucketMs = 5000) {
  const clean = (rows || [])
    .filter((r) => r.time && r.price != null)
    .map((r) => ({ t: new Date(r.time).getTime(), price: Number(r.price), size: Number(r.size) || 0 }))
    .sort((a, b) => a.t - b.t);
  if (!clean.length) return [];

  const bars = [];
  let bucketStart = Math.floor(clean[0].t / bucketMs) * bucketMs;
  let cur = null;
  for (const row of clean) {
    while (row.t >= bucketStart + bucketMs) {
      bucketStart += bucketMs;
      cur = null;
    }
    if (!cur) {
      cur = { t: bucketStart, open: row.price, high: row.price, low: row.price, close: row.price, volume: 0 };
      bars.push(cur);
    }
    cur.high = Math.max(cur.high, row.price);
    cur.low = Math.min(cur.low, row.price);
    cur.close = row.price;
    cur.volume += row.size;
  }
  return bars;
}

// EMA periods drawn as overlay lines when requested - matching the labeling
// convention every charting platform uses ("EMA 9", "EMA 21", ...): the
// period IS the label, not an arbitrary color name.
const EMA_OVERLAY_COLORS = { 9: "#f0b90b", 21: "#8b5cf6" };

export function Candlestick({ bars, height = 280, upColor = "#16c784", downColor = "#f6465d",
                             showBollinger = false, emaPeriods = [] }) {
  const { path, volMax, lo, hi, bb, emaLines } = useMemo(() => {
    if (!bars?.length) return { path: [], volMax: 1, lo: 0, hi: 1, bb: null, emaLines: {} };
    const closes = bars.map((b) => b.close);
    const bbands = showBollinger ? bollingerBands(closes) : null;
    const emaLines = {};
    for (const p of emaPeriods) emaLines[p] = ema(closes, p);
    const los = bars.map((b) => b.low), his = bars.map((b) => b.high);
    // widen the price axis to fit Bollinger bands if they extend past the
    // raw candle range, so an overlay never silently clips off-chart
    const extra = bbands ? [...bbands.upper, ...bbands.lower].filter((v) => v != null) : [];
    return {
      path: bars,
      volMax: Math.max(1, ...bars.map((b) => b.volume)),
      lo: Math.min(...los, ...(extra.length ? extra : [Infinity])),
      hi: Math.max(...his, ...(extra.length ? extra : [-Infinity])),
      bb: bbands,
      emaLines,
    };
  }, [bars, showBollinger, emaPeriods]);

  if (!bars?.length) return <div className="muted chart-empty">No trade data yet for this symbol.</div>;

  const priceH = height * 0.72;
  const volH = height * 0.22;
  const gap = height * 0.06;
  const w = 100 / bars.length; // percent width per bar, viewBox is 0-1000 wide
  const range = Math.max(hi - lo, 1e-9);
  const y = (price) => priceH - ((price - lo) / range) * priceH;
  const cxOf = (i) => (i + 0.5) * w * 10;

  // aligned-array -> SVG polyline points, skipping nulls (not enough
  // history yet) so the line only draws where the indicator is actually
  // defined rather than plotting a false leading value at 0.
  const linePoints = (values) => values
    .map((v, i) => (v == null ? null : `${cxOf(i)},${y(v)}`))
    .filter(Boolean)
    .join(" ");

  return (
    <svg viewBox={`0 0 1000 ${height}`} preserveAspectRatio="none" className="candlestick-chart" role="img"
         aria-label="Candlestick chart">
      {bb && (
        <>
          <polyline points={linePoints(bb.upper)} fill="none" stroke="var(--muted)" strokeWidth={1} strokeDasharray="3,3" opacity={0.7} />
          <polyline points={linePoints(bb.middle)} fill="none" stroke="var(--muted)" strokeWidth={1} opacity={0.5} />
          <polyline points={linePoints(bb.lower)} fill="none" stroke="var(--muted)" strokeWidth={1} strokeDasharray="3,3" opacity={0.7} />
        </>
      )}
      {path.map((b, i) => {
        const cx = cxOf(i);
        const bw = Math.max(2, w * 10 * 0.6);
        const up = b.close >= b.open;
        const color = up ? upColor : downColor;
        const bodyTop = y(Math.max(b.open, b.close));
        const bodyBot = y(Math.min(b.open, b.close));
        const volY = priceH + gap + (volH - (b.volume / volMax) * volH);
        const volBarH = (b.volume / volMax) * volH;
        return (
          <g key={b.t}>
            <line x1={cx} x2={cx} y1={y(b.high)} y2={y(b.low)} stroke={color} strokeWidth={1} />
            <rect x={cx - bw / 2} y={bodyTop} width={bw} height={Math.max(1, bodyBot - bodyTop)} fill={color} />
            <rect x={cx - bw / 2} y={volY} width={bw} height={Math.max(0.5, volBarH)} fill={color} opacity={0.35} />
          </g>
        );
      })}
      {Object.entries(emaLines).map(([period, values]) => (
        <polyline key={period} points={linePoints(values)} fill="none"
                  stroke={EMA_OVERLAY_COLORS[period] || "var(--accent)"} strokeWidth={1.5} />
      ))}
      <line x1="0" x2="1000" y1={priceH + gap + volH} y2={priceH + gap + volH} stroke="var(--border)" strokeWidth={1} />
    </svg>
  );
}

/** MACD subplot - histogram bars + macd/signal lines, same viewBox
 * convention as Candlestick so it visually aligns as a chart "subplot"
 * beneath it. Standard 12/26/9 defaults. */
export function MacdPanel({ bars, height = 90 }) {
  const { macdLine, signal, histogram, lo, hi } = useMemo(() => {
    if (!bars?.length) return { macdLine: [], signal: [], histogram: [], lo: -1, hi: 1 };
    const { macd: m, signal: s, histogram: h } = macd(bars.map((b) => b.close));
    const defined = [...m, ...s, ...h].filter((v) => v != null);
    const mag = defined.length ? Math.max(...defined.map(Math.abs), 1e-9) : 1;
    return { macdLine: m, signal: s, histogram: h, lo: -mag, hi: mag };
  }, [bars]);

  if (!bars?.length) return null;

  const w = 100 / bars.length;
  const range = Math.max(hi - lo, 1e-9);
  const y = (v) => height - ((v - lo) / range) * height;
  const zero = y(0);
  const cxOf = (i) => (i + 0.5) * w * 10;
  const linePoints = (values) => values
    .map((v, i) => (v == null ? null : `${cxOf(i)},${y(v)}`))
    .filter(Boolean)
    .join(" ");

  return (
    <svg viewBox={`0 0 1000 ${height}`} preserveAspectRatio="none" className="macd-chart" role="img"
         aria-label="MACD indicator">
      <line x1="0" x2="1000" y1={zero} y2={zero} stroke="var(--border)" strokeWidth={1} />
      {histogram.map((v, i) => {
        if (v == null) return null;
        const bw = Math.max(2, w * 10 * 0.6);
        const barY = v >= 0 ? y(v) : zero;
        const barH = Math.max(0.5, Math.abs(y(v) - zero));
        return <rect key={i} x={cxOf(i) - bw / 2} y={barY} width={bw} height={barH}
                     fill={v >= 0 ? "#16c784" : "#f6465d"} opacity={0.6} />;
      })}
      <polyline points={linePoints(macdLine)} fill="none" stroke="#f0b90b" strokeWidth={1.3} />
      <polyline points={linePoints(signal)} fill="none" stroke="#8b5cf6" strokeWidth={1.3} />
    </svg>
  );
}

/** rows: recent trades, most-recent-first or last; renders most-recent-first. */
export function LiveTape({ rows, maxRows = 30 }) {
  const recent = useMemo(() => {
    return [...(rows || [])]
      .filter((r) => r.time && r.price != null)
      .sort((a, b) => new Date(b.time) - new Date(a.time))
      .slice(0, maxRows);
  }, [rows, maxRows]);

  if (!recent.length) return <div className="muted chart-empty">No trades yet.</div>;

  return (
    <div className="live-tape">
      {recent.map((r, i) => {
        const prev = recent[i + 1];
        const up = prev ? r.price >= prev.price : true;
        return (
          <div className="tape-row" key={`${r.time}-${i}`}>
            <span className="tape-time">{new Date(r.time).toLocaleTimeString([], { hour12: false })}</span>
            <span className={`tape-price ${up ? "up" : "down"}`}>{Number(r.price).toFixed(2)}</span>
            <span className="tape-size">{Number(r.size || 0).toLocaleString()}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Illustrative NBBO derived from the last trade price - NOT a real order
 * book (this pipeline has no L1/L2 quote feed, only trade prints). Spread
 * widens/narrows with recent realized volatility so it's not static, but it
 * is explicitly synthetic and labeled as such everywhere it's shown. */
export function syntheticDepth(rows, levels = 5) {
  const prices = (rows || []).map((r) => Number(r.price)).filter((v) => !Number.isNaN(v));
  if (prices.length < 2) return null;
  const last = prices[prices.length - 1];
  const returns = [];
  for (let i = 1; i < prices.length; i++) returns.push(Math.abs(prices[i] - prices[i - 1]) / prices[i - 1]);
  const avgMove = returns.reduce((a, b) => a + b, 0) / returns.length;
  const spreadBp = Math.max(1, avgMove * 10000 * 2); // widen with realized vol, floor at 1bp
  const tick = last * (spreadBp / 10000) / 2;

  const bids = [], asks = [];
  for (let i = 0; i < levels; i++) {
    bids.push({ price: last - tick * (i + 1), size: Math.round(200 + Math.random() * 800 * (levels - i) / levels) });
    asks.push({ price: last + tick * (i + 1), size: Math.round(200 + Math.random() * 800 * (levels - i) / levels) });
  }
  return { mid: last, bids, asks };
}

export function DepthPanel({ rows, levels = 5 }) {
  const book = useMemo(() => syntheticDepth(rows, levels), [rows, levels]);
  if (!book) return <div className="muted chart-empty">Not enough trade data yet.</div>;
  const maxSize = Math.max(...book.bids.map((b) => b.size), ...book.asks.map((a) => a.size));

  return (
    <div className="depth-panel">
      <div className="depth-disclaimer">
        Illustrative NBBO derived from recent trade prints &mdash; not a live order book.
        This feed publishes trade prints only; a real L1/L2 book would plug in here.
      </div>
      <div className="depth-ladder">
        <div className="depth-side depth-bids">
          {book.bids.map((b) => (
            <div className="depth-row" key={b.price}>
              <span className="depth-bar" style={{ width: `${(b.size / maxSize) * 100}%` }} />
              <span className="depth-size">{b.size}</span>
              <span className="depth-price down">{b.price.toFixed(2)}</span>
            </div>
          ))}
        </div>
        <div className="depth-side depth-asks">
          {book.asks.map((a) => (
            <div className="depth-row" key={a.price}>
              <span className="depth-price up">{a.price.toFixed(2)}</span>
              <span className="depth-size">{a.size}</span>
              <span className="depth-bar" style={{ width: `${(a.size / maxSize) * 100}%` }} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
