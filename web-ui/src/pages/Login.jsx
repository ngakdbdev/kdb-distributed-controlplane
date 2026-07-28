import { useState } from "react";
import { api } from "../api.js";

export default function Login({ onLoggedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // With an Organisation ID present, the primary sign-in binds against that
  // tenant's LDAP/Active Directory; without one, it's a local password login.
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
      setError(org
        ? "LDAP sign-in failed - check organisation ID, username and password."
        : "Login failed - check email/password.");
    } finally {
      setBusy(false);
    }
  }

  function ssoSignIn() {
    if (!slug.trim()) {
      setError("Enter your organisation ID to sign in with Microsoft.");
      return;
    }
    // full-page redirect into the Entra flow; the callback returns us here
    // with the session token in the URL fragment (handled in App.jsx)
    window.location.href = api.ssoLoginUrl(slug.trim());
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <h1>kdb+ tick control plane</h1>
        <p className="muted">Sign in to manage your deployment.</p>
        <label>
          Email or username
          <input value={email} onChange={(e) => setEmail(e.target.value)}
                 placeholder="you@yourbank.com" />
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
        <button type="submit" disabled={busy}>
          {busy ? "Signing in..." : (slug.trim() ? "Sign in with LDAP / AD" : "Sign in")}
        </button>

        <div className="sso-divider"><span>or</span></div>

        <button type="button" className="sso-button" onClick={ssoSignIn}>
          Sign in with Microsoft Entra
        </button>
      </form>
    </div>
  );
}
