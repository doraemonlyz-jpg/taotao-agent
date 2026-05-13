/* React hook · re-renders the consumer when auth changes (login,
 * logout, expired, cross-tab change).  Used by AuthGate + the
 * topbar's identity badge. */
import { useEffect, useState } from "react";
import { decodeJwt, getAuth, isJwtExpired, onAuthChange } from "./auth";

export interface UseAuthState {
  mode: "noauth" | "apikey" | "jwt";
  authenticated: boolean;
  expired: boolean;
  /** When mode=jwt and parseable, the decoded claims · null otherwise. */
  claims: ReturnType<typeof decodeJwt>;
}

export function useAuth(): UseAuthState {
  const [state, setState] = useState<UseAuthState>(() => compute());

  useEffect(() => {
    const off = onAuthChange(() => setState(compute()));
    return off;
  }, []);

  return state;
}

function compute(): UseAuthState {
  const a = getAuth();
  if (a.mode === "noauth") {
    // Treat noauth as "not yet picked" until LoginPanel sends a Verify.
    // App.tsx flips to authenticated after a successful /usage/me probe.
    return { mode: "noauth", authenticated: false, expired: false, claims: null };
  }
  if (a.mode === "jwt" && a.token) {
    const expired = isJwtExpired(a.token);
    return {
      mode: "jwt",
      authenticated: !expired,
      expired,
      claims: decodeJwt(a.token),
    };
  }
  if (a.mode === "apikey" && a.token) {
    return { mode: "apikey", authenticated: true, expired: false, claims: null };
  }
  return { mode: "noauth", authenticated: false, expired: false, claims: null };
}
