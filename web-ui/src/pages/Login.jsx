import { useState } from "react";
import { api } from "../api.js";
import { PRODUCT, COMPANY, TAGLINE, COPYRIGHT } from "../brand.js";

export default function Login({ onLoggedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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

  return (
    <div className="login-fullscreen">
      <section className="login-hero">
        <div className="login-hero-inner">
          <div className="login-logo">
            <span className="login-logo-mark">{PRODUCT.slice(0, 2)}</span>
            <span className="login-logo-name">{PRODUCT}</span>
          </div>
          <h1 className="login-hero-title">{TAGLINE}</h1>
          <ul className="login-hero-points">
            <li>Define &amp; provision tick clusters across AWS, Azure, GCP and on-prem</li>
            <li>Live query workspace with federated scatter-gather across gateways</li>
            <li>Stream real market data in, export history to your lakehouse</li>
          </ul>
          <div className="login-hero-foot">{COMPANY}</div>
        </div>
      </section>

      <section className="login-panel">
        <form className="login-form" onSubmit={submit}>
          <h2>Sign in</h2>
          <p className="muted">Welcome back. Sign in to manage your deployment.</p>

          <label>
            Email or username
            <input value={email} onChange={(e) => setEmail(e.target.value)}
                   placeholder="you@yourbank.com" autoFocus />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label>
            Organisation ID <span className="muted">(for SSO / LDAP)</span>
            <input value={slug} onChange={(e) => setSlug(e.target.value)}
                   placeholder="your-bank" />
          </label>

          {error && <div className="error">{error}</div>}

          <button type="submit" className="login-submit" disabled={busy}>
            {busy ? "Signing in…" : (slug.trim() ? "Sign in with LDAP / AD" : "Sign in")}
          </button>

          <div className="sso-divider"><span>or</span></div>

          <button type="button" className="sso-button" onClick={ssoSignIn}>
            Sign in with Microsoft Entra
          </button>

          <div className="login-copyright">{COPYRIGHT}</div>
        </form>
      </section>
    </div>
  );
}
