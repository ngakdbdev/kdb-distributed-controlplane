import { useState } from "react";
import Nav from "./components/Nav.jsx";
import AuditLog from "./pages/AuditLog.jsx";
import Connectors from "./pages/Connectors.jsx";
import Login from "./pages/Login.jsx";
import Metrics from "./pages/Metrics.jsx";
import Subscribers from "./pages/Subscribers.jsx";
import Topology from "./pages/Topology.jsx";

const PAGES = {
  topology: Topology,
  metrics: Metrics,
  connectors: Connectors,
  subscribers: Subscribers,
  audit: AuditLog,
};

export default function App() {
  const [loggedIn, setLoggedIn] = useState(!!localStorage.getItem("kcp_token"));
  const [active, setActive] = useState("topology");

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
        <Page />
      </main>
    </div>
  );
}
