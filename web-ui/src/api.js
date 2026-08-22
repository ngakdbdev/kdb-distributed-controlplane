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
    // FastAPI's standard error shape is {"detail": "..."} - extract it so
    // callers (and their `.replace(/^Error:\s*/, "")` display logic) show
    // a clean sentence instead of the raw JSON body verbatim. Falls back
    // to the raw text for anything that isn't that shape (a proxy/gateway
    // error page, an empty body, etc).
    let message = body;
    try {
      const parsed = JSON.parse(body);
      if (typeof parsed?.detail === "string") message = parsed.detail;
      else if (parsed?.detail != null) message = JSON.stringify(parsed.detail);
    } catch { /* not JSON - use the raw body as-is */ }
    throw new Error(`${res.status}: ${message}`);
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
  platformHealth: () => request("/platform/health"),
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
  suggestTickhouse: (body) => request("/tickhouses/suggest", { method: "POST", body: JSON.stringify(body) }),
  previewTickhouse: (body) => request("/tickhouses/preview", { method: "POST", body: JSON.stringify(body) }),
  createTickhouse: (body) => request("/tickhouses", { method: "POST", body: JSON.stringify(body) }),
  listTickhouses: () => request("/tickhouses"),
  getTickhouse: (id) => request(`/tickhouses/${id}`),
  deleteTickhouse: (id) => request(`/tickhouses/${id}`, { method: "DELETE" }),
  provisionTickhouse: (id, agentId) =>
    request(`/tickhouses/${id}/provision`, { method: "POST", body: JSON.stringify({ agent_id: agentId }) }),
  tickhouseStatus: (id) => request(`/tickhouses/${id}/status`),

  cloudProvisionAws: (body) => request("/cloud-provision/aws", { method: "POST", body: JSON.stringify(body) }),
  cloudProvisionAzure: (body) => request("/cloud-provision/azure", { method: "POST", body: JSON.stringify(body) }),
  cloudProvisionGcp: (body) => request("/cloud-provision/gcp", { method: "POST", body: JSON.stringify(body) }),
  listCloudProvisionRuns: () => request("/cloud-provision"),
  getCloudProvisionRun: (id) => request(`/cloud-provision/${id}`),

  // Global infra profiles (control-api/app/routers/infra_profiles.py) -
  // reusable non-secret cloud/k8s coordinate bundles, platform-admin
  // managed, readable by any authenticated user (needed to auto-load one
  // while creating a TickHouse).
  infraProfilesMeta: () => request("/infra-profiles/meta"),
  listInfraProfiles: () => request("/infra-profiles"),
  createInfraProfile: (body) => request("/infra-profiles", { method: "POST", body: JSON.stringify(body) }),
  updateInfraProfile: (id, body) => request(`/infra-profiles/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteInfraProfile: (id) => request(`/infra-profiles/${id}`, { method: "DELETE" }),

  // Feed-handler admin portal (control-api/app/routers/feedhandlers.py) -
  // the C++ protocol/venue-adapter engine (data-plane/feedhandler-cpp/):
  // browse the provider catalog, activate a source with its config +
  // credentials (encrypted at rest), fetch the decrypted engine-config an
  // operator hands to a real deployment.
  feedhandlerCatalog: () => request("/feedhandlers/catalog"),
  listFeedHandlers: () => request("/feedhandlers"),
  createFeedHandler: (body) => request("/feedhandlers", { method: "POST", body: JSON.stringify(body) }),
  updateFeedHandler: (id, body) => request(`/feedhandlers/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteFeedHandler: (id) => request(`/feedhandlers/${id}`, { method: "DELETE" }),
  feedHandlerEngineConfig: (id) => request(`/feedhandlers/${id}/engine-config`),

  // Per-tenant user management (control-api/app/routers/users.py) - an
  // Admin (tenant_admin) creating/editing logins within their own tenant.
  listUsers: () => request("/users"),
  createUser: (body) => request("/users", { method: "POST", body: JSON.stringify(body) }),
  updateUser: (id, body) => request(`/users/${id}`, { method: "PUT", body: JSON.stringify(body) }),

  // Live query workspace
  queryTargets: () => request("/query/targets"),
  queryTables: (target) => request(`/query/tables?target=${encodeURIComponent(target)}`),
  runQuery: (body) => request("/query/run", { method: "POST", body: JSON.stringify(body) }),
  // Binary response (a real .parquet file), not JSON - can't go through the
  // shared request() helper above, which always parses the body as JSON.
  exportParquet: async (columns, rows, filename) => {
    const res = await fetch(`${BASE}/query/export/parquet`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ columns, rows, filename }),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`${res.status} ${res.statusText}: ${body}`);
    }
    return res.blob();
  },
  nl2q: (text, target) =>
    request("/query/nl2q", { method: "POST", body: JSON.stringify({ text, target }) }, LLM_TIMEOUT_MS),
  codegen: (text, target) =>
    request("/query/codegen", { method: "POST", body: JSON.stringify({ text, target }) }, CODEGEN_TIMEOUT_MS),
  analyzeQuery: (q, target) =>
    request("/query/analyze", { method: "POST", body: JSON.stringify({ q, target }) }, LLM_TIMEOUT_MS),
  queryHistory: (limit = 50) => request(`/query/history?limit=${limit}`),

  // Background export to S3/ADLS - for pulls too large for the local Parquet
  // download (capped at 10GB, see control-api/app/parquet_export.py).
  startBackgroundExport: (body) => request("/query/export/background", { method: "POST", body: JSON.stringify(body) }),
  getExportJob: (id) => request(`/query/export/jobs/${id}`),
  listExportJobs: (limit = 20) => request(`/query/export/jobs?limit=${limit}`),

  // Model settings (platform admin only - backend enforces via require_platform_admin)
  getLLMConfig: () => request("/admin/llm-config"),
  updateLLMConfig: (body) => request("/admin/llm-config", { method: "PUT", body: JSON.stringify(body) }),

  // Symbol reference
  searchSymbols: (q, market) =>
    request(`/symbols/search?q=${encodeURIComponent(q || "")}${market ? `&market=${encodeURIComponent(market)}` : ""}`),
  symbolMarkets: () => request("/symbols/markets"),

  // Trading terminal
  tradingPermission: () => request("/trading/permission"),
  marketClock: () => request("/trading/market-clock"),
  getTradingViewWebhookConfig: () => request("/tradingview/config"),
  putTradingViewWebhookConfig: (body) => request("/tradingview/config", { method: "PUT", body: JSON.stringify(body) }),
  rotateTradingViewWebhookToken: () => request("/tradingview/rotate", { method: "POST" }),
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

  // Predictive Signals page (control-api/app/routers/signals.py)
  predictiveSignals: (symbols) =>
    request(`/signals/predictive${symbols ? `?symbols=${encodeURIComponent(symbols)}` : ""}`),
  newsFeed: (symbols, limit = 30) =>
    request(`/signals/news?limit=${limit}${symbols ? `&symbols=${encodeURIComponent(symbols)}` : ""}`),
  portfolioSentiment: () => request("/signals/portfolio-sentiment"),

  // Server-side signal bot (control-api/app/routers/bot.py) - runs on the
  // server (app/bot_scheduler.py), not in this browser tab; these just
  // read/control its state.
  getBotConfig: () => request("/bot/config"),
  putBotConfig: (body) => request("/bot/config", { method: "PUT", body: JSON.stringify(body) }),
  getBotPositions: () => request("/bot/positions"),
  getBotLog: (limit = 60) => request(`/bot/log?limit=${limit}`),
  closeBotPosition: (symbol) => request(`/bot/positions/${encodeURIComponent(symbol)}/close`, { method: "POST" }),
  // Escape hatches for a position stuck open because the broker will never
  // accept a real closing order for it (e.g. a demo-only symbol it doesn't
  // list) - drops it from local tracking without a broker order. See
  // routers/bot.py's force_clear_position/hard_reset for the full rationale.
  forceClearBotPosition: (symbol) => request(`/bot/positions/${encodeURIComponent(symbol)}/force-clear`, { method: "POST" }),
  hardResetBot: () => request("/bot/reset", { method: "POST" }),
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
  provision: (agentId, shardCount, note = "", gatewayReplicas = null) =>
    request(`/fleet/agents/${agentId}/provision`,
      { method: "POST", body: JSON.stringify({ shard_count: shardCount, note,
                                              gateway_replicas: gatewayReplicas }) }),
  deprovision: (agentId) =>
    request(`/fleet/agents/${agentId}/deprovision`, { method: "POST" }),
  listAgentCommands: (agentId, limit = 50) =>
    request(`/fleet/agents/${agentId}/commands?limit=${limit}`),
};

// Auto-reconnects with backoff (1s, 2s, 4s, ... capped at 15s, reset once a
// connection actually opens) - a plain `new WebSocket` here had NO retry at
// all: any transient disruption (a backend redeploy, a brief gateway hiccup,
// a container restart cycling through Docker DNS) closed the socket once and
// left every dashboard using it permanently stuck showing "offline"/stale
// data until a manual page reload, even minutes after the backend recovered
// - confirmed live. Returns an object shaped like the WebSocket subset every
// caller here actually uses (assign .onclose/.onerror, call .close()) so no
// call site needs to change.
export function metricsSocket(onMessage) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${window.location.host}/api/metrics/stream`;
  const handle = { onclose: null, onerror: null };
  let ws = null;
  let closedByCaller = false;
  let retryMs = 1000;

  function connect() {
    ws = new WebSocket(url);
    ws.onmessage = (evt) => onMessage(JSON.parse(evt.data));
    ws.onopen = () => { retryMs = 1000; };
    ws.onerror = (evt) => handle.onerror?.(evt);
    ws.onclose = (evt) => {
      handle.onclose?.(evt);
      if (closedByCaller) return;
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, 15000);
    };
  }
  connect();

  handle.close = () => { closedByCaller = true; ws.close(); };
  return handle;
}
