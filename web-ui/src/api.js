const BASE = "/api";
const REQUEST_TIMEOUT_MS = 12000;
// LLM-backed calls (control-api/app/nl2q.py, q_codegen.py, query_advisor.py)
// route through a local model on CPU by default - single calls have been
// observed taking 4-20s, and NL2Q_LLM_TIMEOUT_SEC on the backend is
// configured up to 60s to give that room. The blanket 12s client timeout
// above was aborting those requests client-side well before the backend
// itself gave up, surfacing as a spurious "Request timed out" - not a real
// backend problem. codegen additionally retries once on a visibly
// incomplete response (see q_codegen.py), so its worst case is roughly
// double a single call's.
const LLM_TIMEOUT_MS = 90000;
const CODEGEN_TIMEOUT_MS = 130000;

function authHeaders() {
  const token = localStorage.getItem("kcp_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...(options.headers || {}),
      },
    });
  } catch (err) {
    if (err?.name === "AbortError") {
      throw new Error(`Request timed out after ${timeoutMs / 1000}s`);
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

export const api = {
  login: (email, password) =>
    request("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),

  // Direct LDAP / Active Directory bind against a tenant's directory.
  ldapLogin: (slug, username, password) =>
    request(`/auth/ldap/${encodeURIComponent(slug)}/login`,
      { method: "POST", body: JSON.stringify({ username, password }) }),

  // Full-page navigation target that kicks off the Entra redirect flow for a
  // tenant. The API 302s to Entra, then Entra 302s back to the callback, which
  // finally redirects here with the session token in the URL fragment.
  ssoLoginUrl: (slug) => `${BASE}/auth/sso/${encodeURIComponent(slug)}/login`,

  topologyStatus: () => request("/topology/status"),
  tickerplants: () => request("/tickerplants"),
  startService: (service) => request(`/topology/service/${service}/start`, { method: "POST" }),
  stopService: (service) => request(`/topology/service/${service}/stop`, { method: "POST" }),
  restartService: (service) => request(`/topology/service/${service}/restart`, { method: "POST" }),
  serviceLogs: (service, tail = 200) => request(`/topology/service/${service}/logs?tail=${tail}`),

  metricsSnapshot: () => request("/metrics/snapshot"),

  listConnectors: () => request("/connectors"),
  listProviders: () => request("/connectors/providers"),
  listExportSinks: () => request("/export/sinks"),

  // TickHouse (declarative tick clusters)
  analyzeMigration: (files) => request("/migration/analyze", { method: "POST", body: JSON.stringify({ files }) }),
  migrationTcoRates: () => request("/migration/tco/rates"),
  migrationTco: (body) => request("/migration/tco", { method: "POST", body: JSON.stringify(body) }),

  tickhouseMeta: () => request("/tickhouses/meta"),
  previewTickhouse: (body) => request("/tickhouses/preview", { method: "POST", body: JSON.stringify(body) }),
  createTickhouse: (body) => request("/tickhouses", { method: "POST", body: JSON.stringify(body) }),
  listTickhouses: () => request("/tickhouses"),
  getTickhouse: (id) => request(`/tickhouses/${id}`),
  deleteTickhouse: (id) => request(`/tickhouses/${id}`, { method: "DELETE" }),
  provisionTickhouse: (id, agentId) =>
    request(`/tickhouses/${id}/provision`, { method: "POST", body: JSON.stringify({ agent_id: agentId }) }),
  tickhouseStatus: (id) => request(`/tickhouses/${id}/status`),

  // Live query workspace
  queryTargets: () => request("/query/targets"),
  queryTables: (target) => request(`/query/tables?target=${encodeURIComponent(target)}`),
  runQuery: (body) => request("/query/run", { method: "POST", body: JSON.stringify(body) }),
  nl2q: (text, target) =>
    request("/query/nl2q", { method: "POST", body: JSON.stringify({ text, target }) }, LLM_TIMEOUT_MS),
  codegen: (text, target) =>
    request("/query/codegen", { method: "POST", body: JSON.stringify({ text, target }) }, CODEGEN_TIMEOUT_MS),
  analyzeQuery: (q, target) =>
    request("/query/analyze", { method: "POST", body: JSON.stringify({ q, target }) }, LLM_TIMEOUT_MS),
  queryHistory: (limit = 50) => request(`/query/history?limit=${limit}`),

  // Model settings (platform admin only - backend enforces via require_platform_admin)
  getLLMConfig: () => request("/admin/llm-config"),
  updateLLMConfig: (body) => request("/admin/llm-config", { method: "PUT", body: JSON.stringify(body) }),

  // Symbol reference
  searchSymbols: (q, market) =>
    request(`/symbols/search?q=${encodeURIComponent(q || "")}${market ? `&market=${encodeURIComponent(market)}` : ""}`),
  symbolMarkets: () => request("/symbols/markets"),

  // Trading terminal
  tradingPermission: () => request("/trading/permission"),
  placeOrder: (body) => request("/trading/orders", { method: "POST", body: JSON.stringify(body) }),
  listOrders: () => request("/trading/orders"),
  cancelOrder: (id) => request(`/trading/orders/${id}/cancel`, { method: "POST" }),
  matchOrders: (symbol, price) =>
    request("/trading/orders/match", { method: "POST", body: JSON.stringify({ symbol, price }) }),
  getPositions: (marks) => request(`/trading/positions${marks ? `?marks=${encodeURIComponent(marks)}` : ""}`),
  computeGreeks: (body) => request("/trading/greeks", { method: "POST", body: JSON.stringify(body) }),
  marketSummary: (body) => request("/trading/market", { method: "POST", body: JSON.stringify(body) }),
  forecast: (body) => request("/trading/forecast", { method: "POST", body: JSON.stringify(body) }),
  grantTrading: (email, can) => request("/trading/grant", { method: "POST", body: JSON.stringify({ email, can_trade: can }) }),
  toggleConnector: (id) => request(`/connectors/${id}/toggle`, { method: "POST" }),
  setConnectorSymbols: (id, symbols) =>
    request(`/connectors/${id}/symbols`, { method: "PUT", body: JSON.stringify({ symbols }) }),

  listSubscribers: () => request("/subscribers"),
  addSubscriber: (sub) => request("/subscribers", { method: "POST", body: JSON.stringify(sub) }),
  removeSubscriber: (id) => request(`/subscribers/${id}`, { method: "DELETE" }),

  listAudit: (limit = 100) => request(`/audit?limit=${limit}`),

  // Fleet / provisioning: each agent is a tenant-controlled environment
  // (AWS/Azure/GCP/on-prem). Registering one returns a one-time enrollment
  // token; provisioning queues a command the agent reconciles in its cluster.
  listAgents: () => request("/fleet/agents"),
  createAgent: (name, environment) =>
    request("/fleet/agents", { method: "POST", body: JSON.stringify({ name, environment }) }),
  provision: (agentId, shardCount, note = "") =>
    request(`/fleet/agents/${agentId}/provision`,
      { method: "POST", body: JSON.stringify({ shard_count: shardCount, note }) }),
  deprovision: (agentId) =>
    request(`/fleet/agents/${agentId}/deprovision`, { method: "POST" }),
  listAgentCommands: (agentId, limit = 50) =>
    request(`/fleet/agents/${agentId}/commands?limit=${limit}`),
};

export function metricsSocket(onMessage) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${window.location.host}/api/metrics/stream`);
  ws.onmessage = (evt) => onMessage(JSON.parse(evt.data));
  return ws;
}
