import { useState, useEffect, FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { Helmet } from "react-helmet-async";

export default function InviteAccept() {
  // SEC-002 (PR-F): after acceptance, we use a full-page navigation
  // (window.location.assign) so AuthProvider re-mounts and re-runs
  // the cookie-based hydration. The react-router useNavigate hook is
  // not appropriate here because a SPA route change wouldn't re-run
  // AuthProvider's mount effect.
  const [searchParams] = useSearchParams();
  // MEDIUM-10: ``code`` is the new opaque one-time identifier. ``token``
  // is kept for backward-compat with links issued before the change.
  const inviteCode = searchParams.get("code");
  const legacyToken = searchParams.get("token");
  const inviteId = inviteCode || legacyToken;

  const [orgName, setOrgName] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(true);

  useEffect(() => {
    if (!inviteId) {
      setError("Invalid invite link — no code provided.");
      setFetching(false);
      return;
    }
    const param = inviteCode
      ? `code=${encodeURIComponent(inviteCode)}`
      : `token=${encodeURIComponent(legacyToken || "")}`;
    fetch(`/api/v1/org/invite-info?${param}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("Invalid invite"))))
      .then((data) => {
        setOrgName(data.org_name || "your organization");
      })
      .catch(() => {
        setOrgName("your organization");
      })
      .finally(() => setFetching(false));
  }, [inviteId, inviteCode, legacyToken]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!inviteId) return;
    setError(null);
    setLoading(true);
    try {
      const payload: Record<string, unknown> = { name, password };
      if (inviteCode) payload.code = inviteCode;
      else if (legacyToken) payload.token = legacyToken;
      const res = await fetch("/api/v1/org/accept-invite", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Failed to accept invite" }));
        throw new Error(err.detail || "Failed to accept invite");
      }
      // SEC-002 (PR-F): backend has set the HttpOnly session cookie.
      // We do NOT persist the access_token in localStorage. A full
      // page reload to /dashboard re-runs AuthProvider hydration via
      // /auth/me, which then knows about the new session.
      window.location.assign("/dashboard");
    } catch (err: any) {
      setError(err.message || "Failed to accept invite");
    } finally {
      setLoading(false);
    }
  };

  if (fetching) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <p className="text-muted-foreground">Loading invite...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-4">
      <Helmet>
        <title>Accept Invite — AgenticOrg</title>
      </Helmet>
      <div className="w-full max-w-md">
        <div className="bg-card border border-border rounded-xl shadow-lg p-8">
          {/* Branding */}
          <div className="text-center mb-8">
            <h1 className="text-2xl font-bold tracking-tight">
              <span className="bg-gradient-to-r from-blue-400 to-cyan-500 bg-clip-text text-transparent">
                AgenticOrg
              </span>
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Join <span className="font-semibold text-foreground">{orgName}</span> on AgenticOrg
            </p>
          </div>

          {/* Error */}
          {error && (
            <div className="mb-4 rounded-lg bg-destructive/10 border border-destructive/30 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {!inviteId ? (
            <p className="text-sm text-muted-foreground text-center">
              This invite link is invalid. Please ask your administrator for a new one.
            </p>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="inviteName" className="block text-sm font-medium text-foreground mb-1.5">
                  Your Name
                </label>
                <input
                  id="inviteName"
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Jane Doe"
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
                />
              </div>
              <div>
                <label htmlFor="invitePassword" className="block text-sm font-medium text-foreground mb-1.5">
                  Password
                </label>
                <input
                  id="invitePassword"
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Create a password"
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? "Joining..." : "Join organization"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
