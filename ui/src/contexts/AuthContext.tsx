import { createContext, useContext, useState, useCallback, ReactNode } from "react";

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

interface AuthContextType {
  token: string | null;
  user: AuthUser | null;
  login: (email: string, password: string) => Promise<void>;
  loginWithGoogle: (credential: string) => Promise<void>;
  signup: (orgName: string, name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("token"));
  const [user, setUser] = useState<AuthUser | null>(() => {
    const s = localStorage.getItem("user");
    return s ? JSON.parse(s) : null;
  });

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Login failed" }));
      throw new Error(err.detail || "Login failed");
    }
    const data = await res.json();
    const loginUser: AuthUser = { ...data.user, onboardingComplete: data.user?.onboarding_complete ?? true };
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("user", JSON.stringify(loginUser));
    setToken(data.access_token);
    setUser(loginUser);
    import("@/components/Analytics").then(m => {
      m.trackEvent("login", { method: "email" });
      m.identifyUser({ user_id: loginUser.email, role: loginUser.role, tenant_id: loginUser.tenant_id });
    }).catch(() => {});
  }, []);

  const signup = useCallback(async (orgName: string, name: string, email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ org_name: orgName, admin_name: name, admin_email: email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Signup failed" }));
      throw new Error(err.detail || "Signup failed");
    }
    const data = await res.json();
    const userData: AuthUser = { ...data.user, onboardingComplete: data.user?.onboarding_complete ?? false };
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("user", JSON.stringify(userData));
    setToken(data.access_token);
    setUser(userData);
  }, []);

  const loginWithGoogle = useCallback(async (credential: string) => {
    const res = await fetch(`${API_BASE}/auth/google", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Google login failed" }));
      throw new Error(err.detail || "Google login failed");
    }
    const data = await res.json();
    const googleUser: AuthUser = { ...data.user, onboardingComplete: data.user?.onboarding_complete ?? true };
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("user", JSON.stringify(googleUser));
    setToken(data.access_token);
    setUser(googleUser);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ token, user, login, loginWithGoogle, signup, logout, isAuthenticated: !!token }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
