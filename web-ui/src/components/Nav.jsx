import { PRODUCT } from "../brand.js";

// Grouped, left-sidebar nav (Databricks/Snowflake-style) instead of a
// single row of 17 top tabs. Groups are purely a presentation grouping -
// every existing tab id below is unchanged, so App.jsx's PAGES map and
// each page's own logic need no changes at all.
const GROUPS = [
  {
    label: "Overview",
    items: [
      { id: "overview", label: "Overview", mark: "OV" },
      { id: "topology", label: "Topology", mark: "TO" },
    ],
  },
  {
    label: "Live monitoring",
    items: [
      { id: "tickerplants", label: "Tickerplants", mark: "TP" },
      { id: "metrics", label: "Metrics", mark: "MX" },
      { id: "alerts", label: "Alerts", mark: "AL" },
    ],
  },
  {
    label: "Query",
    items: [
      { id: "query", label: "Query", mark: "QY" },
      { id: "query-analysis", label: "Query analysis", mark: "QA" },
    ],
  },
  {
    label: "Trading",
    items: [
      { id: "trading", label: "Trading", mark: "TR" },
      { id: "execution", label: "Execution", mark: "EX" },
    ],
  },
  {
    label: "Data",
    items: [
      { id: "connectors", label: "Connectors", mark: "CN" },
      { id: "subscribers", label: "Subscribers", mark: "SB" },
      { id: "export", label: "Data export", mark: "DX" },
    ],
  },
  {
    label: "Manage",
    items: [
      { id: "tickhouses", label: "TickHouses", mark: "TH" },
      { id: "fleet", label: "Fleet", mark: "FL" },
      { id: "audit", label: "Audit log", mark: "AU" },
    ],
  },
  {
    label: "Sales",
    items: [
      { id: "migration", label: "Migration assessment", mark: "MG" },
    ],
  },
];
const ADMIN_GROUP = {
  label: "Admin",
  items: [{ id: "model-settings", label: "Model settings", mark: "MS" }],
};

export default function Nav({ active, onChange, onLogout, isPlatformAdmin }) {
  const groups = isPlatformAdmin ? [...GROUPS, ADMIN_GROUP] : GROUPS;
  return (
    <nav className="sidenav">
      <div className="sidenav-brand">
        <span className="nav-brand-mark">{PRODUCT.slice(0, 2)}</span>
        <span className="sidenav-brand-name">{PRODUCT}</span>
      </div>
      <div className="sidenav-scroll">
        {groups.map((group) => (
          <div className="sidenav-group" key={group.label}>
            <div className="sidenav-group-label">{group.label}</div>
            {group.items.map((t) => (
              <button
                key={t.id}
                className={`sidenav-item ${active === t.id ? "active" : ""}`}
                onClick={() => onChange(t.id)}
              >
                <span className="sidenav-item-mark">{t.mark}</span>
                <span className="sidenav-item-label">{t.label}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      <button className="sidenav-logout" onClick={onLogout}>
        <span className="sidenav-item-mark">↩</span>
        <span className="sidenav-item-label">Log out</span>
      </button>
    </nav>
  );
}
