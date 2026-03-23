import { useState, useEffect, FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Helmet } from "react-helmet-async";

export default function InviteAccept() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const inviteToken = searchParams.get("token");

  const [orgName, setOrgName] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(true);

  useEffect(() => {
    if (!inviteToken) {
      setError("Invalid invite link — no token provided.");
      setFetching(false);
      return;
    }
    // Optionally fetch invite details to show org name
    fetch(`/api/v1/org/invite-info?token=${encodeURIComponent(inviteToken)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("Invalid invite"))))
      .then((data) => {
        setOrgName(data.org_name || "your organization");
      })
      .catch(() => {
        setOrgName("your organization");
      })
      .finally(() => setFetching(false));
  }, [inviteToken]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!inviteToken) return;
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/v1/org/accept-invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: inviteToken, name, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Failed to accept invite" }));
        throw new Error(err.detail || "Failed to accept invite");
      }
      const data = await res.json();
      localStorage.setItem("token", data.access_token);
      localStorage.setItem("user", JSON.stringify(data.user));
      navigate("/dashboard");
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
              <span className="bg-gradient-to-r from-blue-400 to-violet-500 bg-clip-text text-transparent">
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

          {!inviteToken ? (
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
