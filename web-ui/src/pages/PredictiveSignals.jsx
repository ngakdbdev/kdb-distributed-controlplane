import { useEffect, useState } from "react";
import { api } from "../api.js";
import SymbolPicker from "../components/SymbolPicker.jsx";
import { fmt } from "../lib/tradingCore.js";

const REFRESH_MS = 15000; // signals/news are minutes-scale by nature (see
                          // signal_composite.py/news_feed.py) - no reason to
                          // poll at the 1s cadence Markets/Metrics use

const FACTOR_LABELS = {
  momentum: "Momentum",
  vol_scaled_momentum: "Vol-scaled momentum",
  mean_reversion: "Mean reversion",
  order_flow_imbalance: "Order-flow imbalance",
  news_sentiment: "News sentiment",
};

const DEFAULT_SYMS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"];

// The Predictive Signals page - a weighted, multi-factor composite score per
// symbol (control-api/app/signal_composite.py), a real aggregated news feed
// (app/news_feed.py), and portfolio-wide sentiment. Every number here comes
// from a real, documented technique applied to real data (recent trade
// prints, real news articles) - see signal_composite.py's own module
// docstring for the honest "this is systematic/quantitative trading, not
// HFT" framing, and news_feed.py's for the region-tagging caveat (a
// heuristic, not real geo-data). Nothing here is a trade recommendation;
// same spirit as market.py's own forecast disclaimer.
export default function PredictiveSignals({ onNavigate }) {
  const [syms, setSyms] = useState(DEFAULT_SYMS);
  const [signals, setSignals] = useState(null);
  const [weights, setWeights] = useState({});
  const [newsSources, setNewsSources] = useState({});
  const [news, setNews] = useState([]);
  const [portfolioSentiment, setPortfolioSentiment] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let closed = false;
    async function pull() {
      try {
        const [sig, newsResp, port] = await Promise.all([
          api.predictiveSignals(syms.join(",")),
          api.newsFeed(syms.join(","), 20),
          api.portfolioSentiment(),
        ]);
        if (closed) return;
        setSignals(sig.signals);
        setWeights(sig.weights);
        setNewsSources(newsResp.sources);
        setNews(newsResp.items);
        setPortfolioSentiment(port);
        setError("");
      } catch (err) {
        if (!closed) setError(String(err).replace(/^Error:\s*/, ""));
      } finally {
        if (!closed) setLoading(false);
      }
    }
    pull();
    const id = setInterval(pull, REFRESH_MS);
    return () => { closed = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syms.join(",")]);

  const sentimentTone = (score) => (score > 0.15 ? "up" : score < -0.15 ? "down" : "");

  return (
    <div className="page">
      <h2>Predictive Signals</h2>
      <p className="muted">
        A weighted, multi-factor composite score per symbol — momentum, volatility-scaled momentum,
        mean reversion, order-flow imbalance, and news sentiment, each computed from real data and
        blended with documented (not backtested) default weights. <strong>Not a trade recommendation</strong> —
        see each factor's own caveats below. <a href="#" onClick={(e) => { e.preventDefault(); onNavigate?.("bot"); }}>
        The bot</a> can optionally use this as an additional screen, alongside its own proven momentum logic.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="card">
        <div className="form-row">
          <div style={{ flex: 1, minWidth: "16rem" }}>
            <SymbolPicker value={syms} onChange={(v) => setSyms(v.slice(0, 12))} placeholder="add a symbol to score…" />
          </div>
        </div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="kpi-label">Portfolio sentiment</div>
          <div className={`kpi-value ${sentimentTone(portfolioSentiment?.weighted_sentiment || 0)}`}>
            {portfolioSentiment ? fmt(portfolioSentiment.weighted_sentiment, 2) : "—"}
          </div>
          <div className="kpi-sub">{portfolioSentiment?.n_positions ?? 0} position{portfolioSentiment?.n_positions === 1 ? "" : "s"}, value-weighted</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">News sources active</div>
          <div className="kpi-value" style={{ fontSize: "1.1rem" }}>
            {Object.entries(newsSources).filter(([, on]) => on).map(([k]) => k).join(", ") || "none configured"}
          </div>
          <div className="kpi-sub">Finnhub (keyword sentiment) / Alpha Vantage (model sentiment)</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Symbols scored</div>
          <div className="kpi-value">{signals?.length ?? 0}</div>
          <div className="kpi-sub">of {syms.length} requested</div>
        </div>
      </div>

      <div className="card">
        <div className="section-head"><h3>Composite signal</h3></div>
        {loading && !signals ? <p className="muted">Loading…</p> : !signals?.length ? (
          <p className="muted">No scoreable data yet for these symbols — add symbols with recent trade prints above.</p>
        ) : (
          <div>
            {signals.map((row) => (
              <div className="list-row" key={row.symbol} style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
                <span className="list-row-icon">{row.symbol.slice(0, 2)}</span>
                <div className="list-row-main" style={{ minWidth: "8rem" }}>
                  <div className="list-row-title">{row.symbol}</div>
                  <div className={`list-row-sub ${sentimentTone(row.composite_score)}`}>
                    composite {fmt(row.composite_score, 3)}
                  </div>
                </div>
                <div className="factor-chip-row">
                  {Object.entries(row.factors).map(([key, f]) => (
                    <span key={key}
                          className={`factor-chip ${!f.available ? "unavailable" : f.value > 0.05 ? "up" : f.value < -0.05 ? "down" : ""}`}
                          title={`${FACTOR_LABELS[key]}: ${f.available ? fmt(f.value, 3) : "no data"} (weight ${fmt(f.weight * 100, 0)}%)`}>
                      {FACTOR_LABELS[key]}: {f.available ? fmt(f.value, 2) : "n/a"}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="muted" style={{ fontSize: "0.76rem", marginTop: "0.6rem" }}>
          Default weights: {Object.entries(weights).map(([k, w]) => `${FACTOR_LABELS[k] || k} ${fmt(w * 100, 0)}%`).join(", ")}.
          A symbol missing a factor (e.g. no news yet) has that factor drop out of its own blend rather than
          score as bearish — see signal_composite.py.
        </div>
      </div>

      <div className="card">
        <div className="section-head"><h3>News</h3><span className="muted">Real headlines, region tags are a best-effort heuristic — see below</span></div>
        {news.length === 0 ? (
          <p className="muted">No news configured or returned yet — set FINNHUB_API_KEY (and optionally ALPHAVANTAGE_API_KEY) on the control-api deployment.</p>
        ) : (
          <div className="news-feed">
            {news.map((a, i) => (
              <a className="news-item" key={i} href={a.url || "#"} target="_blank" rel="noreferrer">
                <div className="news-item-head">
                  <span className={`news-sentiment-pill ${sentimentTone(a.sentiment_score)}`}>
                    {a.sentiment_score > 0 ? "▲" : a.sentiment_score < 0 ? "▼" : "•"} {fmt(a.sentiment_score, 2)}
                  </span>
                  <span className="news-region">{a.region}</span>
                  <span className="muted news-time">{a.published_at ? new Date(a.published_at).toLocaleString() : ""}</span>
                </div>
                <div className="news-headline">{a.headline}</div>
                <div className="muted news-source">{a.source} · {(a.symbols || []).join(", ")}</div>
              </a>
            ))}
          </div>
        )}
        <div className="muted" style={{ fontSize: "0.76rem", marginTop: "0.6rem" }}>
          Region tags are inferred from keywords in the headline/summary (news_feed.py) — not real
          geographic metadata. Neither free news source provides authoritative geo-tagging.
        </div>
      </div>
    </div>
  );
}
