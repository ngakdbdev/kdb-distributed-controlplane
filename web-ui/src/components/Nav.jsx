import { PRODUCT } from "../brand.js";
import ThemeToggle from "./ThemeToggle.jsx";

// Institutional-platform taxonomy, ordered Operations / Data / Analytics /
// Markets / Trading / Risk / Sales (top to bottom - group ORDER is the
// only thing that changed here; every group's own contents are exactly
// as before). Every existing page id below is UNCHANGED (App.jsx's PAGES
// map and each page's own logic need no changes), this only changes which
// group a page is filed under, its label, and now the group order.
// Admin-only pages (previously a separate "Admin" bucket bolted onto the
// end) live inside the group they actually belong to - TickHouses/Feed
// handlers under Data, Fleet/Autoscaling/Infra settings/Users/Audit under
// Operations - each tagged `roles` and filtered per-item at render time,
// so a group simply shows fewer items for a non-admin instead of
// disappearing as a whole separate section.
//
// Two pages don't have a real home in the taxonomy yet, on purpose:
// - `overview` stays under Operations (not Markets) because its content
//   today is still the platform-health/TickHouse rollup, not a market
//   overview - it moves once that page's content actually changes.
// - There's no dedicated Risk page yet (no VaR/exposure-limits backend -
//   see routers/trading.py's risk_check integration, which is a pretrade
//   gate, not a queryable risk surface) - Risk holds Alerts for now,
//   which already correlates real ops/market/execution signals.
const GROUPS = [
  {
    label: "Operations",
    items: [
      { id: "overview", label: "Overview", mark: "OV" },
      { id: "fleet", label: "Fleet", mark: "FL", roles: ["tenant_admin"] },
      { id: "autoscale", label: "Autoscaling", mark: "AS", roles: ["tenant_admin"] },
      { id: "infra-settings", label: "Infrastructure settings", mark: "IS", roles: ["tenant_admin"] },
      { id: "users", label: "Users", mark: "US", roles: ["tenant_admin"] },
      { id: "audit", label: "Audit log", mark: "AU", roles: ["tenant_admin"] },
    ],
  },
  {
    label: "Data",
    items: [
      { id: "tickerplants", label: "Tickerplants", mark: "TP" },
      { id: "metrics", label: "Metrics", mark: "MX" },
      { id: "topology", label: "Topology", mark: "TO" },
      { id: "connectors", label: "Connectors", mark: "CN" },
      { id: "subscribers", label: "Subscribers", mark: "SB" },
      { id: "export", label: "Data export", mark: "DX" },
      { id: "tickhouses", label: "TickHouses", mark: "TH", roles: ["tenant_admin"] },
      { id: "feed-handlers", label: "Feed handlers", mark: "FH", roles: ["tenant_admin"] },
    ],
  },
  {
    label: "Analytics",
    items: [
      { id: "signals", label: "Predictive Signals", mark: "PS" },
      { id: "query", label: "Query", mark: "QY" },
      { id: "query-analysis", label: "Query analysis", mark: "QA" },
    ],
  },
  {
    label: "Markets",
    items: [
      { id: "markets", label: "Markets", mark: "MK" },
    ],
  },
  {
    label: "Trading",
    items: [
      { id: "orders", label: "Orders", mark: "OR" },
      { id: "portfolio", label: "Portfolio", mark: "PF" },
      { id: "execution", label: "Execution", mark: "EX" },
      { id: "bot", label: "Bot", mark: "BT" },
    ],
  },
  {
    label: "Risk",
    items: [
      { id: "alerts", label: "Alerts", mark: "AL" },
    ],
  },
  {
    label: "Sales",
    items: [
      { id: "migration", label: "Migration assessment", mark: "MG" },
    ],
  },
];
// Platform-wide settings (LLMConfig is a single global row, not per-tenant)
// - the SaaS operator level, unrelated to any one tenant's own Admin, so
// this stays a fully separate group rather than folded into one above.
const PLATFORM_ADMIN_GROUP = {
  label: "Platform Admin",
  items: [{ id: "model-settings", label: "Model settings", mark: "MS" }],
};

function visibleItems(items, role) {
  return items.filter((item) => !item.roles || item.roles.includes(role));
}

// Flat, role-filtered page index for CommandBar.jsx's "jump to a page"
// results - derived from the same GROUPS/PLATFORM_ADMIN_GROUP data the
// sidebar itself renders from, so the two can't drift into listing
// different pages.
export function allPagesForRole(role) {
  const groups = role === "platform_admin" ? [...GROUPS, PLATFORM_ADMIN_GROUP] : GROUPS;
  return groups.flatMap((group) => visibleItems(group.items, role).map((item) => ({ ...item, group: group.label })));
}

export default function Nav({ active, onChange, onLogout, role }) {
  const groups = GROUPS
    .map((group) => ({ ...group, items: visibleItems(group.items, role) }))
    .filter((group) => group.items.length > 0);
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
      <ThemeToggle />
      <button className="sidenav-logout" onClick={onLogout}>
        <span className="sidenav-item-mark">↩</span>
        <span className="sidenav-item-label">Log out</span>
      </button>
    </nav>
  );
}
