/* Login screen · two modes:
 *
 *   - "noauth"  · skip · the backend has API_KEY unset (dev mode)
 *   - "apikey"  · paste your shared API key
 *   - "jwt"     · paste a JWT (or wire to your IdP redirect later)
 *
 * The skeleton intentionally has NO OAuth redirect flow · plug your
 * provider here:
 *
 *   onLoginAuth0() {
 *     window.location = `${AUTH0_DOMAIN}/authorize?...`;
 *   }
 *
 * Then handle the callback in a /callback route, exchange the code,
 * call setJwt(token) from auth.ts, and you're done.
 */
import { useEffect, useState } from "react";
import { clearAuth, decodeJwt, setApiKey, setJwt } from "../auth";
import { probeServer, verifyAuth } from "../apiClient";

interface Props {
  onSuccess: () => void;
}

type Tab = "noauth" | "apikey" | "jwt";

export default function LoginPanel({ onSuccess }: Props) {
  const [tab, setTab] = useState<Tab>("noauth");
  const [keyInput, setKeyInput] = useState("");
  const [jwtInput, setJwtInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [serverStatus, setServerStatus] = useState<"unknown" | "ok" | "down">("unknown");

  useEffect(() => {
    probeServer().then((r) => setServerStatus(r.ok ? "ok" : "down"));
  }, []);

  const tryNoAuth = async () => {
    setBusy(true);
    setError(null);
    clearAuth();
    const ok = await verifyAuth();
    setBusy(false);
    if (ok) onSuccess();
    else setError("Server requires auth · pick API key or JWT tab.");
  };

  const tryApiKey = async () => {
    if (!keyInput.trim()) {
      setError("API key required");
      return;
    }
    setBusy(true);
    setError(null);
    setApiKey(keyInput.trim());
    const ok = await verifyAuth();
    setBusy(false);
    if (ok) onSuccess();
    else setError("Server rejected the key · check API_KEY in backend/.env");
  };

  const tryJwt = async () => {
    const t = jwtInput.trim();
    if (!t) {
      setError("JWT required");
      return;
    }
    const claims = decodeJwt(t);
    if (!claims) {
      setError("Could not parse JWT · check the token is base64url-encoded");
      return;
    }
    setBusy(true);
    setError(null);
    setJwt(t);
    const ok = await verifyAuth();
    setBusy(false);
    if (ok) {
      onSuccess();
    } else {
      setError(
        "Server rejected the JWT · check JWT_PUBLIC_KEY / JWT_AUDIENCE on the server"
      );
    }
  };

  return (
    <div className="login-shell">
      <div className="login-card">
        <h1 className="login-title">taotao-agent</h1>
        <div className="login-status">
          <span
            className={`login-dot login-dot-${serverStatus}`}
            title={`server: ${serverStatus}`}
          />
          <span>backend: {serverStatus}</span>
        </div>

        <div className="login-tabs" role="tablist">
          <button
            className={`login-tab ${tab === "noauth" ? "active" : ""}`}
            onClick={() => setTab("noauth")}
            type="button"
          >
            no-auth dev
          </button>
          <button
            className={`login-tab ${tab === "apikey" ? "active" : ""}`}
            onClick={() => setTab("apikey")}
            type="button"
          >
            API key
          </button>
          <button
            className={`login-tab ${tab === "jwt" ? "active" : ""}`}
            onClick={() => setTab("jwt")}
            type="button"
          >
            JWT
          </button>
        </div>

        <div className="login-body">
          {tab === "noauth" && (
            <>
              <p className="login-hint">
                Use this when the backend has <code>API_KEY=</code> empty
                (default for <code>make dev</code>).
              </p>
              <button
                className="login-cta"
                onClick={tryNoAuth}
                disabled={busy}
                type="button"
              >
                {busy ? "checking…" : "enter as anonymous"}
              </button>
            </>
          )}

          {tab === "apikey" && (
            <>
              <p className="login-hint">
                Paste the shared key set as <code>API_KEY=</code> on the server.
              </p>
              <input
                className="login-input"
                type="password"
                placeholder="X-API-Key value"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                autoFocus
              />
              <button
                className="login-cta"
                onClick={tryApiKey}
                disabled={busy}
                type="button"
              >
                {busy ? "verifying…" : "sign in"}
              </button>
            </>
          )}

          {tab === "jwt" && (
            <>
              <p className="login-hint">
                Paste a JWT issued by your IdP (Auth0 / Cognito / Authentik).
                In production this screen would redirect to your provider
                instead.
              </p>
              <textarea
                className="login-input login-textarea"
                placeholder="eyJhbGciOiJSUzI1NiIs..."
                value={jwtInput}
                onChange={(e) => setJwtInput(e.target.value)}
                rows={4}
              />
              <button
                className="login-cta"
                onClick={tryJwt}
                disabled={busy}
                type="button"
              >
                {busy ? "verifying…" : "sign in"}
              </button>
            </>
          )}

          {error && <p className="login-error">{error}</p>}
        </div>

        <p className="login-footer">
          Need help? See{" "}
          <a href="/tutorial/saas.html" target="_blank" rel="noopener">
            Book 24 · From Demo to SaaS
          </a>
          .
        </p>
      </div>
    </div>
  );
}
