/* Top-level guard · renders LoginPanel until the server accepts our
 * credentials (or the user picks no-auth and the server allows it).
 *
 * One-time bootstrap: on mount we hit /usage/me to see if the existing
 * stored credentials still work (handles "user came back tomorrow ·
 * JWT still valid").  If they don't, we wipe them and show the login.
 */
import { useEffect, useState, type ReactNode } from "react";
import { verifyAuth } from "../apiClient";
import { onAuthChange } from "../auth";
import LoginPanel from "./LoginPanel";

interface Props {
  children: ReactNode;
}

type GateState = "checking" | "needs_login" | "authenticated";

export default function AuthGate({ children }: Props) {
  const [state, setState] = useState<GateState>("checking");

  useEffect(() => {
    let cancelled = false;
    verifyAuth().then((ok) => {
      if (cancelled) return;
      setState(ok ? "authenticated" : "needs_login");
    });
    // Re-verify whenever auth changes (logout from elsewhere, cross-tab).
    const off = onAuthChange(() => {
      verifyAuth().then((ok) => {
        if (cancelled) return;
        setState(ok ? "authenticated" : "needs_login");
      });
    });
    return () => {
      cancelled = true;
      off();
    };
  }, []);

  if (state === "checking") {
    return (
      <div className="auth-checking">
        <div className="thinking">
          <span /> <span /> <span />
        </div>
      </div>
    );
  }
  if (state === "needs_login") {
    return <LoginPanel onSuccess={() => setState("authenticated")} />;
  }
  return <>{children}</>;
}
