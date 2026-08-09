import { useEffect, useMemo, useState } from "react";
import Nav from "./components/Nav.jsx";
import { COPYRIGHT } from "./brand.js";
import { roleFromToken } from "./jwt.js";
import AuditLog from "./pages/AuditLog.jsx";
import Alerts from "./pages/Alerts.jsx";
import Autoscale from "./pages/Autoscale.jsx";
import Bot from "./pages/Bot.jsx";
import Overview from "./pages/Overview.jsx";
import Tickerplants from "./pages/Tickerplants.jsx";
import Connectors from "./pages/Connectors.jsx";
import Execution from "./pages/Execution.jsx";
import Export from "./pages/Export.jsx";
import Fleet from "./pages/Fleet.jsx";
import TickHouses from "./pages/TickHouses.jsx";
import Login from "./pages/Login.jsx";
import Markets from "./pages/Markets.jsx";
import Metrics from "./pages/Metrics.jsx";
import Migration from "./pages/Migration.jsx";
import ModelSettings from "./pages/ModelSettings.jsx";
import Orders from "./pages/Orders.jsx";
import Portfolio from "./pages/Portfolio.jsx";
import Query from "./pages/Query.jsx";
import QueryAnalysis from "./pages/QueryAnalysis.jsx";
import RecoveryWatch from "./components/RecoveryWatch.jsx";
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
  markets: Markets,
  orders: Orders,
  portfolio: Portfolio,
  bot: Bot,
  execution: Execution,
  connectors: Connectors,
  subscribers: Subscribers,
  tickhouses: TickHouses,
  autoscale: Autoscale,
  fleet: Fleet,
  export: Export,
  audit: AuditLog,
  migration: Migration,
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
  // Params carried across a deep-link nav call (e.g. Markets' "Buy AAPL"
  // button jumping to Orders with {symbol: "AAPL", side: "buy"} prefilled).
  // Cleared right after the target page mounts so a later in-page symbol
  // change on Orders doesn't get stomped by a stale deep-link on remount.
  const [navParams, setNavParams] = useState(null);
  // Decoded straight from the JWT (see jwt.js) - works uniformly for
  // password, LDAP, and SSO login, all of which mint the same token shape.
  // UI-only: hides a tab a non-admin can't use anyway. The real boundary is
  // require_platform_admin on the backend, checked on every request
  // regardless of what this says.
  const role = useMemo(() => (loggedIn ? roleFromToken(localStorage.getItem("kcp_token")) : null), [loggedIn]);

  useEffect(() => {
    if (!loggedIn && captureSsoToken()) setLoggedIn(true);
  }, [loggedIn]);

  function navigate(id, params) {
    setActive(id);
    setNavParams(params || null);
  }

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
      <Nav active={active} onChange={navigate} onLogout={logout} isPlatformAdmin={isPlatformAdmin} />
      <div className="app-content-col">
        <main className="app-main">
          <Page onNavigate={navigate} initial={navParams} />
        </main>
        <footer className="app-footer">
          <span>{COPYRIGHT}</span>
        </footer>
      </div>
      <RecoveryWatch />
    </div>
  );
}
