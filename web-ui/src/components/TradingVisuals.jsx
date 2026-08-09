import { useMemo } from "react";

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

export function Candlestick({ bars, height = 280, upColor = "#16c784", downColor = "#f6465d" }) {
  const { path, volMax, lo, hi } = useMemo(() => {
    if (!bars?.length) return { path: [], volMax: 1, lo: 0, hi: 1 };
    const los = bars.map((b) => b.low), his = bars.map((b) => b.high);
    return {
      path: bars,
      volMax: Math.max(1, ...bars.map((b) => b.volume)),
      lo: Math.min(...los),
      hi: Math.max(...his),
    };
  }, [bars]);

  if (!bars?.length) return <div className="muted chart-empty">No trade data yet for this symbol.</div>;

  const priceH = height * 0.72;
  const volH = height * 0.22;
  const gap = height * 0.06;
  const w = 100 / bars.length; // percent width per bar, viewBox is 0-1000 wide
  const range = Math.max(hi - lo, 1e-9);
  const y = (price) => priceH - ((price - lo) / range) * priceH;

  return (
    <svg viewBox={`0 0 1000 ${height}`} preserveAspectRatio="none" className="candlestick-chart" role="img"
         aria-label="Candlestick chart">
      {path.map((b, i) => {
        const cx = (i + 0.5) * w * 10;
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
      <line x1="0" x2="1000" y1={priceH + gap + volH} y2={priceH + gap + volH} stroke="var(--border)" strokeWidth={1} />
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
