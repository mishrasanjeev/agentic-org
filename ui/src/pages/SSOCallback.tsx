import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@/contexts/AuthContext";

/** Complete the cookie-first OIDC redirect and hydrate the browser session. */
export default function SSOCallback() {
  const { loginWithToken } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let redirectTimer: number | undefined;
    const cleanup = () => {
      active = false;
      if (redirectTimer !== undefined) window.clearTimeout(redirectTimer);
    };
    const fail = (message: string) => {
      if (!active) return;
      setError(message);
      redirectTimer = window.setTimeout(
        () => navigate("/login", { replace: true }),
        3000,
      );
    };

    const fragment = window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : window.location.hash;
    const params = new URLSearchParams(fragment);
    const legacyToken = params.get("token");
    const errorMessage = params.get("error");

    if (errorMessage) {
      fail(errorMessage);
      return cleanup;
    }

    // New callbacks arrive with an HttpOnly cookie and no bearer fragment.
    // The optional fragment is accepted only while old redirects age out;
    // AuthContext never stores or uses it as browser authentication.
    void loginWithToken(legacyToken || undefined)
      .then(() => {
        if (!active) return;
        window.history.replaceState(null, "", "/sso/callback");
        navigate("/dashboard", { replace: true });
      })
      .catch((reason) => fail(String(reason?.message || reason)));

    return cleanup;
  }, [loginWithToken, navigate]);

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
              Redirecting you to the login page...
            </p>
          </>
        ) : (
          <>
            <h1 className="text-xl font-semibold">Completing sign-in...</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Verifying your identity and starting your session.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
