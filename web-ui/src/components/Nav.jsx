const TABS = [
  { id: "topology", label: "Topology" },
  { id: "metrics", label: "Metrics" },
  { id: "connectors", label: "Connectors" },
  { id: "subscribers", label: "Subscribers" },
  { id: "audit", label: "Audit log" },
];

export default function Nav({ active, onChange, onLogout }) {
  return (
    <nav className="nav">
      <div className="nav-brand">kdb+ tick control plane</div>
      <div className="nav-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`nav-tab ${active === t.id ? "active" : ""}`}
            onClick={() => onChange(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <button className="nav-logout" onClick={onLogout}>Log out</button>
    </nav>
  );
}
