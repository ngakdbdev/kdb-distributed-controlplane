import { useEffect, useMemo, useState } from "react";
import Nav from "./components/Nav.jsx";
import { COPYRIGHT } from "./brand.js";
import { roleFromToken } from "./jwt.js";
import AuditLog from "./pages/AuditLog.jsx";
import Alerts from "./pages/Alerts.jsx";
import Overview from "./pages/Overview.jsx";
import Tickerplants from "./pages/Tickerplants.jsx";
import Connectors from "./pages/Connectors.jsx";
import Execution from "./pages/Execution.jsx";
import Export from "./pages/Export.jsx";
import Fleet from "./pages/Fleet.jsx";
import TickHouses from "./pages/TickHouses.jsx";
import Login from "./pages/Login.jsx";
import Metrics from "./pages/Metrics.jsx";
import ModelSettings from "./pages/ModelSettings.jsx";
import Query from "./pages/Query.jsx";
import QueryAnalysis from "./pages/QueryAnalysis.jsx";
import Trading from "./pages/Trading.jsx";
import Subscribers from "./pages/Subscribers.jsx";
import Topology from "./pages/Topology.jsx";

const PAGES = {
  overview: Overview,
  topology: Topology,
  tickerplants: Tickerplants,
  metrics: Metrics,
  alerts: Alerts,
  query: Query,
  "query-analysis": QueryAnalysis,
  "model-settings": ModelSettings,
  trading: Trading,
  execution: Execution,
  connectors: Connectors,
  subscribers: Subscribers,
  tickhouses: TickHouses,
  fleet: Fleet,
  export: Export,
  audit: AuditLog,
};

// The SSO callback redirects to <ui>/#access_token=...&token_type=bearer.
// Capture it once on load, persist it, and scrub it from the URL.
function captureSsoToken() {
  if (!window.location.hash) return false;
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = params.get("access_token");
  if (!token) return false;
  localStorage.setItem("kcp_token", token);
  history.replaceState(null, "", window.location.pathname + window.location.search);
  return true;
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(() => captureSsoToken() || !!localStorage.getItem("kcp_token"));
  const [active, setActive] = useState("overview");
  // Decoded straight from the JWT (see jwt.js) - works uniformly for
  // password, LDAP, and SSO login, all of which mint the same token shape.
  // UI-only: hides a tab a non-admin can't use anyway. The real boundary is
  // require_platform_admin on the backend, checked on every request
  // regardless of what this says.
  const role = useMemo(() => (loggedIn ? roleFromToken(localStorage.getItem("kcp_token")) : null), [loggedIn]);

  useEffect(() => {
    if (!loggedIn && captureSsoToken()) setLoggedIn(true);
  }, [loggedIn]);

  function logout() {
    localStorage.removeItem("kcp_token");
    setLoggedIn(false);
  }

  if (!loggedIn) {
    return <Login onLoggedIn={() => setLoggedIn(true)} />;
  }

  const isPlatformAdmin = role === "platform_admin";
  const Page = (active === "model-settings" && !isPlatformAdmin) ? Overview : (PAGES[active] || Overview);

  return (
    <div className="app-shell">
      <Nav active={active} onChange={setActive} onLogout={logout} isPlatformAdmin={isPlatformAdmin} />
      <div className="app-content-col">
        <main className="app-main">
          <Page onNavigate={setActive} />
        </main>
        <footer className="app-footer">
          <span>{COPYRIGHT}</span>
        </footer>
      </div>
    </div>
  );
}
