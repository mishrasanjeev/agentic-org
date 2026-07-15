import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  formatPlanPrice,
  isPublicPlanCatalog,
  orderedPlans,
  type PublicPlan,
  type PublicPlanCatalog,
} from "@/lib/billingCatalog";
import api from "@/lib/api";

function CheckIcon() {
  return (
    <svg className="h-5 w-5 flex-shrink-0 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  );
}

function DemoModal({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ name: "", email: "", company: "", role: "" });
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const response = await fetch("/api/v1/demo-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!response.ok) throw new Error("Request failed");
      setDone(true);
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const fieldClass =
    "w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20";

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/70 px-4 backdrop-blur-sm"
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <div className="relative w-full max-w-md rounded-2xl bg-white p-8 shadow-2xl">
        <button type="button" onClick={onClose} className="absolute right-4 top-4 text-slate-400 hover:text-slate-600" aria-label="Close">
          <span aria-hidden="true">×</span>
        </button>
        {done ? (
          <div className="py-8 text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-emerald-100">
              <CheckIcon />
            </div>
            <h3 className="mb-2 text-xl font-bold text-slate-900">Thanks!</h3>
            <p className="text-slate-600">Your request was received. We will contact you to discuss evaluation scope and next steps.</p>
            <button type="button" onClick={onClose} className="mt-6 rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-medium text-white hover:bg-blue-700">
              Close
            </button>
          </div>
        ) : (
          <>
            <h3 className="mb-1 text-xl font-bold text-slate-900">Book a Demo</h3>
            <p className="mb-6 text-sm text-slate-500">Review AgenticOrg for your organization.</p>
            <form onSubmit={handleSubmit} className="space-y-4">
              <input required type="text" placeholder="Your name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} className={fieldClass} />
              <input required type="email" placeholder="Work email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} className={fieldClass} />
              <input type="text" placeholder="Company" value={form.company} onChange={(event) => setForm({ ...form, company: event.target.value })} className={fieldClass} />
              <select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })} className={fieldClass}>
                <option value="">Select your role</option>
                {['CEO', 'CFO', 'CHRO', 'CMO', 'COO', 'CTO', 'Other'].map((role) => <option key={role} value={role}>{role}</option>)}
              </select>
              {error && <p className="text-sm text-red-600">{error}</p>}
              <button type="submit" disabled={submitting} className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
                {submitting ? "Sending..." : "Request Demo"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}

function formatLimit(value: number | null): string {
  return value === null ? "No finite catalog cap" : value.toLocaleString("en-US");
}

function formatStorage(value: number | null): string {
  if (value === null) return "No finite catalog cap";
  return `${(value / (1024 * 1024 * 1024)).toLocaleString("en-US")} GB`;
}

function planFacts(plan: PublicPlan): string[] {
  return [
    `Agents: ${formatLimit(plan.limits.agent_count)}`,
    `Agent runs per ${plan.limits.agent_runs_interval}: ${formatLimit(plan.limits.agent_runs)}`,
    `Storage: ${formatStorage(plan.limits.storage_bytes)}`,
    `Account signup: ${plan.signup_available ? "Available" : "Not offered by this catalog"}`,
    `Checkout mode: ${plan.checkout_mode}`,
  ];
}

const FAQS = [
  {
    question: "Where do these plan facts come from?",
    answer: "The page validates and renders the complete versioned response from the public billing catalog. It does not maintain a second set of prices or plan limits.",
  },
  {
    question: "What happens when I reach a plan limit?",
    answer: "The catalog publishes offer limits, but this page does not claim that every runtime enforcement hook is integrated. Confirm enforcement readiness before relying on a limit.",
  },
  {
    question: "Are connectors included with a plan?",
    answer: "The catalog does not grant connector readiness. Each connector still requires tenant configuration, authentication, compatibility checks, and release evidence.",
  },
  {
    question: "Are annual discounts or service levels included?",
    answer: "No annual discount, support level, or service-level commitment is asserted by this page. Confirm applicable terms in a verified checkout or signed agreement.",
  },
];

function FaqItem({ question, answer }: { question: string; answer: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-slate-200 last:border-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between py-5 text-left"
      >
        <span className="text-base font-medium text-slate-900">{question}</span>
        <span className={`text-slate-500 transition-transform ${open ? "rotate-45" : ""}`} aria-hidden="true">+</span>
      </button>
      {open && <p className="pb-5 text-sm leading-relaxed text-slate-600">{answer}</p>}
    </div>
  );
}

export default function Pricing() {
  const [showDemo, setShowDemo] = useState(false);
  const [catalog, setCatalog] = useState<PublicPlanCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let mounted = true;
    api.get<unknown>("/billing/plans")
      .then((response) => {
        if (!mounted) return;
        if (isPublicPlanCatalog(response.data)) {
          setCatalog(response.data);
          setFailed(false);
        } else {
          setCatalog(null);
          setFailed(true);
        }
      })
      .catch(() => {
        if (mounted) {
          setCatalog(null);
          setFailed(true);
        }
      })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, []);

  const plans = catalog ? orderedPlans(catalog) : [];
  const comparisonRows = catalog ? [
    { label: "Prices", values: plans.map((plan) => plan.prices.map(formatPlanPrice).join(" · ")) },
    { label: "Agents", values: plans.map((plan) => formatLimit(plan.limits.agent_count)) },
    { label: "Agent runs", values: plans.map((plan) => `${formatLimit(plan.limits.agent_runs)} / ${plan.limits.agent_runs_interval}`) },
    { label: "Storage", values: plans.map((plan) => formatStorage(plan.limits.storage_bytes)) },
    { label: "Account signup", values: plans.map((plan) => plan.signup_available ? "Available" : "Not offered") },
    { label: "Checkout mode", values: plans.map((plan) => plan.checkout_mode) },
  ] : [];

  return (
    <div className="min-h-screen bg-white">
      {showDemo && <DemoModal onClose={() => setShowDemo(false)} />}

      <nav className="fixed inset-x-0 top-0 z-50 border-b border-slate-700/50 bg-slate-900/95 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <Link to="/" className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 text-sm font-bold text-white">AO</div>
            <span className="text-lg font-semibold text-white">AgenticOrg</span>
          </Link>
          <div className="hidden items-center gap-8 md:flex">
            <Link to="/" className="text-sm text-slate-300 hover:text-white">Home</Link>
            <Link to="/pricing" className="text-sm font-medium text-white">Pricing</Link>
            <Link to="/evals" className="text-sm text-slate-300 hover:text-white">Evals</Link>
          </div>
          <div className="flex items-center gap-3">
            <Link to="/login" className="hidden rounded-lg border border-slate-500 px-4 py-2 text-sm font-medium text-slate-300 hover:border-white hover:text-white sm:inline-flex">
              Sign In
            </Link>
            <button type="button" onClick={() => setShowDemo(true)} className="rounded-lg bg-gradient-to-r from-blue-500 to-cyan-500 px-5 py-2 text-sm font-medium text-white hover:from-blue-600 hover:to-cyan-600">
              Book a Demo
            </button>
          </div>
        </div>
      </nav>

      <section className="bg-gradient-to-b from-slate-900 via-slate-800 to-white px-4 pb-20 pt-32">
        <div className="mx-auto max-w-4xl text-center">
          <p className="mb-4 text-xs font-semibold uppercase tracking-[0.3em] text-cyan-300">Versioned offer catalog</p>
          <h1 className="mb-5 text-4xl font-extrabold tracking-tight text-white sm:text-5xl">Plan facts from one source</h1>
          <p className="mx-auto max-w-2xl text-lg text-slate-300">
            This page renders the complete response from the public billing catalog. Runtime enforcement, connector readiness, and additional terms are verified separately.
          </p>
        </div>
      </section>

      <main>
        <section className="-mt-6 px-4 pb-20">
          {(loading || failed) && (
            <p className="mx-auto mb-8 max-w-3xl rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 text-center text-sm text-amber-900" role="status">
              {loading
                ? "Loading the complete billing catalog..."
                : "The complete billing catalog is unavailable. No plan offer is displayed; query the service again before relying on commercial terms."}
            </p>
          )}
          {!loading && catalog && (
            <div className="mx-auto grid max-w-6xl grid-cols-1 gap-8 md:grid-cols-3">
              {plans.map((plan) => (
                <article key={plan.plan_id} className="flex flex-col rounded-2xl border border-slate-200 bg-white p-8 shadow-sm transition-shadow hover:shadow-lg" data-testid="plan-card">
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-400">{plan.plan_id}</p>
                  <h2 className="mb-5 text-2xl font-bold text-slate-900">{plan.display_name}</h2>
                  <div className="mb-7 space-y-1 border-b border-slate-100 pb-6">
                    {plan.prices.map((price) => <p key={price.currency} className="text-2xl font-extrabold text-slate-900">{formatPlanPrice(price)}</p>)}
                  </div>
                  <ul className="flex-1 space-y-3">
                    {planFacts(plan).map((fact) => (
                      <li key={fact} className="flex items-start gap-3"><CheckIcon /><span className="text-sm text-slate-700">{fact}</span></li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          )}
        </section>

        {catalog && (
          <section className="bg-slate-50 px-4 py-20">
            <div className="mx-auto max-w-6xl">
              <div className="mb-10 text-center">
                <h2 className="text-3xl font-bold text-slate-900">Catalog facts by plan</h2>
                <p className="mt-3 text-sm text-slate-500">Catalog version {catalog.catalog_version}</p>
              </div>
              <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
                <table className="w-full border-collapse">
                  <thead><tr className="border-b-2 border-slate-200">
                    <th className="px-5 py-4 text-left text-sm font-semibold text-slate-600">Fact</th>
                    {plans.map((plan) => <th key={plan.plan_id} className="px-5 py-4 text-center text-sm font-semibold text-slate-600">{plan.display_name}</th>)}
                  </tr></thead>
                  <tbody>{comparisonRows.map((row) => (
                    <tr key={row.label} className="border-b border-slate-100 last:border-0">
                      <td className="px-5 py-4 text-sm font-medium text-slate-700">{row.label}</td>
                      {row.values.map((value, index) => <td key={plans[index].plan_id} className="px-5 py-4 text-center text-sm text-slate-700">{value}</td>)}
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          </section>
        )}

        <section className="px-4 py-20">
          <div className="mx-auto max-w-3xl">
            <h2 className="mb-12 text-center text-3xl font-bold text-slate-900">Commercial truth boundaries</h2>
            <div className="border-t border-slate-200">{FAQS.map((faq) => <FaqItem key={faq.question} {...faq} />)}</div>
          </div>
        </section>

        <section className="bg-gradient-to-r from-slate-900 to-slate-800 px-4 py-20">
          <div className="mx-auto max-w-4xl text-center">
            <h2 className="mb-4 text-3xl font-bold text-white">Evaluate against your requirements</h2>
            <p className="mx-auto mb-8 max-w-xl text-slate-300">Use the catalog as the offer source, then verify runtime readiness and applicable terms before making a purchase decision.</p>
            <div className="flex flex-col items-center justify-center gap-4 sm:flex-row">
              {catalog && plans.some((plan) => plan.signup_available) && (
                <Link to="/signup" className="rounded-lg bg-white px-8 py-3 text-sm font-semibold text-slate-900 hover:bg-slate-100">
                  Create an Account
                </Link>
              )}
              <button type="button" onClick={() => setShowDemo(true)} className="rounded-lg bg-gradient-to-r from-blue-500 to-cyan-500 px-8 py-3 text-sm font-semibold text-white hover:from-blue-600 hover:to-cyan-600">Book a Demo</button>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-800 bg-slate-900 px-4 py-10">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 md:flex-row">
          <span className="text-sm text-slate-400">AgenticOrg</span>
          <div className="flex items-center gap-6"><Link to="/" className="text-sm text-slate-400 hover:text-white">Home</Link><Link to="/evals" className="text-sm text-slate-400 hover:text-white">Evals</Link><Link to="/login" className="text-sm text-slate-400 hover:text-white">Sign In</Link></div>
          <p className="text-xs text-slate-500">&copy; {new Date().getFullYear()} AgenticOrg.</p>
        </div>
      </footer>
    </div>
  );
}
