import { useEffect, useRef, useState } from "react";

// TradingView's own documented embedding method (their public "Widgets" -
// https://www.tradingview.com/widget/advanced-chart/ - a free script, no
// API key) rather than the undocumented internal iframe URL their widget
// happens to render to today - the script is TradingView's own supported
// surface, so it's the one less likely to silently break on their side.
// Loaded once and cached module-wide (not per mount) since every Markets.jsx
// symbol switch would otherwise re-fetch and re-run the same external script.
let tvScriptPromise = null;
function loadTradingViewScript() {
  if (tvScriptPromise) return tvScriptPromise;
  tvScriptPromise = new Promise((resolve, reject) => {
    if (window.TradingView) { resolve(); return; }
    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/tv.js";
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("failed to load TradingView's chart script"));
    document.head.appendChild(script);
  });
  return tvScriptPromise;
}

// Best-effort ticker -> TradingView "EXCHANGE:SYMBOL" mapping. The free
// widget has no "resolve this ticker for me" API, so this is a heuristic,
// not a guarantee - wrong for e.g. NYSE-listed names guessed as NASDAQ, or
// a crypto venue TradingView doesn't mirror under COINBASE:. The override
// input below exists specifically to cover what this guess gets wrong.
export function guessTradingViewSymbol(symbol) {
  if (!symbol) return "";
  const s = symbol.toUpperCase();
  const cryptoMatch = s.match(/^([A-Z]{2,10})[-/]([A-Z]{2,10})$/);
  if (cryptoMatch) {
    const [, base, quote] = cryptoMatch;
    return `COINBASE:${base}${quote}`;
  }
  return `NASDAQ:${s}`;
}

// Renders TradingView's own live chart for `symbol` - TradingView's own
// market data (their free tier, typically delayed for many exchanges), NOT
// this platform's internal tick feed. Exists for cross-checking and
// TradingView's own indicator library, not as a replacement for the Chart
// tab above (which IS this platform's real internal data).
export default function TradingViewChart({ symbol }) {
  const containerRef = useRef(null);
  const [tvSymbol, setTvSymbol] = useState(() => guessTradingViewSymbol(symbol));
  const [status, setStatus] = useState("loading"); // loading / ready / error
  const widgetId = useRef(`tv_chart_${Math.random().toString(36).slice(2)}`).current;

  useEffect(() => { setTvSymbol(guessTradingViewSymbol(symbol)); }, [symbol]);

  useEffect(() => {
    if (!tvSymbol) return undefined;
    let cancelled = false;
    setStatus("loading");
    loadTradingViewScript().then(() => {
      if (cancelled || !containerRef.current || !window.TradingView) return;
      containerRef.current.innerHTML = "";
      // eslint-disable-next-line no-new
      new window.TradingView.widget({
        autosize: true,
        symbol: tvSymbol,
        interval: "60",
        timezone: "Etc/UTC",
        theme: "dark",
        style: "1",
        locale: "en",
        toolbar_bg: "#0E131A",
        enable_publishing: false,
        allow_symbol_change: false,
        container_id: widgetId,
      });
      setStatus("ready");
    }).catch(() => { if (!cancelled) setStatus("error"); });
    return () => { cancelled = true; };
  }, [tvSymbol, widgetId]);

  return (
    <div>
      <p className="muted" style={{ marginTop: 0, fontSize: "0.8rem" }}>
        External reference chart from TradingView — this is TradingView's own market data (may differ from,
        or lag, this platform's live internal prices shown on the Chart tab), for cross-checking and access
        to TradingView's own indicator library.
      </p>
      <div className="form-row" style={{ marginBottom: "0.5rem" }}>
        <label className="muted">TradingView symbol (best-effort guess — override if wrong)
          <input value={tvSymbol} onChange={(e) => setTvSymbol(e.target.value.toUpperCase())}
                 style={{ width: "12rem", display: "block" }} />
        </label>
      </div>
      {status === "error" && <p className="error">Could not load TradingView's chart script — check your network/ad-blocker and retry.</p>}
      <div id={widgetId} ref={containerRef} style={{ height: "32rem", width: "100%" }} />
    </div>
  );
}
