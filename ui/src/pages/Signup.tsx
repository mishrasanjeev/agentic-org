import { useState, useEffect, FormEvent } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { GoogleLogin, GoogleOAuthProvider } from "@react-oauth/google";
import { useAuth } from "../contexts/AuthContext";

export default function Signup() {
  const { signup, loginWithGoogle } = useAuth();
  const navigate = useNavigate();
  const [orgName, setOrgName] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [googleClientId, setGoogleClientId] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/v1/auth/config")
      .then((r) => r.json())
      .then((data) => { if (data.google_client_id) setGoogleClientId(data.google_client_id); })
      .catch(() => {});
  }, []);

  // Clear top-level error when user edits any field
  useEffect(() => { setError(null); }, [orgName, name, email, password, confirmPassword]);

  // Real-time password match validation
  useEffect(() => {
    if (!confirmPassword) {
      setFieldErrors(prev => { const { confirmPassword: _, ...rest } = prev; return rest; });
      return;
    }
    if (password !== confirmPassword) {
      setFieldErrors(prev => ({ ...prev, confirmPassword: "Passwords do not match" }));
    } else {
      setFieldErrors(prev => { const { confirmPassword: _, ...rest } = prev; return rest; });
    }
  }, [password, confirmPassword]);

  // Real-time password strength
  useEffect(() => {
    if (!password) {
      setFieldErrors(prev => { const { password: _, ...rest } = prev; return rest; });
      return;
    }
    if (password.length < 8) {
      setFieldErrors(prev => ({ ...prev, password: "Password must be at least 8 characters" }));
    } else if (!/[A-Z]/.test(password)) {
      setFieldErrors(prev => ({ ...prev, password: "Include at least one uppercase letter" }));
    } else if (!/[0-9!@#$%^&*]/.test(password)) {
      setFieldErrors(prev => ({ ...prev, password: "Include a number or special character" }));
    } else {
      setFieldErrors(prev => { const { password: _, ...rest } = prev; return rest; });
    }
  }, [password]);

  const handleGoogleSuccess = async (credentialResponse: any) => {
    setError(null);
    setLoading(true);
    try {
      await loginWithGoogle(credentialResponse.credential);
      import("@/components/Analytics").then(m => m.trackEvent("sign_up", { method: "google" }));
      navigate("/onboarding");
    } catch (err: any) {
      setError(err.message || "Google signup failed");
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validate all fields
    const errors: Record<string, string> = {};
    if (!orgName.trim()) errors.orgName = "Organization name is required";
    if (!name.trim()) errors.name = "Your name is required";
    if (!email.trim()) errors.email = "Email is required";
    if (password.length < 8) errors.password = "Password must be at least 8 characters";
    if (password !== confirmPassword) errors.confirmPassword = "Passwords do not match";

    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      // Show the most important error at the top too
      const firstError = Object.values(errors)[0];
      setError(firstError);
      return;
    }

    setLoading(true);
    try {
      await signup(orgName, name, email, password);
      import("@/components/Analytics").then(m => m.trackEvent("sign_up", { method: "email" }));
      navigate("/onboarding");
    } catch (err: any) {
      const msg = err.message || "Signup failed";
      // Parse backend errors for better messages
      if (msg.includes("already exists") || msg.includes("duplicate")) {
        setError("An account with this email already exists. Try signing in instead.");
      } else if (msg.includes("password")) {
        setError(msg);
        setFieldErrors(prev => ({ ...prev, password: msg }));
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  };

  const inputClass = (field: string) =>
    `w-full rounded-lg border px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 transition-colors ${
      fieldErrors[field]
        ? "border-red-500 bg-red-50/50 focus:border-red-500 focus:ring-red-500/30"
        : "border-border bg-background focus:border-primary"
    }`;

  const signupForm = (
    <div className="min-h-screen bg-background flex items-center justify-center px-4">
      <Helmet>
        <title>Create Account — AgenticOrg</title>
        <meta name="description" content="Create your AgenticOrg account to manage AI agents, workflows, and approvals." />
        <link rel="canonical" href="https://agenticorg.ai/signup" />
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
              Create your organization
            </p>
          </div>

          {/* Top-level error */}
          {error && (
            <div className="mb-4 rounded-lg bg-destructive/10 border border-destructive/30 px-4 py-3 text-sm text-destructive flex items-start gap-2">
              <svg className="w-4 h-4 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" /></svg>
              <span>{error}</span>
            </div>
          )}

          {/* Signup form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="orgName" className="block text-sm font-medium text-foreground mb-1.5">
                Organization Name
              </label>
              <input
                id="orgName"
                type="text"
                required
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="Acme Corp"
                className={inputClass("orgName")}
              />
              {fieldErrors.orgName && <p className="mt-1 text-xs text-red-500">{fieldErrors.orgName}</p>}
            </div>
            <div>
              <label htmlFor="name" className="block text-sm font-medium text-foreground mb-1.5">
                Your Name
              </label>
              <input
                id="name"
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Jane Doe"
                className={inputClass("name")}
              />
              {fieldErrors.name && <p className="mt-1 text-xs text-red-500">{fieldErrors.name}</p>}
            </div>
            <div>
              <label htmlFor="signupEmail" className="block text-sm font-medium text-foreground mb-1.5">
                Email
              </label>
              <input
                id="signupEmail"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                className={inputClass("email")}
              />
              {fieldErrors.email && <p className="mt-1 text-xs text-red-500">{fieldErrors.email}</p>}
            </div>
            <div>
              <label htmlFor="signupPassword" className="block text-sm font-medium text-foreground mb-1.5">
                Password
              </label>
              <input
                id="signupPassword"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Min 8 chars, uppercase + number/symbol"
                className={inputClass("password")}
              />
              {fieldErrors.password && <p className="mt-1 text-xs text-red-500">{fieldErrors.password}</p>}
              {password && !fieldErrors.password && (
                <p className="mt-1 text-xs text-emerald-500">Password strength: Good</p>
              )}
            </div>
            <div>
              <label htmlFor="confirmPassword" className="block text-sm font-medium text-foreground mb-1.5">
                Confirm Password
              </label>
              <input
                id="confirmPassword"
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Re-enter your password"
                className={inputClass("confirmPassword")}
              />
              {fieldErrors.confirmPassword && (
                <p className="mt-1 text-xs text-red-500">{fieldErrors.confirmPassword}</p>
              )}
              {confirmPassword && !fieldErrors.confirmPassword && (
                <p className="mt-1 text-xs text-emerald-500">Passwords match</p>
              )}
            </div>
            <button
              type="submit"
              disabled={loading || Object.keys(fieldErrors).length > 0}
              className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Creating account..." : "Create account"}
            </button>
          </form>

          {/* Google signup */}
          {googleClientId && (
            <div className="mt-4">
              <div className="relative my-4">
                <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-border" /></div>
                <div className="relative flex justify-center text-xs uppercase"><span className="bg-card px-2 text-muted-foreground">Or</span></div>
              </div>
              <div className="flex justify-center">
                <GoogleLogin
                  onSuccess={handleGoogleSuccess}
                  onError={() => setError("Google sign-up failed")}
                  size="large"
                  width={350}
                  text="signup_with"
                />
              </div>
            </div>
          )}

          {/* Sign-in link */}
          <div className="mt-6 text-center">
            <p className="text-sm text-muted-foreground">
              Already have an account?{" "}
              <Link to="/login" className="text-primary hover:underline">
                Sign in
              </Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  );

  if (googleClientId) {
    return (
      <GoogleOAuthProvider clientId={googleClientId}>
        {signupForm}
      </GoogleOAuthProvider>
    );
  }
  return signupForm;
}
