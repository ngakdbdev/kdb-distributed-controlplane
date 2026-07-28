const BASE = "/api";

function authHeaders() {
  const token = localStorage.getItem("kcp_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });
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
  startService: (service) => request(`/topology/service/${service}/start`, { method: "POST" }),
  stopService: (service) => request(`/topology/service/${service}/stop`, { method: "POST" }),
  restartService: (service) => request(`/topology/service/${service}/restart`, { method: "POST" }),
  serviceLogs: (service, tail = 200) => request(`/topology/service/${service}/logs?tail=${tail}`),

  metricsSnapshot: () => request("/metrics/snapshot"),

  listConnectors: () => request("/connectors"),
  toggleConnector: (id) => request(`/connectors/${id}/toggle`, { method: "POST" }),

  listSubscribers: () => request("/subscribers"),
  addSubscriber: (sub) => request("/subscribers", { method: "POST", body: JSON.stringify(sub) }),
  removeSubscriber: (id) => request(`/subscribers/${id}`, { method: "DELETE" }),

  listAudit: (limit = 100) => request(`/audit?limit=${limit}`),
};

export function metricsSocket(onMessage) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${window.location.host}/api/metrics/stream`);
  ws.onmessage = (evt) => onMessage(JSON.parse(evt.data));
  return ws;
}
