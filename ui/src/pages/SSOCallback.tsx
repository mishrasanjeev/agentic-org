import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@/contexts/AuthContext";

/**
 * SSOCallback — landing page after the OIDC flow.
 *
 * The backend redirects the user's browser to ``/sso/callback#token=<jwt>``
 * after a successful OIDC code exchange. We pull the token out of the
 * URL fragment (so it never lands in server access logs), hand it to
 * the auth context, and then bounce the user into the dashboard.
 *
 * If the URL has no token (e.g. user hit Back), show an error and
 * redirect to /login.
 */
export default function SSOCallback() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fragment = window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : window.location.hash;
    const params = new URLSearchParams(fragment);
    const token = params.get("token");
    const errorMsg = params.get("error");

    if (errorMsg) {
      setError(errorMsg);
      const t = window.setTimeout(() => navigate("/login", { replace: true }), 3000);
      return () => window.clearTimeout(t);
    }

    if (!token) {
      setError("No SSO token returned by the identity provider.");
      const t = window.setTimeout(() => navigate("/login", { replace: true }), 3000);
      return () => window.clearTimeout(t);
    }

    auth
      .loginWithToken(token)
      .then(() => {
        // Strip the fragment from the URL so the token doesn't sit in
        // browser history, then route to the dashboard.
        window.history.replaceState(null, "", "/sso/callback");
        navigate("/dashboard", { replace: true });
      })
      .catch((e) => {
        setError(String(e?.message || e));
        const t = window.setTimeout(() => navigate("/login", { replace: true }), 3000);
        return () => window.clearTimeout(t);
      });
  }, [auth, navigate]);

  return (
    <div
      className="flex min-h-screen items-center justify-center p-6"
      role="status"
      aria-live="polite"
    >
      <div className="max-w-md text-center">
        {error ? (
          <>
            <h1 className="text-xl font-semibold text-destructive">SSO sign-in failed</h1>
            <p className="mt-2 text-sm text-muted-foreground">{error}</p>
            <p className="mt-4 text-xs text-muted-foreground">
              Redirecting you to the login page…
            </p>
          </>
        ) : (
          <>
            <h1 className="text-xl font-semibold">Completing sign-in…</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Verifying your identity and starting your session.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
