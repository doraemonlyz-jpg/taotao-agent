/* Client-side auth · token storage + header injection.
 *
 * Two modes match the backend's `agent.auth` resolver:
 *
 *   - "apikey" · single shared X-API-Key header
 *       Set via UI · stored in localStorage as taotao.auth.apikey
 *   - "jwt"    · per-user Bearer token from your IdP (Auth0, Cognito,
 *               Authentik, etc.)  Stored as taotao.auth.jwt
 *
 * For the skeleton we don't ship an OAuth redirect flow · users
 * either paste their JWT directly (admin / dev) or run the demo with
 * `noauth` mode (default · backend has no API_KEY set).
 *
 * Production: replace the LoginPanel with an Auth0/Cognito redirect,
 * exchange the code for a JWT, then call setJwt(token).  The rest of
 * the codebase doesn't change.
 *
 * Why localStorage and not httpOnly cookies:
 *   - Vite dev server can't set cookies for the FastAPI domain easily.
 *   - localStorage is XSS-vulnerable but our threat model accepts that
 *     for an internal admin surface (your IdP enforces 2FA upstream).
 *   - For a real public SaaS, swap to httpOnly cookies set by the
 *     /auth/callback endpoint and remove the storage code entirely.
 */
const KEY_MODE = "taotao.auth.mode";
const KEY_API = "taotao.auth.apikey";
const KEY_JWT = "taotao.auth.jwt";

export type AuthMode = "noauth" | "apikey" | "jwt";

interface AuthState {
  mode: AuthMode;
  token: string | null; // For jwt mode = the bearer; for apikey = the key
}

/* Read current credentials · synchronous · safe at boot. */
export function getAuth(): AuthState {
  if (typeof window === "undefined") {
    return { mode: "noauth", token: null };
  }
  const mode = (window.localStorage.getItem(KEY_MODE) as AuthMode | null) || "noauth";
  if (mode === "apikey") {
    return { mode, token: window.localStorage.getItem(KEY_API) };
  }
  if (mode === "jwt") {
    return { mode, token: window.localStorage.getItem(KEY_JWT) };
  }
  return { mode: "noauth", token: null };
}

export function setApiKey(key: string): void {
  window.localStorage.setItem(KEY_MODE, "apikey");
  window.localStorage.setItem(KEY_API, key);
  window.localStorage.removeItem(KEY_JWT);
  notifyChange();
}

export function setJwt(token: string): void {
  window.localStorage.setItem(KEY_MODE, "jwt");
  window.localStorage.setItem(KEY_JWT, token);
  window.localStorage.removeItem(KEY_API);
  notifyChange();
}

export function clearAuth(): void {
  window.localStorage.removeItem(KEY_MODE);
  window.localStorage.removeItem(KEY_API);
  window.localStorage.removeItem(KEY_JWT);
  notifyChange();
}

/* Decoded JWT payload · WITHOUT signature verification (server does that).
 * We only use this to render "you're logged in as ${email}" + auto-logout
 * when the token's `exp` claim is in the past.  Never trust client-side
 * JWT decoding for authorization decisions. */
export interface JwtPayload {
  sub?: string;
  email?: string;
  tenant_id?: string;
  exp?: number;
  iat?: number;
  roles?: string[];
}

export function decodeJwt(token: string): JwtPayload | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    // base64url → base64 → utf8
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const json = atob(padded);
    return JSON.parse(json) as JwtPayload;
  } catch {
    return null;
  }
}

/* True when the JWT's `exp` is in the past · forces a re-login. */
export function isJwtExpired(token: string): boolean {
  const claims = decodeJwt(token);
  if (!claims?.exp) return false;
  return claims.exp * 1000 < Date.now();
}

/* Build the Headers to attach to every API request. */
export function authHeaders(): Record<string, string> {
  const a = getAuth();
  if (a.mode === "apikey" && a.token) {
    return { "X-API-Key": a.token };
  }
  if (a.mode === "jwt" && a.token) {
    return { Authorization: `Bearer ${a.token}` };
  }
  return {};
}

/* ----------------- Cross-tab + cross-component sync ----------------- */
type Listener = () => void;
const listeners = new Set<Listener>();

export function onAuthChange(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notifyChange(): void {
  for (const fn of listeners) fn();
  // Also broadcast across browser tabs · the `storage` event fires in
  // OTHER tabs only, so we still need to fire in-tab listeners above.
  window.dispatchEvent(new StorageEvent("storage", { key: KEY_MODE }));
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (
      e.key === KEY_MODE ||
      e.key === KEY_API ||
      e.key === KEY_JWT ||
      e.key === null
    ) {
      for (const fn of listeners) fn();
    }
  });
}
