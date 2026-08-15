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
      { id: "markets", label: "Markets", mark: "MK" },
      { id: "orders", label: "Orders", mark: "OR" },
      { id: "portfolio", label: "Portfolio", mark: "PF" },
      { id: "signals", label: "Predictive Signals", mark: "PS" },
      { id: "bot", label: "Bot", mark: "BT" },
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
    label: "Sales",
    items: [
      { id: "migration", label: "Migration assessment", mark: "MG" },
    ],
  },
];
// Tenant-scoped infra/admin pages (TickHouses/Fleet/Infra settings/Users all
// require_admin server-side - see control-api/app/routers/auth.py) - shown
// only to that tenant's own Admin (tenant_admin), not functional_user/
// quant_analyst (see models.UserRole) and not platform_admin, who has no
// tenant_id and so can't use any of these anyway (require_admin composes on
// require_tenant_scope, which platform_admin never satisfies).
const TENANT_ADMIN_GROUP = {
  label: "Admin",
  items: [
    { id: "tickhouses", label: "TickHouses", mark: "TH" },
    { id: "autoscale", label: "Autoscaling", mark: "AS" },
    { id: "fleet", label: "Fleet", mark: "FL" },
    { id: "infra-settings", label: "Infrastructure settings", mark: "IS" },
    { id: "users", label: "Users", mark: "US" },
    { id: "audit", label: "Audit log", mark: "AU" },
  ],
};
// Platform-wide settings (LLMConfig is a single global row, not per-tenant)
// - the SaaS operator level, unrelated to any one tenant's own Admin.
const PLATFORM_ADMIN_GROUP = {
  label: "Platform Admin",
  items: [{ id: "model-settings", label: "Model settings", mark: "MS" }],
};

export default function Nav({ active, onChange, onLogout, role }) {
  const groups = [...GROUPS];
  if (role === "tenant_admin") groups.push(TENANT_ADMIN_GROUP);
  if (role === "platform_admin") groups.push(PLATFORM_ADMIN_GROUP);
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
