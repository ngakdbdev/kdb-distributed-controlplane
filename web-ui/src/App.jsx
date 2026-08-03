import { useEffect, useState } from "react";
import Nav from "./components/Nav.jsx";
import { COPYRIGHT } from "./brand.js";
import AuditLog from "./pages/AuditLog.jsx";
import Overview from "./pages/Overview.jsx";
import Connectors from "./pages/Connectors.jsx";
import Export from "./pages/Export.jsx";
import Fleet from "./pages/Fleet.jsx";
import TickHouses from "./pages/TickHouses.jsx";
import Login from "./pages/Login.jsx";
import Metrics from "./pages/Metrics.jsx";
import Query from "./pages/Query.jsx";
import Trading from "./pages/Trading.jsx";
import Subscribers from "./pages/Subscribers.jsx";
import Topology from "./pages/Topology.jsx";

const PAGES = {
  overview: Overview,
  topology: Topology,
  metrics: Metrics,
  query: Query,
  trading: Trading,
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

  const Page = PAGES[active];

  return (
    <div className="app-shell">
      <Nav active={active} onChange={setActive} onLogout={logout} />
      <main className="app-main">
        <Page onNavigate={setActive} />
      </main>
      <footer className="app-footer">
        <span>{COPYRIGHT}</span>
      </footer>
    </div>
  );
}
