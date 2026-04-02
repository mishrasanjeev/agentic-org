import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { useAuth } from "../contexts/AuthContext";

interface InviteRow {
  role: string;
  name: string;
  email: string;
}

export default function Onboarding() {
  const { user, token } = useAuth();
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [industry, setIndustry] = useState("");
  const [orgSize, setOrgSize] = useState("");
  const [invites, setInvites] = useState<InviteRow[]>([
    { role: "CFO", name: "", email: "" },
    { role: "CHRO", name: "", email: "" },
    { role: "CMO", name: "", email: "" },
    { role: "COO", name: "", email: "" },
  ]);
  const [inviteLoading, setInviteLoading] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteSuccess, setInviteSuccess] = useState(false);

  const updateInvite = (index: number, field: "name" | "email", value: string) => {
    setInvites((prev) => prev.map((row, i) => (i === index ? { ...row, [field]: value } : row)));
  };

  const sendInvites = async () => {
    setInviteLoading(true);
    setInviteError(null);
    try {
      const filled = invites.filter((r) => r.email.trim());
      for (const invite of filled) {
        await fetch("/api/v1/org/invite", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ role: invite.role.toLowerCase(), name: invite.name, email: invite.email }),
        });
      }
      setInviteSuccess(true);
      setStep(3);
    } catch (err: any) {
      setInviteError(err.message || "Failed to send invites");
    } finally {
      setInviteLoading(false);
    }
  };

  const finishOnboarding = async () => {
    try {
      await fetch("/api/v1/org/onboarding", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ complete: true }),
      });
    } catch {
      // best-effort
    }
    navigate("/dashboard");
  };

  const GETTING_STARTED_STEPS = [
    { icon: "1", title: "Explore your AI agents", desc: "35 pre-built agents are ready across Finance, HR, Marketing, Operations, and Back Office.", link: "/dashboard/agents" },
    { icon: "2", title: "Try the Playground", desc: "Run agents with sample data to see how they work — no configuration needed.", link: "/playground" },
    { icon: "3", title: "Set up Connectors", desc: "Connect your ERP, CRM, HRIS, and other tools to power your agents with real data.", link: "/dashboard/connectors" },
    { icon: "4", title: "Create Workflows", desc: "Chain agents together into automated workflows with human-in-the-loop approval gates.", link: "/dashboard/workflows" },
    { icon: "5", title: "Review Prompt Templates", desc: "Customize agent behavior using the prompt library — clone and edit to match your processes.", link: "/dashboard/prompt-templates" },
  ];

  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-4">
      <Helmet>
        <title>Onboarding — AgenticOrg</title>
      </Helmet>
      <div className="w-full max-w-lg">
        <div className="bg-card border border-border rounded-xl shadow-lg p-8">
          {/* Progress indicator */}
          <div className="flex items-center justify-center gap-2 mb-8">
            {[1, 2, 3, 4].map((s) => (
              <div
                key={s}
                className={`h-2 w-12 rounded-full transition-colors ${
                  s <= step ? "bg-primary" : "bg-muted"
                }`}
              />
            ))}
          </div>

          {/* Step 1: Welcome */}
          {step === 1 && (
            <div className="space-y-6">
              <div className="text-center">
                <h2 className="text-2xl font-bold">Welcome!</h2>
                <p className="text-muted-foreground mt-2">
                  Setting up <span className="font-semibold text-foreground">{user?.name || "your organization"}</span> on AgenticOrg
                </p>
              </div>
              <div>
                <label htmlFor="industry" className="block text-sm font-medium text-foreground mb-1.5">
                  Industry (optional)
                </label>
                <select
                  id="industry"
                  value={industry}
                  onChange={(e) => setIndustry(e.target.value)}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
                >
                  <option value="">Select industry...</option>
                  <option value="technology">Technology</option>
                  <option value="finance">Finance & Banking</option>
                  <option value="healthcare">Healthcare</option>
                  <option value="manufacturing">Manufacturing</option>
                  <option value="retail">Retail & E-commerce</option>
                  <option value="education">Education</option>
                  <option value="other">Other</option>
                </select>
              </div>
              <div>
                <label htmlFor="orgSize" className="block text-sm font-medium text-foreground mb-1.5">
                  Organization Size (optional)
                </label>
                <select
                  id="orgSize"
                  value={orgSize}
                  onChange={(e) => setOrgSize(e.target.value)}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
                >
                  <option value="">Select size...</option>
                  <option value="1-10">1-10 employees</option>
                  <option value="11-50">11-50 employees</option>
                  <option value="51-200">51-200 employees</option>
                  <option value="201-1000">201-1,000 employees</option>
                  <option value="1000+">1,000+ employees</option>
                </select>
              </div>
              <button
                onClick={() => setStep(2)}
                className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary/50 transition-colors"
              >
                Continue
              </button>
            </div>
          )}

          {/* Step 2: Invite your team */}
          {step === 2 && (
            <div className="space-y-6">
              <div className="text-center">
                <h2 className="text-2xl font-bold">Invite your team</h2>
                <p className="text-muted-foreground mt-2">
                  Add your C-suite members to collaborate on AgenticOrg
                </p>
              </div>

              {inviteError && (
                <div className="rounded-lg bg-destructive/10 border border-destructive/30 px-4 py-3 text-sm text-destructive">
                  {inviteError}
                </div>
              )}

              <div className="space-y-3">
                {invites.map((row, i) => (
                  <div key={row.role} className="flex items-center gap-2">
                    <span className="text-sm font-medium w-14 text-muted-foreground">{row.role}</span>
                    <input
                      type="text"
                      placeholder="Name"
                      value={row.name}
                      onChange={(e) => updateInvite(i, "name", e.target.value)}
                      className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
                    />
                    <input
                      type="email"
                      placeholder="Email"
                      value={row.email}
                      onChange={(e) => updateInvite(i, "email", e.target.value)}
                      className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
                    />
                  </div>
                ))}
              </div>

              <div className="flex gap-3">
                <button
                  onClick={() => setStep(3)}
                  className="flex-1 rounded-lg border border-border bg-background px-4 py-2.5 text-sm font-medium text-foreground hover:bg-muted transition-colors"
                >
                  Skip
                </button>
                <button
                  onClick={sendInvites}
                  disabled={inviteLoading || !invites.some((r) => r.email.trim())}
                  className="flex-1 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {inviteLoading ? "Sending..." : "Send Invites"}
                </button>
              </div>
            </div>
          )}

          {/* Step 3: Connect systems */}
          {step === 3 && (
            <div className="space-y-6">
              <div className="text-center">
                <h2 className="text-2xl font-bold">Connect your systems</h2>
                <p className="text-muted-foreground mt-2">
                  Integrate your existing tools and data sources to power your AI agents.
                </p>
              </div>

              {inviteSuccess && (
                <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-800">
                  Invitations sent successfully!
                </div>
              )}

              <div className="rounded-lg border border-border p-4 text-center">
                <p className="text-sm text-muted-foreground mb-3">
                  AgenticOrg supports 43+ connectors for ERP, CRM, HRIS, and more.
                </p>
                <button
                  onClick={() => navigate("/dashboard/connectors")}
                  className="rounded-lg border border-primary text-primary px-4 py-2 text-sm font-medium hover:bg-primary/10 transition-colors"
                >
                  Go to Connectors
                </button>
              </div>

              <div className="flex gap-3">
                <button
                  onClick={() => setStep(4)}
                  className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary/50 transition-colors"
                >
                  Continue
                </button>
              </div>
            </div>
          )}

          {/* Step 4: Getting Started Guide */}
          {step === 4 && (
            <div className="space-y-6">
              <div className="text-center">
                <h2 className="text-2xl font-bold">You're all set!</h2>
                <p className="text-muted-foreground mt-2">
                  Here's what you can do next to get the most out of AgenticOrg.
                </p>
              </div>

              <div className="space-y-3">
                {GETTING_STARTED_STEPS.map((item) => (
                  <button
                    key={item.icon}
                    onClick={() => navigate(item.link)}
                    className="w-full text-left flex items-start gap-3 p-3 rounded-lg border border-border hover:bg-muted/50 hover:border-primary/30 transition-colors"
                  >
                    <span className="flex-shrink-0 w-7 h-7 rounded-full bg-primary/10 text-primary flex items-center justify-center text-sm font-bold">
                      {item.icon}
                    </span>
                    <div>
                      <p className="text-sm font-medium text-foreground">{item.title}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">{item.desc}</p>
                    </div>
                  </button>
                ))}
              </div>

              <button
                onClick={finishOnboarding}
                className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary/50 transition-colors"
              >
                Go to Dashboard
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
