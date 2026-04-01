import { useState } from "react";
import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";

/* ------------------------------------------------------------------ */
/*  CheckIcon                                                          */
/* ------------------------------------------------------------------ */
function CheckIcon({ className = "w-5 h-5 text-emerald-500" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  );
}

function XIcon({ className = "w-5 h-5 text-slate-300" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  DemoModal                                                          */
/* ------------------------------------------------------------------ */
function DemoModal({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ name: "", email: "", company: "", role: "" });
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const res = await fetch("/api/v1/demo-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!res.ok) throw new Error("Request failed");
      setDone(true);
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const fieldClass =
    "w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 outline-none transition-all";

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="relative w-full max-w-md rounded-2xl bg-white shadow-2xl p-8">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-slate-600 transition-colors"
          aria-label="Close"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        {done ? (
          <div className="text-center py-8">
            <div className="w-16 h-16 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-4">
              <CheckIcon className="w-8 h-8 text-emerald-600" />
            </div>
            <h3 className="text-xl font-bold text-slate-900 mb-2">Thanks!</h3>
            <p className="text-slate-600">We will contact you within 24 hours.</p>
            <button
              onClick={onClose}
              className="mt-6 bg-gradient-to-r from-blue-500 to-violet-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-violet-700 transition-all"
            >
              Close
            </button>
          </div>
        ) : (
          <>
            <h3 className="text-xl font-bold text-slate-900 mb-1">Book a Demo</h3>
            <p className="text-sm text-slate-500 mb-6">See AgenticOrg in action for your organization.</p>
            <form onSubmit={handleSubmit} className="space-y-4">
              <input required type="text" placeholder="Your name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={fieldClass} />
              <input required type="email" placeholder="Work email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={fieldClass} />
              <input type="text" placeholder="Company" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className={fieldClass} />
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className={fieldClass}>
                <option value="">Select your role</option>
                <option value="CEO">CEO</option>
                <option value="CFO">CFO</option>
                <option value="CHRO">CHRO</option>
                <option value="CMO">CMO</option>
                <option value="COO">COO</option>
                <option value="CTO">CTO</option>
                <option value="Other">Other</option>
              </select>
              {error && <p className="text-sm text-red-600">{error}</p>}
              <button
                type="submit"
                disabled={submitting}
                className="w-full bg-gradient-to-r from-blue-500 to-violet-600 text-white py-2.5 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-violet-700 transition-all disabled:opacity-50"
              >
                {submitting ? "Sending..." : "Request Demo"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tier data                                                          */
/* ------------------------------------------------------------------ */
const TIERS = [
  {
    name: "Free",
    price: "$0",
    period: "/month",
    description: "Get started with core AI agents and see results immediately.",
    highlight: false,
    cta: "Start Free",
    ctaLink: "/login",
    features: [
      "35 AI agents",
      "20 connectors",
      "500 tasks/day",
      "Community support",
      "Shadow mode testing",
      "Basic audit log",
      "Single workspace",
    ],
  },
  {
    name: "Pro",
    price: "$499",
    period: "/month",
    description: "Scale your operations with advanced agents and priority support.",
    highlight: true,
    cta: "Start Pro",
    ctaLink: "demo",
    features: [
      "Unlimited AI agents",
      "43 connectors",
      "Unlimited tasks",
      "Email support",
      "Custom workflows",
      "Priority HITL queue",
      "Advanced analytics",
      "API access",
      "Team workspaces",
    ],
  },
  {
    name: "Enterprise",
    price: "Custom",
    period: "",
    description: "Full platform with dedicated support, SLA, and custom deployments.",
    highlight: false,
    cta: "Contact Sales",
    ctaLink: "demo",
    features: [
      "Unlimited AI agents",
      "43 connectors",
      "Unlimited everything",
      "Dedicated support",
      "99.9% SLA guarantee",
      "Custom connectors",
      "On-premise option",
      "SSO / SAML",
      "Custom integrations",
      "Dedicated CSM",
    ],
  },
];

/* ------------------------------------------------------------------ */
/*  Feature comparison table data                                      */
/* ------------------------------------------------------------------ */
interface ComparisonRow {
  feature: string;
  free: string | boolean;
  pro: string | boolean;
  enterprise: string | boolean;
}

const COMPARISON: ComparisonRow[] = [
  { feature: "AI Agents", free: "35", pro: "Unlimited", enterprise: "Unlimited" },
  { feature: "Connectors", free: "20", pro: "43", enterprise: "43" },
  { feature: "Tasks per day", free: "500", pro: "Unlimited", enterprise: "Unlimited" },
  { feature: "Shadow mode", free: true, pro: true, enterprise: true },
  { feature: "Custom workflows", free: false, pro: true, enterprise: true },
  { feature: "Human-in-the-Loop (HITL)", free: "Basic", pro: "Priority", enterprise: "Dedicated" },
  { feature: "Audit log", free: "7 days", pro: "90 days", enterprise: "Unlimited" },
  { feature: "API access", free: false, pro: true, enterprise: true },
  { feature: "Analytics & Observatory", free: "Basic", pro: "Advanced", enterprise: "Full" },
  { feature: "SSO / SAML", free: false, pro: false, enterprise: true },
  { feature: "Custom connectors", free: false, pro: false, enterprise: true },
  { feature: "On-premise deployment", free: false, pro: false, enterprise: true },
  { feature: "SLA guarantee", free: false, pro: false, enterprise: "99.9%" },
  { feature: "Support", free: "Community", pro: "Email", enterprise: "Dedicated" },
  { feature: "Dedicated CSM", free: false, pro: false, enterprise: true },
];

/* ------------------------------------------------------------------ */
/*  FAQ data                                                           */
/* ------------------------------------------------------------------ */
const FAQS = [
  {
    q: "Can I switch plans at any time?",
    a: "Yes. You can upgrade or downgrade your plan at any time. When upgrading, you get immediate access to the new features. When downgrading, the change takes effect at the next billing cycle.",
  },
  {
    q: "What happens when I hit the task limit on Free?",
    a: "Tasks beyond the daily limit are queued and processed the next day. You will receive a notification suggesting an upgrade. No data is lost.",
  },
  {
    q: "Do you offer annual billing?",
    a: "Yes. Annual plans receive a 20% discount. Contact our sales team for details on annual Enterprise agreements.",
  },
  {
    q: "What connectors are included?",
    a: "Free includes 20 core connectors (Oracle, SAP, Salesforce, Slack, GSTN, and more). Pro and Enterprise include all 43 connectors (Darwinbox, Stripe, HubSpot, EPFO, Jira, and more). Enterprise adds custom integrations.",
  },
  {
    q: "Is my data secure?",
    a: "All plans include encryption at rest and in transit, tenant isolation, and HMAC-signed audit logs. Enterprise adds on-premise deployment, SSO/SAML, and a dedicated security review.",
  },
  {
    q: "Can I try Pro features before committing?",
    a: "Yes. We offer a 14-day free trial of Pro. Book a demo and we will set it up for your team.",
  },
];

/* ------------------------------------------------------------------ */
/*  FAQ Accordion Item                                                 */
/* ------------------------------------------------------------------ */
function FaqItem({ q, a }: { q: string; a: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-slate-200 last:border-0">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between py-5 text-left"
      >
        <span className="text-base font-medium text-slate-900">{q}</span>
        <svg
          className={`w-5 h-5 text-slate-500 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <p className="pb-5 text-sm text-slate-600 leading-relaxed">{a}</p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  CellValue — renders check/x/text in the comparison table           */
/* ------------------------------------------------------------------ */
function CellValue({ value }: { value: string | boolean }) {
  if (value === true) return <CheckIcon />;
  if (value === false) return <XIcon />;
  return <span className="text-sm text-slate-700">{value}</span>;
}

/* ================================================================== */
/*  Pricing Page                                                       */
/* ================================================================== */
export default function Pricing() {
  const [showDemo, setShowDemo] = useState(false);

  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Pricing | AgenticOrg</title>
        <meta name="description" content="Simple, transparent pricing for AgenticOrg. Start free with 35 agents, scale with Pro, go all-in with Enterprise." />
        <link rel="canonical" href="https://agenticorg.ai/pricing" />
      </Helmet>

      {/* Demo modal */}
      {showDemo && <DemoModal onClose={() => setShowDemo(false)} />}

      {/* ============================================================ */}
      {/* NAVBAR                                                        */}
      {/* ============================================================ */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-slate-900/95 backdrop-blur-md border-b border-slate-700/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm">
              AO
            </div>
            <span className="text-white font-semibold text-lg">AgenticOrg</span>
          </Link>

          <div className="hidden md:flex items-center gap-8">
            <Link to="/" className="text-slate-300 hover:text-white text-sm transition-colors">Home</Link>
            <Link to="/pricing" className="text-white text-sm font-medium">Pricing</Link>
            <Link to="/evals" className="text-slate-300 hover:text-white text-sm transition-colors">Evals</Link>
          </div>

          <div className="flex items-center gap-3">
            <Link
              to="/login"
              className="hidden sm:inline-flex border border-slate-500 text-slate-300 hover:text-white hover:border-white px-4 py-2 rounded-lg text-sm font-medium transition-all"
            >
              Sign In
            </Link>
            <button
              onClick={() => setShowDemo(true)}
              className="bg-gradient-to-r from-blue-500 to-violet-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25"
            >
              Book a Demo
            </button>
          </div>
        </div>
      </nav>

      {/* ============================================================ */}
      {/* HERO                                                          */}
      {/* ============================================================ */}
      <section className="pt-32 pb-16 px-4 bg-gradient-to-b from-slate-900 via-slate-800 to-white">
        <div className="max-w-4xl mx-auto text-center">
          <h1 className="text-4xl sm:text-5xl font-extrabold text-white mb-4 tracking-tight">
            Simple, transparent pricing
          </h1>
          <p className="text-lg text-slate-300 max-w-2xl mx-auto">
            Start free with 35 agents. Scale to unlimited agents and 43 connectors as your team grows.
            No hidden fees. Cancel anytime.
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/* PRICING CARDS                                                 */}
      {/* ============================================================ */}
      <section className="pb-20 px-4 -mt-4">
        <div className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-8">
          {TIERS.map((tier) => (
            <div
              key={tier.name}
              className={`relative rounded-2xl border p-8 flex flex-col ${
                tier.highlight
                  ? "border-blue-500 shadow-xl shadow-blue-500/10 ring-2 ring-blue-500/20 bg-white scale-[1.02]"
                  : "border-slate-200 bg-white shadow-sm hover:shadow-md transition-shadow"
              }`}
            >
              {/* Popular badge */}
              {tier.highlight && (
                <div className="absolute -top-4 left-1/2 -translate-x-1/2">
                  <span className="bg-gradient-to-r from-blue-500 to-violet-600 text-white text-xs font-bold px-4 py-1.5 rounded-full uppercase tracking-wider">
                    Most Popular
                  </span>
                </div>
              )}

              <div className="mb-6">
                <h3 className="text-lg font-semibold text-slate-900 mb-1">{tier.name}</h3>
                <div className="flex items-baseline gap-1">
                  <span className="text-4xl font-extrabold text-slate-900">{tier.price}</span>
                  {tier.period && <span className="text-slate-500 text-sm">{tier.period}</span>}
                </div>
                <p className="text-sm text-slate-500 mt-2">{tier.description}</p>
              </div>

              {/* CTA */}
              {tier.ctaLink === "demo" ? (
                <button
                  onClick={() => setShowDemo(true)}
                  className={`w-full py-3 rounded-lg text-sm font-semibold transition-all mb-8 ${
                    tier.highlight
                      ? "bg-gradient-to-r from-blue-500 to-violet-600 text-white hover:from-blue-600 hover:to-violet-700 shadow-lg shadow-blue-500/25"
                      : "bg-slate-900 text-white hover:bg-slate-800"
                  }`}
                >
                  {tier.cta}
                </button>
              ) : (
                <Link
                  to={tier.ctaLink}
                  className={`w-full py-3 rounded-lg text-sm font-semibold transition-all mb-8 block text-center ${
                    tier.highlight
                      ? "bg-gradient-to-r from-blue-500 to-violet-600 text-white hover:from-blue-600 hover:to-violet-700 shadow-lg shadow-blue-500/25"
                      : "bg-slate-900 text-white hover:bg-slate-800"
                  }`}
                >
                  {tier.cta}
                </Link>
              )}

              {/* Feature list */}
              <ul className="space-y-3 flex-1">
                {tier.features.map((f) => (
                  <li key={f} className="flex items-start gap-3">
                    <CheckIcon className="w-5 h-5 text-emerald-500 flex-shrink-0 mt-0.5" />
                    <span className="text-sm text-slate-700">{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      {/* ============================================================ */}
      {/* FEATURE COMPARISON TABLE                                      */}
      {/* ============================================================ */}
      <section className="py-20 px-4 bg-slate-50">
        <div className="max-w-5xl mx-auto">
          <h2 className="text-3xl font-bold text-slate-900 text-center mb-12">
            Compare plans in detail
          </h2>

          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b-2 border-slate-200">
                  <th className="text-left py-4 pr-4 text-sm font-semibold text-slate-600 w-1/3">Feature</th>
                  <th className="text-center py-4 px-4 text-sm font-semibold text-slate-600">Free</th>
                  <th className="text-center py-4 px-4 text-sm font-semibold text-blue-600 bg-blue-50/50 rounded-t-lg">Pro</th>
                  <th className="text-center py-4 px-4 text-sm font-semibold text-slate-600">Enterprise</th>
                </tr>
              </thead>
              <tbody>
                {COMPARISON.map((row) => (
                  <tr key={row.feature} className="border-b border-slate-100 hover:bg-slate-50/80">
                    <td className="py-3.5 pr-4 text-sm text-slate-700 font-medium">{row.feature}</td>
                    <td className="py-3.5 px-4 text-center">
                      <div className="flex justify-center"><CellValue value={row.free} /></div>
                    </td>
                    <td className="py-3.5 px-4 text-center bg-blue-50/30">
                      <div className="flex justify-center"><CellValue value={row.pro} /></div>
                    </td>
                    <td className="py-3.5 px-4 text-center">
                      <div className="flex justify-center"><CellValue value={row.enterprise} /></div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* FAQ                                                           */}
      {/* ============================================================ */}
      <section className="py-20 px-4">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-3xl font-bold text-slate-900 text-center mb-12">
            Frequently asked questions
          </h2>
          <div className="divide-y divide-slate-200 border-t border-slate-200">
            {FAQS.map((faq) => (
              <FaqItem key={faq.q} q={faq.q} a={faq.a} />
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* CTA BANNER                                                    */}
      {/* ============================================================ */}
      <section className="py-20 px-4 bg-gradient-to-r from-slate-900 to-slate-800">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-3xl font-bold text-white mb-4">
            Ready to automate your enterprise?
          </h2>
          <p className="text-slate-300 mb-8 max-w-xl mx-auto">
            Start free with 35 agents and 20 connectors. Upgrade to Pro for 43 connectors and unlimited tasks.
          </p>
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <Link
              to="/login"
              className="bg-white text-slate-900 px-8 py-3 rounded-lg text-sm font-semibold hover:bg-slate-100 transition-all"
            >
              Start Free
            </Link>
            <button
              onClick={() => setShowDemo(true)}
              className="bg-gradient-to-r from-blue-500 to-violet-600 text-white px-8 py-3 rounded-lg text-sm font-semibold hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25"
            >
              Book a Demo
            </button>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* FOOTER                                                        */}
      {/* ============================================================ */}
      <footer className="bg-slate-900 border-t border-slate-800 py-12 px-4">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-[10px]">
              AO
            </div>
            <span className="text-slate-400 text-sm">AgenticOrg</span>
          </div>
          <div className="flex items-center gap-6">
            <Link to="/" className="text-slate-400 hover:text-white text-sm transition-colors">Home</Link>
            <Link to="/evals" className="text-slate-400 hover:text-white text-sm transition-colors">Evals</Link>
            <Link to="/login" className="text-slate-400 hover:text-white text-sm transition-colors">Sign In</Link>
          </div>
          <p className="text-slate-500 text-xs">
            &copy; {new Date().getFullYear()} AgenticOrg by Edumatica. All rights reserved.
          </p>
        </div>
      </footer>
    </div>
  );
}
