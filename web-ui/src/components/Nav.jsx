import { PRODUCT } from "../brand.js";
const TABS = [
  { id: "topology", label: "Topology" },
  { id: "tickhouses", label: "TickHouses" },
  { id: "metrics", label: "Metrics" },
  { id: "query", label: "Query" },
  { id: "trading", label: "Trading" },
  { id: "connectors", label: "Connectors" },
  { id: "subscribers", label: "Subscribers" },
  { id: "fleet", label: "Fleet" },
  { id: "export", label: "Data export" },
  { id: "audit", label: "Audit log" },
];

export default function Nav({ active, onChange, onLogout }) {
  return (
    <nav className="nav">
      <div className="nav-brand"><span className="nav-brand-mark">{PRODUCT.slice(0, 2)}</span> {PRODUCT}</div>
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
