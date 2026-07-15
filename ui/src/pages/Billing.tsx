import { useState, useEffect } from "react";
import api from "@/lib/api";
import {
  formatPlanPrice,
  isPublicPlanCatalog,
  orderedPlans,
  type PublicPlan,
} from "@/lib/billingCatalog";

interface Usage {
  agent_runs: number;
  agent_count: number;
  storage_bytes: number;
}

interface Subscription {
  plan: string;
  tier: string;
  provider: string;
  order_id: string;
  is_paid: boolean;
}

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
  const [plans, setPlans] = useState<PublicPlan[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  // Currency switcher and INR (Pine Labs Plural) rail removed 2026-04-25 —
  // all customers (including India) checkout via Stripe in USD until we
  // wire the Plural integration properly. Backend /billing/subscribe/india
  // and the Plural client remain in place, just unreachable from the UI.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const currentPlan = subscription?.plan || "free";

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.get("/billing/plans").then((response) => response.data),
      api.get("/billing/subscription").then((r) => r.data).catch(() => null),
      api.get("/billing/usage").then((r) => r.data).catch(() => null),
    ])
      .then(([p, sub, u]) => {
        if (!isPublicPlanCatalog(p)) throw new Error("Incomplete billing catalog");
        setPlans(orderedPlans(p));
        setSubscription(sub);
        setUsage(u);
      })
      .catch(() => setError("Failed to load billing data"))
      .finally(() => setLoading(false));
  }, []);

  const handleSubscribe = async (plan: string) => {
    setActionLoading(plan);
    setError(null);
    try {
      const resp = await api.post("/billing/subscribe", { plan });
      const data = resp.data;
      const url = data.challenge_url || data.checkout_url;
      if (url) {
        window.location.href = url;
      } else if (data.updated) {
        const sub = await api.get("/billing/subscription").then((r) => r.data);
        setSubscription(sub);
      } else {
        setError("No payment URL returned");
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Failed to start payment");
    } finally {
      setActionLoading(null);
    }
  };

  const handleCancel = async () => {
    if (!confirm("Are you sure you want to cancel your subscription? You'll be downgraded to the Free plan.")) {
      return;
    }
    setActionLoading("cancel");
    setError(null);
    try {
      await api.post("/billing/cancel", { subscription_id: subscription?.order_id || "" });
      // Refresh subscription state
      const sub = await api.get("/billing/subscription").then((r) => r.data);
      setSubscription(sub);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Failed to cancel subscription");
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <p className="text-muted-foreground">Loading billing...</p>
      </div>
    );
  }

  const currentDefinition = plans.find((plan) => plan.plan_id === currentPlan);
  const limits = currentDefinition ? {
    runs: currentDefinition.limits.agent_runs ?? -1,
    agents: currentDefinition.limits.agent_count ?? -1,
    storage: currentDefinition.limits.storage_bytes ?? -1,
  } : null;
  const currentRank = currentDefinition?.display_order ?? 0;

  const getButtonLabel = (targetPlan: string): string => {
    const targetRank = plans.find((plan) => plan.plan_id === targetPlan)?.display_order ?? 0;
    if (targetRank > currentRank) return `Upgrade to ${targetPlan.charAt(0).toUpperCase() + targetPlan.slice(1)}`;
    if (targetRank < currentRank) return `Downgrade to ${targetPlan.charAt(0).toUpperCase() + targetPlan.slice(1)}`;
    return "Current plan";
  };

  return (
    <div className="max-w-5xl mx-auto space-y-8 p-3 md:p-6">
      <h1 className="text-2xl font-bold">Billing &amp; Usage</h1>

      {error && (
        <div className="rounded border border-destructive bg-destructive/10 p-3 text-sm text-destructive" role="alert">
          {error}
        </div>
      )}

      {/* Current plan + usage */}
      <section className="border rounded-lg p-6" data-testid="billing-usage">
        <div className="flex flex-col items-start justify-between gap-3 mb-4 md:flex-row md:items-center">
          <div>
            <h2 className="text-lg font-semibold">
              Current Plan:{" "}
              <span className="capitalize text-primary">{currentPlan}</span>
              {subscription?.is_paid && (
                <span className="ml-2 inline-block px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-medium">
                  Active
                </span>
              )}
            </h2>
            {subscription?.provider && (
              <p className="text-xs text-muted-foreground mt-1">
                via {subscription.provider === "plural" ? "PineLabs Plural (INR)" : "Stripe (USD)"}
                {subscription.order_id && ` — ${subscription.order_id}`}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {subscription?.is_paid && (
              <button
                onClick={handleCancel}
                disabled={actionLoading === "cancel"}
                className="px-3 py-1 rounded border border-destructive text-destructive text-sm hover:bg-destructive/10 disabled:opacity-50"
              >
                {actionLoading === "cancel" ? "Cancelling..." : "Cancel plan"}
              </button>
            )}
          </div>
        </div>
        {usage && limits && (
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
          {plans.map((p) => {
            const isCurrent = p.plan_id === currentPlan;
            const targetRank = p.display_order;
            const isUpgrade = targetRank > currentRank;
            const usdPrice = p.prices.find((price) => price.currency === "USD");

            return (
              <div
                key={p.plan_id}
                className={`border rounded-lg p-5 flex flex-col ${
                  isCurrent ? "border-primary ring-2 ring-primary/20" : ""
                }`}
                data-testid={`plan-${p.plan_id}`}
              >
                <h3 className="text-lg font-bold mb-1">{p.display_name}</h3>
                <p className="text-2xl font-semibold mb-3">
                  {usdPrice ? formatPlanPrice(usdPrice) : "No USD price in catalog"}
                </p>

                {/* Adjustment note for upgrades/downgrades */}
                {!isCurrent && p.checkout_mode === "hosted" && subscription?.is_paid && (
                  <p className="text-xs text-muted-foreground mb-2 bg-muted rounded px-2 py-1">
                    {isUpgrade
                      ? "Stripe applies prorated charges according to your billing settings."
                      : "Stripe applies prorated credits according to your billing settings."}
                  </p>
                )}

                <ul className="text-sm space-y-1 flex-1 mb-4">
                  {[
                    `Agents: ${p.limits.agent_count ?? "No finite catalog cap"}`,
                    `Runs per ${p.limits.agent_runs_interval}: ${p.limits.agent_runs ?? "No finite catalog cap"}`,
                    `Storage bytes: ${p.limits.storage_bytes ?? "No finite catalog cap"}`,
                    `Checkout mode: ${p.checkout_mode}`,
                  ].map((fact) => (
                    <li key={fact} className="flex items-start gap-1.5">
                      <span className="text-green-500 mt-0.5">&#10003;</span>
                      {fact}
                    </li>
                  ))}
                </ul>

                {isCurrent ? (
                  <span className="text-center text-sm font-medium text-primary py-2">
                    Current plan
                  </span>
                ) : p.checkout_mode === "none" ? (
                  subscription?.is_paid ? (
                    <button
                      onClick={handleCancel}
                      disabled={actionLoading === "cancel"}
                      className="w-full py-2 rounded border border-destructive text-destructive text-sm font-medium hover:bg-destructive/10 disabled:opacity-50"
                    >
                      Downgrade to Free
                    </button>
                  ) : null
                ) : (
                  <button
                    onClick={() => handleSubscribe(p.plan_id)}
                    disabled={actionLoading === p.plan_id}
                    className={`w-full py-2 rounded text-sm font-medium disabled:opacity-50 ${
                      isUpgrade
                        ? "bg-primary text-primary-foreground hover:opacity-90"
                        : "border border-muted-foreground text-muted-foreground hover:bg-muted"
                    }`}
                  >
                    {actionLoading === p.plan_id ? "Processing..." : getButtonLabel(p.plan_id)}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
