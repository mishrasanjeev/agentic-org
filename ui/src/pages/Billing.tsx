import { useState, useEffect } from "react";

interface Plan {
  plan: string;
  label: string;
  price_usd: number;
  price_inr: number;
  agents: number | string;
  runs: string;
  storage: string;
  features: string[];
}

interface Usage {
  agent_runs: number;
  agent_count: number;
  storage_bytes: number;
}

interface Invoice {
  id: string;
  date: string;
  amount: number;
  currency: string;
  status: string;
  plan: string;
}

const API = import.meta.env.VITE_API_URL ?? "";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function ProgressBar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const isWarning = pct >= 80 && pct < 100;
  const isBlocked = pct >= 100;
  const color = isBlocked ? "bg-red-500" : isWarning ? "bg-yellow-500" : "bg-blue-500";

  return (
    <div className="mb-3">
      <div className="flex justify-between text-sm mb-1">
        <span>{label}</span>
        <span className={isBlocked ? "text-red-600 font-semibold" : ""}>
          {value.toLocaleString()} / {max < 0 ? "Unlimited" : max.toLocaleString()}
        </span>
      </div>
      {max > 0 && (
        <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

export default function Billing() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [currency, setCurrency] = useState<"usd" | "inr">("usd");
  const [currentPlan] = useState("free");
  const [loading, setLoading] = useState(true);

  const tenantId = localStorage.getItem("tenant_id") || "demo";

  useEffect(() => {
    const headers: Record<string, string> = {};
    const token = localStorage.getItem("token");
    if (token) headers["Authorization"] = `Bearer ${token}`;

    Promise.all([
      fetch(`${API}/api/v1/billing/plans`, { headers }).then((r) => r.json()),
      fetch(`${API}/api/v1/billing/usage?tenant_id=${tenantId}`, { headers }).then((r) => r.json()),
      fetch(`${API}/api/v1/billing/invoices?tenant_id=${tenantId}`, { headers }).then((r) => r.json()),
    ])
      .then(([p, u, inv]) => {
        setPlans(Array.isArray(p) ? p : []);
        setUsage(u);
        setInvoices(Array.isArray(inv) ? inv : []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [tenantId]);

  const handleSubscribe = async (plan: string) => {
    const token = localStorage.getItem("token");
    const endpoint = currency === "inr" ? "/api/v1/billing/subscribe/india" : "/api/v1/billing/subscribe";
    const body =
      currency === "inr"
        ? { tenant_id: tenantId, plan }
        : {
            tenant_id: tenantId,
            plan,
            success_url: `${window.location.origin}/dashboard/billing?success=1`,
            cancel_url: `${window.location.origin}/dashboard/billing?cancelled=1`,
          };

    try {
      const resp = await fetch(`${API}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      const url = data.checkout_url || data.payment_url;
      if (url) window.location.href = url;
    } catch {
      // handled silently
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <p className="text-muted-foreground">Loading billing...</p>
      </div>
    );
  }

  // Tier limits for progress bars
  const tierLimits: Record<string, { runs: number; agents: number; storage: number }> = {
    free: { runs: 1000, agents: 3, storage: 1073741824 },
    pro: { runs: 10000, agents: 15, storage: 53687091200 },
    enterprise: { runs: -1, agents: -1, storage: -1 },
  };
  const limits = tierLimits[currentPlan] || tierLimits.free;

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      <h1 className="text-2xl font-bold">Billing &amp; Usage</h1>

      {/* Current plan + usage */}
      <section className="border rounded-lg p-6" data-testid="billing-usage">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">
              Current Plan: <span className="capitalize">{currentPlan}</span>
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Currency:</span>
            <button
              onClick={() => setCurrency(currency === "usd" ? "inr" : "usd")}
              className="px-3 py-1 rounded border text-sm hover:bg-muted"
              data-testid="currency-toggle"
            >
              {currency === "usd" ? "USD $" : "INR \u20B9"}
            </button>
          </div>
        </div>
        {usage && (
          <div>
            <ProgressBar value={usage.agent_runs} max={limits.runs} label="Agent Runs (this month)" />
            <ProgressBar value={usage.agent_count} max={limits.agents} label="Active Agents" />
            <ProgressBar
              value={usage.storage_bytes}
              max={limits.storage}
              label={`Storage (${formatBytes(usage.storage_bytes)})`}
            />
          </div>
        )}
      </section>

      {/* Plan cards */}
      <section>
        <h2 className="text-lg font-semibold mb-4">Available Plans</h2>
        <div className="grid md:grid-cols-3 gap-4">
          {plans.map((p) => (
            <div
              key={p.plan}
              className={`border rounded-lg p-5 flex flex-col ${
                p.plan === currentPlan ? "border-primary ring-2 ring-primary/20" : ""
              }`}
              data-testid={`plan-${p.plan}`}
            >
              <h3 className="text-lg font-bold mb-1">{p.label}</h3>
              <p className="text-2xl font-semibold mb-3">
                {currency === "usd"
                  ? p.price_usd === 0
                    ? "Free"
                    : `$${p.price_usd}/mo`
                  : p.price_inr === 0
                    ? "Free"
                    : `\u20B9${p.price_inr.toLocaleString("en-IN")}/mo`}
              </p>
              <ul className="text-sm space-y-1 flex-1 mb-4">
                {p.features.map((f, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span className="text-green-500 mt-0.5">&#10003;</span>
                    {f}
                  </li>
                ))}
              </ul>
              {p.plan !== "free" && p.plan !== currentPlan && (
                <button
                  onClick={() => handleSubscribe(p.plan)}
                  className="w-full py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
                >
                  {currentPlan === "free" ? "Upgrade" : "Switch"} to {p.label}
                </button>
              )}
              {p.plan === currentPlan && (
                <span className="text-center text-sm text-muted-foreground">Current plan</span>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Invoice history */}
      <section>
        <h2 className="text-lg font-semibold mb-4">Invoice History</h2>
        {invoices.length === 0 ? (
          <p className="text-muted-foreground text-sm">No invoices yet.</p>
        ) : (
          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-sm" data-testid="invoice-table">
              <thead className="bg-muted/50">
                <tr>
                  <th className="text-left px-4 py-2">Date</th>
                  <th className="text-left px-4 py-2">Plan</th>
                  <th className="text-left px-4 py-2">Amount</th>
                  <th className="text-left px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((inv) => (
                  <tr key={inv.id} className="border-t">
                    <td className="px-4 py-2">{inv.date}</td>
                    <td className="px-4 py-2 capitalize">{inv.plan}</td>
                    <td className="px-4 py-2">
                      {inv.currency === "inr"
                        ? `\u20B9${(inv.amount / 100).toLocaleString("en-IN")}`
                        : `$${(inv.amount / 100).toFixed(2)}`}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                          inv.status === "paid"
                            ? "bg-green-100 text-green-700"
                            : "bg-yellow-100 text-yellow-700"
                        }`}
                      >
                        {inv.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
