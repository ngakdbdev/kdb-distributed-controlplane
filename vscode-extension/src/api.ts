/**
 * api.ts - thin client for the control-api query workspace endpoints.
 *
 * Mirrors web-ui/src/api.js's request()/queryTargets/runQuery/nl2q/etc. so
 * this extension and the web Query workspace stay behaviorally identical
 * against the same backend - same paths, same auth header, same timeouts.
 * The one difference: the web app talks through a same-origin "/api" proxy
 * (Caddy/nginx); this extension has no such proxy, so it hits the
 * control-api base URL (tickhouse.apiUrl) directly.
 */

export interface TokenResponse {
  access_token: string;
  token_type: string;
  role: string;
  tenant_id: number | null;
}

export interface QueryTarget {
  id: string;
  label: string;
  host: string;
  port: number;
}

export interface TargetsResponse {
  targets: QueryTarget[];
  allow_write: boolean;
  row_limit_default: number;
  row_limit_max: number;
}

export interface QueryGrid {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  kind: string;
  query?: string;
  elapsed_ms?: number;
  target?: string;
  warning?: string;
  routed_shards?: string[] | null;
  skipped_shards?: string[];
  per_target?: Array<{ target: string; ok: boolean; rows: number; error: string | null; elapsed_ms: number }>;
}

export interface Nl2qResponse {
  ok: boolean;
  q: string | null;
  provider: string | null;
  error: string | null;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const DEFAULT_TIMEOUT_MS = 12000;
const LLM_TIMEOUT_MS = 90000;

export class TickHouseClient {
  constructor(private baseUrl: string, private getToken: () => string | undefined) {}

  private async request<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const token = this.getToken();
    let res: Response;
    try {
      res = await fetch(`${this.baseUrl.replace(/\/$/, "")}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(init.headers || {}),
        },
      });
    } catch (err: any) {
      if (err?.name === "AbortError") {
        throw new ApiError(0, `Request timed out after ${timeoutMs / 1000}s (${path})`);
      }
      throw new ApiError(0, `Could not reach ${this.baseUrl} - ${err?.message || err}`);
    } finally {
      clearTimeout(timer);
    }
    const text = await res.text();
    if (!res.ok) {
      throw new ApiError(res.status, `${res.status} ${res.statusText}: ${text}`);
    }
    return text ? (JSON.parse(text) as T) : (null as unknown as T);
  }

  login(email: string, password: string): Promise<TokenResponse> {
    return this.request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }

  queryTargets(): Promise<TargetsResponse> {
    return this.request<TargetsResponse>("/query/targets");
  }

  queryTables(target: string): Promise<{ tables: string[] }> {
    return this.request(`/query/tables?target=${encodeURIComponent(target)}`);
  }

  runQuery(body: { target?: string; targets?: string[]; query: string; limit: number; allow_write?: boolean }): Promise<QueryGrid> {
    return this.request<QueryGrid>("/query/run", { method: "POST", body: JSON.stringify(body) });
  }

  nl2q(text: string, target: string): Promise<Nl2qResponse> {
    return this.request<Nl2qResponse>(
      "/query/nl2q",
      { method: "POST", body: JSON.stringify({ text, target }) },
      LLM_TIMEOUT_MS,
    );
  }
}
