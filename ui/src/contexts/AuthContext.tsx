import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from "react";
import { useLocation } from "react-router";

const API_BASE = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/api/v1`
  : "/api/v1";

interface AuthUser {
  user_id: string;
  email: string;
  name: string | null;
  role: string;
  domain: string;
  tenant_id: string;
  org_name?: string | null;
  onboardingComplete?: boolean;
}

// SEC-002 (PR-F, 2026-05-01): browser session is now COOKIE-FIRST.
//
// The HttpOnly ``agenticorg_session`` cookie is the primary session
// carrier â€” set by the backend on login/signup/SSO, automatically
// echoed by the browser on every same-origin XHR (because
// ``withCredentials: true`` is configured globally in
// ui/src/lib/api.ts), and validated by the backend's auth middleware.
//
// We DO NOT store ``access_token`` or ``user`` in localStorage anymore.
// Any XSS, malicious browser extension, or third-party script
// compromise that previously could have stolen the bearer can no
// longer reach the cookie because of HttpOnly. The user object is
// rehydrated each session boot via ``GET /auth/me``.
//
// Backwards compatibility:
//   - The ``token`` field on the context is still present so existing
//     consumers don't break, but it is intentionally always ``null``
//     for browser users â€” code that branches on its presence should
//     use ``isAuthenticated`` instead.
//   - On first render after this change deploys, any stale
//     ``localStorage.token`` from previous versions is purged. The
//     /auth/me hydration will succeed if the server-side cookie is
//     still valid; otherwise the user simply sees the login screen.

interface AuthContextType {
  /** @deprecated Always null in browser sessions. Use ``isAuthenticated``. */
  token: null;
  user: AuthUser | null;
  /** Resolve with the hydrated session user so callers can route by role. */
  login: (email: string, password: string) => Promise<AuthUser>;
  loginWithGoogle: (credential: string) => Promise<AuthUser>;
  loginWithToken: (token?: string) => Promise<AuthUser>;
  signup: (orgName: string, name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  /** True until the initial /auth/me hydration completes. UI gates
   *  should defer rendering protected routes while this is true. */
  isHydrating: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

/** Where a freshly signed-in user should land. ``/dashboard`` is gated to
 *  the CxO/admin/auditor roles (see App.tsx); a merchant operator only has
 *  access to the commerce runtime, so sending them to ``/dashboard`` produced
 *  an Access Denied page on every login. */
export function defaultLandingForRole(role: string | null | undefined): string {
  if (role === "merchant") return "/dashboard/commerce-runtime";
  // Bug sheet 2026-09-14 rows 17-19/52: domain_lead and developer are not
  // admitted to /dashboard but can build personal agents, so land them on
  // the agent fleet instead of an Access Denied page.
  if (role === "domain_lead" || role === "developer") return "/dashboard/agents";
  return "/dashboard";
}

export function shouldHydrateSessionForPath(pathname: string) {
  return (
    pathname === "/onboarding" ||
    pathname === "/dashboard" ||
    pathname.startsWith("/dashboard/")
  );
}

const SESSION_FETCH_OPTS: RequestInit = {
  // Browser must send the agenticorg_session HttpOnly cookie on every
  // call. Without this, /auth/me returns 401 even when the cookie is
  // present.
  credentials: "include" as RequestCredentials,
};

function readCookie(name: string): string {
  const match = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/[$()*+./?[\]\\^{|}]/g, "\\$&") + "=([^;]*)"),
  );
  return match ? decodeURIComponent(match[1]) : "";
}

function _purgeLegacyTokenStorage() {
  // First-render cleanup: any localStorage left behind from the
  // pre-PR-F build is dead and should be removed so the regression
  // test (and any browser extensions auditing storage) sees a clean
  // surface.
  try {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
  } catch {
    // ignore â€” private browsing or cookies-disabled paths
  }
}

function _clearPerSessionSelections() {
  // The selected company is tenant-scoped state; it must not survive a
  // logout, otherwise the next user on this browser inherits a company id
  // from another tenant and every scoped fetch returns an empty set.
  try {
    localStorage.removeItem("company_id");
  } catch {
    // ignore
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isHydrating, setIsHydrating] = useState(true);

  const _hydrateFromCookie = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/me`, SESSION_FETCH_OPTS);
      if (res.ok) {
        const data = await res.json();
        const sessionUser: AuthUser = {
          ...data,
          onboardingComplete: data.onboarding_complete ?? false,
        };
        setUser(sessionUser);
        setIsAuthenticated(true);
        return sessionUser;
      }
    } catch {
      // best effort â€” fall through to logged-out state
    }
    setUser(null);
    setIsAuthenticated(false);
    return null;
  }, []);

  useEffect(() => {
    _purgeLegacyTokenStorage();
    if (!shouldHydrateSessionForPath(location.pathname)) {
      setIsHydrating(false);
      return;
    }
    setIsHydrating(true);
    void _hydrateFromCookie().finally(() => setIsHydrating(false));
  }, [_hydrateFromCookie, location.pathname]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      ...SESSION_FETCH_OPTS,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Login failed" }));
      throw new Error(err.detail || "Login failed");
    }
    const data = await res.json();
    // Cookie was set by the backend response. Hydrate from /auth/me
    // (single source of truth) so we never rely on the response body's
    // user shape, which has drifted across endpoints in the past.
    const sessionUser = await _hydrateFromCookie();
    if (!sessionUser) throw new Error("Login succeeded but the session could not be verified");
    const loginUser = data.user;
    if (loginUser) {
      import("@/components/Analytics").then(m => {
        m.trackEvent("login", { method: "email" });
        m.identifyUser({
          user_id: loginUser.email,
          role: loginUser.role,
          tenant_id: loginUser.tenant_id,
        });
      }).catch(() => {});
    }
    return sessionUser;
  }, [_hydrateFromCookie]);

  const signup = useCallback(async (orgName: string, name: string, email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ org_name: orgName, admin_name: name, admin_email: email, password }),
      ...SESSION_FETCH_OPTS,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Signup failed" }));
      throw new Error(err.detail || "Signup failed");
    }
    const sessionUser = await _hydrateFromCookie();
    if (!sessionUser) throw new Error("Signup succeeded but the session could not be verified");
  }, [_hydrateFromCookie]);

  const loginWithGoogle = useCallback(async (credential: string) => {
    const res = await fetch(`${API_BASE}/auth/google`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential }),
      ...SESSION_FETCH_OPTS,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Google login failed" }));
      throw new Error(err.detail || "Google login failed");
    }
    const sessionUser = await _hydrateFromCookie();
    if (!sessionUser) throw new Error("Google login succeeded but the session could not be verified");
    return sessionUser;
  }, [_hydrateFromCookie]);

  const loginWithToken = useCallback(async (_legacyToken?: string) => {
    // The OIDC callback establishes an HttpOnly cookie before redirecting.
    // A token argument is accepted only for compatibility with old callback
    // URLs and is never persisted or used as browser authentication.
    const sessionUser = await _hydrateFromCookie();
    if (!sessionUser) throw new Error("Could not verify the SSO session");
    return sessionUser;
  }, [_hydrateFromCookie]);

  const logout = useCallback(async () => {
    let res: Response;
    try {
      // Backend clears the HttpOnly cookie + the paired CSRF cookie.
      const csrf = readCookie("agenticorg_csrf");
      res = await fetch(`${API_BASE}/auth/logout`, {
        method: "POST",
        headers: csrf ? { "X-CSRF-Token": csrf } : undefined,
        ...SESSION_FETCH_OPTS,
      });
    } catch {
      throw new Error("Could not reach the server to revoke this session");
    }
    if (!res.ok) {
      throw new Error("The server could not revoke this session. Please try again.");
    }
    setUser(null);
    setIsAuthenticated(false);
    _purgeLegacyTokenStorage();
    _clearPerSessionSelections();
  }, []);

  return (
    <AuthContext.Provider
      value={{
        token: null,
        user,
        login,
        loginWithGoogle,
        loginWithToken,
        signup,
        logout,
        isAuthenticated,
        isHydrating,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
