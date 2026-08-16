import { useEffect, useState } from "react";
import { api } from "../api.js";
import { PRODUCT, COMPANY, TAGLINE, COPYRIGHT } from "../brand.js";
import { currentMode, setMode, watchSystemTheme } from "../lib/theme.js";

const MINI_THEME_MODES = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

export default function Login({ onLoggedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [themeMode, setThemeMode] = useState(() => {
    const mode = currentMode();
    return mode === "light" || mode === "dark" ? mode : "dark";
  });

  useEffect(() => watchSystemTheme(), []);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const org = slug.trim();
    try {
      const { access_token } = org
        ? await api.ldapLogin(org, email, password)
        : await api.login(email, password);
      localStorage.setItem("kcp_token", access_token);
      onLoggedIn();
    } catch (err) {
      const message = String(err?.message || "");
      const lower = message.toLowerCase();
      const statusMatch = message.match(/^(\d{3})\s/);
      const status = statusMatch ? Number(statusMatch[1]) : null;
      if (lower.includes("timed out")) {
        setError("Sign-in timed out. The control API is slow or restarting; please retry in a few seconds.");
      } else if (status === 401) {
        setError(org
          ? "LDAP sign-in failed - check organisation ID, username and password."
          : "Login failed - check email/password.");
      } else if (status && status >= 500) {
        setError(`Sign-in unavailable right now (server error ${status}). This is not a credentials problem - the backend or proxy is likely mid-restart; please retry in a few seconds.`);
      } else if (status) {
        setError(`Sign-in failed (${status}). This is not necessarily a credentials problem - check the control API is reachable.`);
      } else {
        setError(`Sign-in failed: ${message || "network error reaching the control API."}`);
      }
    } finally {
      setBusy(false);
    }
  }

  function ssoSignIn() {
    if (!slug.trim()) {
      setError("Enter your organisation ID to sign in with Microsoft.");
      return;
    }
    window.location.href = api.ssoLoginUrl(slug.trim());
  }

  function chooseTheme(next) {
    setMode(next);
    setThemeMode(next);
  }

  const marketNodes = [
    { label: "Ingestion", sub: "Multi-provider feed connectors" },
    { label: "Signal Core", sub: "Real-time analytics + risk gates" },
    { label: "Execution", sub: "Broker-aware smart routing" },
    { label: "Portfolio", sub: "P/L, margin, and post-trade state" },
  ];

  const productCards = [
    {
      title: "Vantik Pulse",
      body: "A latency-first ingestion mesh that normalizes institutional and crypto venues into one canonical event stream.",
      tag: "Products",
    },
    {
      title: "Vantik Forge",
      body: "A control plane for topology orchestration, autoscaling, and resilient shard-aware market data routing.",
      tag: "Technology",
    },
    {
      title: "Vantik Orbit",
      body: "Execution intelligence with risk controls, signal composition, and portfolio-aware trade lifecycle automation.",
      tag: "Markets",
    },
  ];

  return (
    <div className="vantik-shell vantik-auth-layout">
      <div className="vantik-grid-bg" aria-hidden="true" />
      <div className="vantik-radial-a" aria-hidden="true" />
      <div className="vantik-radial-b" aria-hidden="true" />

      <aside className="vantik-brand-panel">
        <header className="vantik-topbar vantik-topbar-inline">
          <a className="vantik-wordmark" href="#hero" aria-label="Vantik home">
            <span className="vantik-wordmark-mark">{PRODUCT.slice(0, 2)}</span>
            <span className="vantik-wordmark-name">{PRODUCT}</span>
          </a>
          <nav className="vantik-nav" aria-label="Primary">
            <a href="#products">Products</a>
            <a href="#technology">Technology</a>
            <a href="#markets">Markets</a>
            <a href="#about">About</a>
            <a href="#contact">Contact</a>
          </nav>
          <div className="vantik-mini-theme" role="radiogroup" aria-label="Theme">
            {MINI_THEME_MODES.map((mode) => (
              <button
                key={mode.id}
                type="button"
                role="radio"
                aria-checked={themeMode === mode.id}
                className={`vantik-mini-theme-btn ${themeMode === mode.id ? "active" : ""}`}
                onClick={() => chooseTheme(mode.id)}
                title={`${mode.label} mode`}
              >
                {mode.label}
              </button>
            ))}
          </div>
        </header>

        <div className="vantik-brand-copy" id="hero">
          <p className="vantik-eyebrow">Contact</p>
          <h1 className="vantik-hero-title">Secure access to your Vantik control plane.</h1>
          <p className="vantik-hero-body">
            {TAGLINE}. Vantik unifies ingestion, analytics, execution, and portfolio intelligence in one deeply modern trading technology fabric.
          </p>

          <div className="vantik-hero-actions">
            <a className="vantik-cta" href="#contact">Contact</a>
            <a className="vantik-link" href="#products">Explore the stack</a>
          </div>
        </div>

        <div
          className="vantik-brand-visual"
          role="img"
          aria-label="Animated diagram showing live tick data flowing through Vantik's ingestion, signal, execution, and portfolio pipeline, with automatic recovery"
        >
          <div className="vantik-reel-head">
            <span className="vantik-video-dot" />
            <strong>Architecture Reel</strong>
            <span>live simulation</span>
          </div>

          <div className="vantik-flow-track" aria-hidden="true">
            <span className="vantik-flow-line" />
            <span className="vantik-flow-dot" style={{ "--dot-delay": "0s" }} />
            <span className="vantik-flow-dot" style={{ "--dot-delay": "0.9s" }} />
            <span className="vantik-flow-dot" style={{ "--dot-delay": "1.8s" }} />
          </div>

          <div className="vantik-node-row">
            {marketNodes.map((node, i) => (
              <article key={node.label} className="vantik-node" style={{ "--node-index": i }}>
                <div className="vantik-node-top">
                  <span className="vantik-node-dot" />
                  <strong>{node.label}</strong>
                </div>
                <span>{node.sub}</span>
              </article>
            ))}
          </div>

          <div className="vantik-video-captions" aria-hidden="true">
            <p className="active">A data node failed and recovered itself in 1.2 seconds — no one paged</p>
            <p>Scaled to 18 synchronized nodes with zero downtime</p>
            <p>Every tick reaches trading and portfolio teams the instant it lands</p>
          </div>

          <div className="vantik-video-footer" aria-hidden="true">
            <span className="tag">Always on</span>
            <span className="tag">Self-healing</span>
            <span className="tag">Real-time delivery</span>
          </div>
        </div>

        <div className="vantik-card-row vantik-brand-cards" id="products">
          {productCards.map((item, i) => (
            <article key={item.title} className="vantik-card" style={{ "--delay": `${i * 100}ms` }}>
              <span>{item.tag}</span>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </div>
      </aside>

      <main className="vantik-auth-panel" id="contact">
        <section className="vantik-login-card">
          <div className="vantik-login-head">
            <p className="vantik-eyebrow">Contact</p>
            <h3>Sign in</h3>
            <p className="muted">Use local credentials, LDAP, or Microsoft Entra SSO.</p>
          </div>

          <form className="vantik-login" onSubmit={submit}>
            <label>
              Email or username
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@yourfirm.com"
                autoFocus
                autoComplete="username"
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <label>
              Organisation ID <span className="muted">(for SSO / LDAP)</span>
              <input
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="your-org"
              />
            </label>

            {error && <div className="error" role="alert">{error}</div>}

            <button type="submit" className="vantik-login-submit" disabled={busy}>
              {busy ? "Signing in…" : (slug.trim() ? "Sign in with LDAP / AD" : "Sign in")}
            </button>

            <div className="sso-divider"><span>or</span></div>

            <button type="button" className="sso-button" onClick={ssoSignIn}>
              Sign in with Microsoft Entra
            </button>

            <p className="vantik-copyright">{COPYRIGHT}</p>
          </form>
        </section>
      </main>
    </div>
  );
}
