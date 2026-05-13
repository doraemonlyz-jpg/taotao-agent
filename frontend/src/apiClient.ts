/* Wrapped fetch · attaches auth headers + handles 401.
 *
 * Migration note · the existing `api.ts` uses bare `fetch()` and works
 * unchanged when no auth is configured. New code SHOULD use `request()`
 * from this file so 401s automatically clear the bad token + bounce
 * the user back to the login screen.
 *
 * We kept api.ts compat-mode (bare fetch) instead of mass-rewriting
 * to avoid touching every endpoint when there's still no UI for
 * re-auth · ChatPanel and the rest will get fixed when you wire your
 * IdP.  Token-protected helpers (admin / billing) MUST use this client.
 */
import { authHeaders, clearAuth, getAuth, isJwtExpired } from "./auth";

const BASE = "/api";

export interface RequestOptions extends RequestInit {
  /** Skip auth attachment · for public endpoints like /health */
  skipAuth?: boolean;
  /** Don't auto-clear on 401 · for the login probe itself */
  skipAuthClearOn401?: boolean;
}

export class HttpError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message?: string) {
    super(message || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

export async function request<T = unknown>(
  path: string,
  opts: RequestOptions = {}
): Promise<T> {
  // Pre-flight · evict an expired JWT before hitting the wire.
  // Saves a 401 round trip.
  if (!opts.skipAuth) {
    const auth = getAuth();
    if (auth.mode === "jwt" && auth.token && isJwtExpired(auth.token)) {
      clearAuth();
      throw new HttpError(401, null, "JWT expired · please re-login");
    }
  }

  const headers: Record<string, string> = {
    ...(opts.headers as Record<string, string>),
  };
  // Only set Content-Type when we're actually sending a JSON body.
  if (opts.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  if (!opts.skipAuth) {
    Object.assign(headers, authHeaders());
  }

  const res = await fetch(`${BASE}${path}`, { ...opts, headers });

  if (res.status === 401 && !opts.skipAuthClearOn401) {
    // Token must be bad · drop it so the next render shows the login UI.
    clearAuth();
  }

  // Try to parse JSON · fall back to text if the server didn't send any.
  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!res.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `HTTP ${res.status}`;
    throw new HttpError(res.status, body, detail);
  }

  return body as T;
}

/* Probe the server · hits /health (no auth required) just to verify
 * the backend is reachable. Used by the login screen as a sanity check. */
export async function probeServer(): Promise<{ ok: boolean; model?: string }> {
  try {
    return await request<{ ok: boolean; model?: string }>("/health", {
      skipAuth: true,
    });
  } catch {
    return { ok: false };
  }
}

/* Probe whether the current credentials are accepted · hits /usage/me
 * which requires identity but not admin role.  Returns true on 200,
 * false on 401/403/network. */
export async function verifyAuth(): Promise<boolean> {
  try {
    await request("/usage/me", { skipAuthClearOn401: true });
    return true;
  } catch (e) {
    if (e instanceof HttpError && (e.status === 401 || e.status === 403)) {
      return false;
    }
    return false;
  }
}
