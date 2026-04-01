import { useState, useEffect, FormEvent } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { GoogleLogin, GoogleOAuthProvider } from "@react-oauth/google";
import { useAuth } from "../contexts/AuthContext";

export default function Login() {
  const { login, loginWithGoogle } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [googleClientId, setGoogleClientId] = useState<string | null>(null);
  const [showDemo, setShowDemo] = useState(false);

  // Fetch Google Client ID from backend config
  useEffect(() => {
    fetch("/api/v1/auth/config")
      .then((r) => r.json())
      .then((data) => {
        if (data.google_client_id) setGoogleClientId(data.google_client_id);
      })
      .catch(() => {}); // silently ignore — Google login just won't show
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleSuccess = async (credentialResponse: any) => {
    setError(null);
    setLoading(true);
    try {
      await loginWithGoogle(credentialResponse.credential);
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.message || "Google login failed");
    } finally {
      setLoading(false);
    }
  };

  const loginForm = (
    <div className="min-h-screen bg-background flex items-center justify-center px-4">
      <Helmet>
        <title>Sign In — AgenticOrg</title>
        <meta name="description" content="Sign in to AgenticOrg to manage your AI agents, workflows, and approvals." />
        <meta name="robots" content="noindex, nofollow" />
        <link rel="canonical" href="https://agenticorg.ai/login" />
      </Helmet>
      <div className="w-full max-w-md">
        <div className="bg-card border border-border rounded-xl shadow-lg p-8">
          {/* Branding */}
          <div className="text-center mb-8">
            <h1 className="text-2xl font-bold tracking-tight">
              <span className="bg-gradient-to-r from-blue-400 to-violet-500 bg-clip-text text-transparent">
                AgenticOrg
              </span>
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Enterprise Agent Swarm Platform
            </p>
          </div>

          {/* Error message */}
          {error && (
            <div className="mb-4 rounded-lg bg-destructive/10 border border-destructive/30 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {/* Google Sign-In */}
          {googleClientId && (
            <>
              <div className="flex justify-center mb-4">
                <GoogleLogin
                  onSuccess={handleGoogleSuccess}
                  onError={() => setError("Google sign-in failed")}
                  size="large"
                  width={350}
                  text="signin_with"
                  shape="rectangular"
                  theme="outline"
                />
              </div>
              <div className="flex items-center gap-3 my-6">
                <div className="flex-1 h-px bg-border" />
                <span className="text-sm font-medium text-muted-foreground uppercase tracking-wide whitespace-nowrap">or sign in with email</span>
                <div className="flex-1 h-px bg-border" />
              </div>
            </>
          )}

          {/* Login form */}
          <form onSubmit={handleSubmit} className="space-y-4" autoComplete="off">
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-foreground mb-1.5">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                autoComplete="off"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label htmlFor="password" className="block text-sm font-medium text-foreground">
                  Password
                </label>
                <Link to="/forgot-password" className="text-xs text-primary hover:text-primary/80 transition-colors">
                  Forgot Password?
                </Link>
              </div>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  required
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 pr-10 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                  tabIndex={-1}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? (
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
                  ) : (
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                  )}
                </button>
              </div>
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Signing in..." : "Sign in with email"}
            </button>
          </form>

          {/* Demo access toggle */}
          <div className="mt-6 text-center">
            <button
              type="button"
              onClick={() => setShowDemo(!showDemo)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {showDemo ? "Hide demo logins" : "Try the demo instead →"}
            </button>
          </div>
          {showDemo && (
            <div className="mt-3 rounded-lg bg-muted/50 border border-border px-4 py-3">
              <p className="text-xs text-muted-foreground text-center mb-2">Click a role to explore with sample data <span className="text-amber-600">(sandbox only — not real credentials)</span>:</p>
              <div className="grid grid-cols-2 gap-1.5">
                {[
                  { role: "CEO/Admin", email: "ceo@agenticorg.local", pw: "ceo123!" },
                  { role: "CFO", email: "cfo@agenticorg.local", pw: "cfo123!", desc: "Finance" },
                  { role: "CHRO", email: "chro@agenticorg.local", pw: "chro123!", desc: "HR" },
                  { role: "CMO", email: "cmo@agenticorg.local", pw: "cmo123!", desc: "Marketing" },
                  { role: "COO", email: "coo@agenticorg.local", pw: "coo123!", desc: "Operations" },
                  { role: "Auditor", email: "auditor@agenticorg.local", pw: "audit123!", desc: "Read-only" },
                ].map(c => (
                  <button key={c.email} type="button"
                    onClick={() => { setEmail(c.email); setPassword(c.pw); }}
                    className="text-left p-2 rounded-lg border border-border hover:bg-muted/50 cursor-pointer transition-colors">
                    <span className="font-medium text-foreground text-xs">{c.role}</span>
                    {c.desc && <span className="text-muted-foreground text-[10px] ml-1">({c.desc})</span>}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Create account — prominent */}
          <div className="mt-6 p-4 rounded-lg border-2 border-dashed border-primary/30 bg-primary/5 text-center">
            <p className="text-sm text-muted-foreground mb-2">New to AgenticOrg?</p>
            <Link to="/signup" className="inline-flex items-center justify-center w-full rounded-lg border border-primary bg-primary/10 px-4 py-2.5 text-sm font-medium text-primary hover:bg-primary/20 transition-colors">
              Create a new organization &rarr;
            </Link>
          </div>

          {/* Back link */}
          <div className="mt-4 text-center">
            <Link to="/" className="text-sm text-muted-foreground hover:text-foreground transition-colors">
              &larr; Back to home
            </Link>
          </div>
        </div>
      </div>
    </div>
  );

  // Wrap in GoogleOAuthProvider only if client ID is available
  if (googleClientId) {
    return (
      <GoogleOAuthProvider clientId={googleClientId}>
        {loginForm}
      </GoogleOAuthProvider>
    );
  }

  return loginForm;
}
