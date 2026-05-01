import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from "react";

const API_BASE = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/api/v1`
  : "/api/v1";

interface AuthUser {
  email: string;
  name: string | null;
  role: string;
  domain: string;
  tenant_id: string;
  onboardingComplete?: boolean;
}

// SEC-002 (PR-F, 2026-05-01): browser session is now COOKIE-FIRST.
//
// The HttpOnly ``agenticorg_session`` cookie is the primary session
// carrier — set by the backend on login/signup/SSO, automatically
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
//     for browser users — code that branches on its presence should
//     use ``isAuthenticated`` instead.
//   - On first render after this change deploys, any stale
//     ``localStorage.token`` from previous versions is purged. The
//     /auth/me hydration will succeed if the server-side cookie is
//     still valid; otherwise the user simply sees the login screen.

interface AuthContextType {
  /** @deprecated Always null in browser sessions. Use ``isAuthenticated``. */
  token: null;
  user: AuthUser | null;
  login: (email: string, password: string) => Promise<void>;
  loginWithGoogle: (credential: string) => Promise<void>;
  loginWithToken: (token: string) => Promise<void>;
  signup: (orgName: string, name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  /** True until the initial /auth/me hydration completes. UI gates
   *  should defer rendering protected routes while this is true. */
  isHydrating: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

const SESSION_FETCH_OPTS: RequestInit = {
  // Browser must send the agenticorg_session HttpOnly cookie on every
  // call. Without this, /auth/me returns 401 even when the cookie is
  // present.
  credentials: "include" as RequestCredentials,
};

function _purgeLegacyTokenStorage() {
  // First-render cleanup: any localStorage left behind from the
  // pre-PR-F build is dead and should be removed so the regression
  // test (and any browser extensions auditing storage) sees a clean
  // surface.
  try {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
  } catch {
    // ignore — private browsing or cookies-disabled paths
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
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
          onboardingComplete: data.onboarding_complete ?? true,
        };
        setUser(sessionUser);
        setIsAuthenticated(true);
        return;
      }
    } catch {
      // best effort — fall through to logged-out state
    }
    setUser(null);
    setIsAuthenticated(false);
  }, []);

  useEffect(() => {
    _purgeLegacyTokenStorage();
    void _hydrateFromCookie().finally(() => setIsHydrating(false));
  }, [_hydrateFromCookie]);

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
    await _hydrateFromCookie();
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
    await _hydrateFromCookie();
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
    await _hydrateFromCookie();
  }, [_hydrateFromCookie]);

  const loginWithToken = useCallback(async (_newToken: string) => {
    // SSO redirect lands a JWT in the URL fragment AND the backend
    // already set the agenticorg_session cookie before the redirect.
    // We just need to hydrate the user object from /auth/me. The raw
    // JWT is intentionally NOT persisted in any browser storage.
    await _hydrateFromCookie();
  }, [_hydrateFromCookie]);

  const logout = useCallback(async () => {
    try {
      // Backend clears the HttpOnly cookie + the paired CSRF cookie.
      await fetch(`${API_BASE}/auth/logout`, {
        method: "POST",
        ...SESSION_FETCH_OPTS,
      });
    } catch {
      // ignore — local state is the source of truth for the SPA after this
    }
    setUser(null);
    setIsAuthenticated(false);
    _purgeLegacyTokenStorage();
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
