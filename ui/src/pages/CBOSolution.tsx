import { useState, useRef, useCallback, useEffect, type FormEvent } from "react";
import { Link } from "react-router-dom";

/* ------------------------------------------------------------------ */
/*  useInView — Intersection Observer hook for scroll animations       */
/* ------------------------------------------------------------------ */
function useInView(threshold = 0.15): { ref: (el: HTMLDivElement | null) => void; visible: boolean } {
  const [visible, setVisible] = useState(false);
  const obsRef = useRef<IntersectionObserver | null>(null);

  const setRef = useCallback((el: HTMLDivElement | null) => {
    if (obsRef.current) { obsRef.current.disconnect(); obsRef.current = null; }
    if (!el) return;
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) { setVisible(true); obs.disconnect(); } },
      { threshold }
    );
    obs.observe(el);
    obsRef.current = obs;
  }, [threshold]);

  return { ref: setRef, visible };
}

function FadeIn({ children, className = "", delay = 0 }: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  const { ref, visible } = useInView();
  return (
    <div
      ref={ref}
      className={`transition-all duration-700 ease-out ${visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"} ${className}`}
      style={{ transitionDelay: `${delay}ms` }}
    >
      {children}
    </div>
  );
}

function CheckIcon({ className = "w-5 h-5 text-emerald-500" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const PAIN_STATS = [
  { value: "Backlogged", label: "Contract review and obligation queues", color: "text-red-500" },
  { value: "Distributed", label: "Control evidence and remediation ownership", color: "text-orange-500" },
  { value: "Time-sensitive", label: "Corporate and regulatory filing work", color: "text-red-500" },
];

const FEATURES = [
  {
    title: "Legal Ops & Contract Review",
    description: "Extract contract terms, compare clauses against configured playbooks, and draft redlines for qualified legal review. Counsel retains interpretation, negotiation, and approval authority.",
    icon: "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
    gradient: "from-amber-500 to-orange-600",
  },
  {
    title: "Risk & Compliance",
    description: "Map configured evidence against internal policies and selected regulatory reference sets, surface potential gaps, and track remediation review. This supports governance work; it does not guarantee compliance.",
    icon: "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z",
    gradient: "from-red-500 to-rose-600",
  },
  {
    title: "Corporate Secretary",
    description: "Draft board minutes, register updates, appointment checklists, and filing packets. Authorized officers and professionals review, approve, and submit through configured portals or providers.",
    icon: "M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4",
    gradient: "from-blue-500 to-cyan-600",
  },
  {
    title: "Internal Communications",
    description: "Draft internal newsletters, policy updates, and town-hall summaries, and summarize feedback signals. Communications remain subject to owner review and publication approval.",
    icon: "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z",
    gradient: "from-emerald-500 to-teal-600",
  },
  {
    title: "Data Governance",
    description: "Classify sensitive data, surface potential PII exposure, and track configured access and lineage evidence. These workflows can support DPDPA programs but do not establish compliance.",
    icon: "M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4",
    gradient: "from-blue-600 to-emerald-500",
  },
  {
    title: "Fraud Detection",
    description: "Surface anomalies in configured transaction, expense, and vendor-payment data, then assemble evidence for investigator review. Fraud determinations and actions remain human-owned.",
    icon: "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z",
    gradient: "from-cyan-500 to-teal-600",
  },
];

const HOW_IT_WORKS = [
  { step: "1", title: "Connect Systems", description: "Configure supported document, workflow, and portal sources. Availability depends on credentials, scopes, tenant plan, provider APIs, and portal access." },
  { step: "2", title: "Define Policies", description: "Provide approved frameworks, contract playbooks, governance policies, thresholds, action scopes, and review responsibilities." },
  { step: "3", title: "Shadow & Validate", description: "Run selected workflows in parallel, evaluate contract and risk outputs, and review exceptions before enabling any action." },
  { step: "4", title: "Enable Selected Actions", description: "Enable only validated actions within configured scopes. Legal, filing, compliance, fraud, and governance decisions retain accountable human owners." },
];

const TRUST_LOGOS = [
  { name: "DocuSign", abbr: "DS" },
  { name: "Confluence", abbr: "CF" },
  { name: "ServiceNow", abbr: "SN" },
  { name: "MCA Portal", abbr: "MC" },
  { name: "SEBI", abbr: "SB" },
  { name: "Digio", abbr: "DG" },
];

const KPI_CARDS = [
  { label: "Contract Queue", value: "In review", change: "Counsel-owned", positive: true },
  { label: "Control Evidence", value: "Mapped", change: "Review required", positive: true },
  { label: "Remediation", value: "Tracked", change: "Owner assigned", positive: true },
  { label: "Filing Packet", value: "Draft", change: "Approval required", positive: true },
];

/* ------------------------------------------------------------------ */
/*  Demo Modal                                                         */
/* ------------------------------------------------------------------ */
function DemoModal({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ name: "", email: "", company: "", role: "" });
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const res = await fetch("/api/v1/demo-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, source: "cbo-solution" }),
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
    "w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:border-amber-500 focus:ring-2 focus:ring-amber-500/20 outline-none transition-all";

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/70 backdrop-blur-sm px-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
    >
      <div className="relative w-full max-w-md rounded-2xl bg-white shadow-2xl p-8">
        <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-slate-600 transition-colors" aria-label="Close">
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
            <p className="text-slate-600">Your request was saved. Our team will follow up using the contact details you provided.</p>
            <button onClick={onClose} className="mt-6 bg-gradient-to-r from-amber-500 to-orange-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:from-amber-600 hover:to-orange-700 transition-all">
              Close
            </button>
          </div>
        ) : (
          <>
            <h3 className="text-xl font-bold text-slate-900 mb-1">Book a Demo</h3>
            <p className="text-sm text-slate-500 mb-6">See how AI agents can transform your business operations.</p>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="cbo-name" className="block text-sm font-medium text-slate-700 mb-1">Name <span className="text-red-500">*</span></label>
                <input id="cbo-name" required type="text" placeholder="Your full name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="cbo-email" className="block text-sm font-medium text-slate-700 mb-1">Work Email <span className="text-red-500">*</span></label>
                <input id="cbo-email" required type="email" placeholder="you@company.com" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="cbo-company" className="block text-sm font-medium text-slate-700 mb-1">Company</label>
                <input id="cbo-company" type="text" placeholder="Your company name" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="cbo-role" className="block text-sm font-medium text-slate-700 mb-1">Your Role</label>
                <select id="cbo-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className={fieldClass}>
                  <option value="">Select role</option>
                  <option value="cbo">CBO / Chief Business Officer</option>
                  <option value="general-counsel">General Counsel</option>
                  <option value="company-secretary">Company Secretary</option>
                  <option value="compliance-head">Head of Compliance</option>
                  <option value="other">Other</option>
                </select>
              </div>
              {error && <p className="text-sm text-red-600">{error}</p>}
              <button type="submit" disabled={submitting} className="w-full bg-gradient-to-r from-amber-500 to-orange-600 text-white px-6 py-3 rounded-lg text-sm font-semibold hover:from-amber-600 hover:to-orange-700 transition-all shadow-lg shadow-amber-500/25 disabled:opacity-60 disabled:cursor-not-allowed">
                {submitting ? "Submitting..." : "Book a Demo"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  CBO Solution Page                                                  */
/* ------------------------------------------------------------------ */
export default function CBOSolution() {
  const [showDemo, setShowDemo] = useState(false);

  return (
    <div className="min-h-screen font-sans text-slate-900 antialiased overflow-x-hidden">

      {/* NAVBAR */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-slate-900/90 backdrop-blur-md border-b border-slate-700/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-sm">AO</div>
            <span className="text-white font-semibold text-lg">AgenticOrg</span>
          </Link>
          <div className="hidden md:flex items-center gap-8">
            <Link to="/" className="text-slate-300 hover:text-white text-sm transition-colors">Home</Link>
            <Link to="/pricing" className="text-slate-300 hover:text-white text-sm transition-colors">Pricing</Link>
            <Link to="/blog" className="text-slate-300 hover:text-white text-sm transition-colors">Blog</Link>
          </div>
          <div className="flex items-center gap-3">
            <Link to="/login" className="hidden sm:inline-flex border border-slate-500 text-slate-300 hover:text-white hover:border-white px-4 py-2 rounded-lg text-sm font-medium transition-all">Sign In</Link>
            <button onClick={() => setShowDemo(true)} className="bg-gradient-to-r from-amber-500 to-orange-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:from-amber-600 hover:to-orange-700 transition-all shadow-lg shadow-amber-500/25">
              Request a Demo
            </button>
          </div>
        </div>
      </nav>

      {/* HERO */}
      <section className="relative min-h-screen flex items-center overflow-hidden bg-slate-900">
        <div className="absolute inset-0">
          <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900" />
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-amber-500/20 rounded-full blur-3xl animate-pulse" />
          <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-orange-500/20 rounded-full blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
        </div>
        <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: "linear-gradient(rgba(255,255,255,.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.1) 1px, transparent 1px)", backgroundSize: "64px 64px" }} />

        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-16">
          <div className="max-w-4xl mx-auto text-center">
            <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
              <span className="w-2 h-2 bg-amber-400 rounded-full animate-pulse" />
              <span className="text-slate-300 text-sm">Built for CBOs &amp; Business Leaders</span>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-white leading-tight tracking-tight">
              AI-Powered{" "}
              <span className="bg-gradient-to-r from-amber-400 via-orange-300 to-red-400 bg-clip-text text-transparent">
                Virtual Business Operations Team
              </span>
            </h1>

            <p className="mt-6 text-lg sm:text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
              Coordinate contract review, control evidence, corporate-secretary work, data governance, communications, and anomaly review through governed AI workflows.
            </p>

            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-amber-500 to-orange-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-amber-600 hover:to-orange-700 transition-all shadow-lg shadow-amber-500/25"
              >
                Request a Demo
              </button>
              <a
                href="mailto:sanjeev@agenticorg.ai?subject=CBO%20Solution%20Demo"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
              >
                Book a Demo
              </a>
            </div>

            <p className="mt-4 text-sm text-slate-500">Deployment scope, connector availability, provider access, legal review, and approval requirements are confirmed during discovery.</p>
          </div>
        </div>
      </section>

      {/* PAIN POINTS */}
      <section className="py-24 bg-slate-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16 max-w-3xl mx-auto">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 leading-tight">
                Common governance and legal-operations friction
              </h2>
              <p className="mt-4 text-lg text-slate-500">
                Illustrative workflow states; actual priorities depend on legal context, control framework, source systems, and accountable owners.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-3 gap-8">
            {PAIN_STATS.map((s, i) => (
              <FadeIn key={s.value} delay={i * 150}>
                <div className="bg-white rounded-2xl p-8 border border-slate-200 text-center hover:shadow-lg transition-all duration-300">
                  <div className={`text-4xl sm:text-5xl font-extrabold ${s.color} mb-3`}>{s.value}</div>
                  <p className="text-slate-600">{s.label}</p>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* FEATURES GRID */}
      <section className="py-24 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Governed AI Workflows for Business Operations</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Configure scoped workflows for contracts, control evidence, corporate-secretary tasks, communications, data governance, and anomaly review.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
            {FEATURES.map((f, i) => (
              <FadeIn key={f.title} delay={i * 100}>
                <div className="bg-gradient-to-br from-slate-50 to-white rounded-2xl p-6 border border-slate-200 hover:shadow-lg transition-all duration-300 h-full">
                  <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${f.gradient} flex items-center justify-center mb-4`}>
                    <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={f.icon} />
                    </svg>
                  </div>
                  <h3 className="text-lg font-bold text-slate-900 mb-2">{f.title}</h3>
                  <p className="text-sm text-slate-600 leading-relaxed">{f.description}</p>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* KPI PREVIEW */}
      <section className="py-24 bg-slate-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Operational CBO Dashboard</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Illustrative operational states. Actual metrics and freshness depend on connected source data, review status, and configured evidence.
              </p>
            </div>
          </FadeIn>

          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {KPI_CARDS.map((kpi, i) => (
              <FadeIn key={kpi.label} delay={i * 100}>
                <div className="bg-white rounded-2xl p-6 border border-slate-200 hover:shadow-lg transition-all duration-300">
                  <p className="text-sm text-slate-500 mb-1">{kpi.label}</p>
                  <p className="text-3xl font-bold text-slate-900 mb-2 tabular-nums">{kpi.value}</p>
                  <span className={`inline-flex items-center gap-1 text-sm font-medium ${kpi.positive ? "text-emerald-600" : "text-red-600"}`}>
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={kpi.positive ? "M5 10l7-7m0 0l7 7m-7-7v18" : "M19 14l-7 7m0 0l-7-7m7 7V3"} />
                    </svg>
                    {kpi.change}
                  </span>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section className="py-24 bg-slate-900">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
                <span className="w-2 h-2 bg-amber-400 rounded-full animate-pulse" />
                <span className="text-slate-300 text-sm">Configure, Validate, Then Enable</span>
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-white">How It Works</h2>
              <p className="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">
                Connect sources, define policy, validate in shadow mode, and enable selected actions with legal and accountable-owner approval.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-4 gap-6">
            {HOW_IT_WORKS.map((s, i) => (
              <FadeIn key={s.step} delay={i * 100}>
                <div className="relative bg-slate-800/50 border border-slate-700 rounded-xl p-6 hover:border-amber-500/50 transition-colors h-full">
                  <div className="w-10 h-10 rounded-full bg-amber-500/20 text-amber-400 flex items-center justify-center text-lg font-bold mb-4">{s.step}</div>
                  <h3 className="text-white font-semibold text-lg mb-2">{s.title}</h3>
                  <p className="text-slate-400 text-sm">{s.description}</p>
                  {i < HOW_IT_WORKS.length - 1 && (
                    <div className="hidden md:block absolute top-1/2 -right-3 transform -translate-y-1/2 text-slate-600">
                      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </div>
                  )}
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* PRICING CTA */}
      <section className="py-24 bg-white">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="bg-gradient-to-br from-amber-50 via-white to-orange-50 rounded-3xl border border-slate-200 p-8 sm:p-12 text-center">
              <div className="inline-flex items-center gap-2 bg-amber-100 text-amber-700 rounded-full px-4 py-1.5 text-sm font-medium mb-6">
                Configurable CBO Workflow Suite
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 mb-4">
                Transform Your Business Operations
              </h2>
              <p className="text-lg text-slate-600 mb-6 max-w-xl mx-auto">
                Select the governance workflows that fit your controls. Availability depends on plan, connectors, provider or portal access, and approval policy.
              </p>
              <ul className="grid sm:grid-cols-2 gap-3 mb-8 max-w-lg mx-auto text-left">
                {[
                  "Contract extraction and draft redlines",
                  "Control-evidence mapping",
                  "Regulatory filing workpapers",
                  "Anomaly review support",
                  "DPDPA program support",
                  "Draft board minutes and registers",
                  "Approval and audit evidence",
                  "Human-owned legal and filing decisions",
                ].map((item) => (
                  <li key={item} className="flex items-center gap-2 text-sm text-slate-700">
                    <CheckIcon className="w-4 h-4 text-emerald-500 flex-shrink-0" />
                    {item}
                  </li>
                ))}
              </ul>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                <button
                  onClick={() => setShowDemo(true)}
                  className="inline-flex items-center justify-center bg-gradient-to-r from-amber-500 to-orange-600 text-white px-10 py-3.5 rounded-xl text-base font-semibold hover:from-amber-600 hover:to-orange-700 transition-all shadow-lg shadow-amber-500/25"
                >
                  Request a Demo
                </button>
                <a
                  href="mailto:sanjeev@agenticorg.ai?subject=CBO%20Solution%20Demo"
                  className="inline-flex items-center justify-center border border-slate-300 text-slate-700 px-10 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-100 transition-all"
                >
                  Book a Demo
                </a>
              </div>
              <p className="mt-3 text-sm text-slate-500">Commercial terms and enabled capabilities are confirmed for the selected plan and deployment scope.</p>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* TRUST / INTEGRATIONS */}
      <section className="py-24 bg-slate-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="bg-gradient-to-br from-amber-50 via-white to-orange-50 rounded-3xl border border-slate-200 overflow-hidden">
              <div className="p-8 sm:p-12">
                <div className="text-center mb-12">
                  <div className="inline-flex items-center gap-2 bg-amber-100 text-amber-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
                    Built for Governance-First Organizations
                  </div>
                  <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Connector Availability</h2>
                  <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                    Listed systems represent supported integration surfaces. Availability depends on credentials, scopes, provider APIs, tenant plan, portal access, and provider approval.
                  </p>
                </div>

                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {TRUST_LOGOS.map((c, i) => (
                    <FadeIn key={c.name} delay={i * 75}>
                      <div className="flex items-center gap-4 bg-white/80 rounded-xl px-5 py-4 border border-slate-100 hover:shadow-md transition-all duration-300">
                        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
                          {c.abbr}
                        </div>
                        <div>
                          <h3 className="font-semibold text-slate-900 text-sm">{c.name}</h3>
                        </div>
                      </div>
                    </FadeIn>
                  ))}
                </div>
              </div>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* FINAL CTA */}
      <section className="py-24 bg-slate-900">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <FadeIn>
            <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
              Bring governance work into accountable workflows
            </h2>
            <p className="text-lg text-slate-400 mb-10">
              Review source-linked drafts and evidence while counsel and accountable officers retain ownership of legal, filing, compliance, and fraud decisions.
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-amber-500 to-orange-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-amber-600 hover:to-orange-700 transition-all shadow-lg shadow-amber-500/25"
              >
                Request a Demo
              </button>
              <a
                href="mailto:sanjeev@agenticorg.ai?subject=CBO%20Solution%20Demo"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
              >
                Book a Demo
              </a>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="bg-slate-950 border-t border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-8 mb-12">
            <div>
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-sm">AO</div>
                <span className="text-white font-semibold">AgenticOrg</span>
              </div>
              <p className="text-sm text-slate-400 leading-relaxed">AI-Powered Virtual Business Ops Team for CBOs.</p>
            </div>
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">Platform</h4>
              <ul className="space-y-2">
                <li><Link to="/" className="text-slate-400 hover:text-white text-sm transition-colors">Home</Link></li>
                <li><Link to="/pricing" className="text-slate-400 hover:text-white text-sm transition-colors">Pricing</Link></li>
                <li><Link to="/blog" className="text-slate-400 hover:text-white text-sm transition-colors">Blog</Link></li>
              </ul>
            </div>
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">Solutions</h4>
              <ul className="space-y-2">
                <li><Link to="/solutions/ca-firms" className="text-slate-400 hover:text-white text-sm transition-colors">For CA Firms</Link></li>
                <li><Link to="/solutions/cfo" className="text-slate-400 hover:text-white text-sm transition-colors">For CFOs</Link></li>
                <li><Link to="/solutions/chro" className="text-slate-400 hover:text-white text-sm transition-colors">For CHROs</Link></li>
                <li><Link to="/solutions/cmo" className="text-slate-400 hover:text-white text-sm transition-colors">For CMOs</Link></li>
                <li><Link to="/solutions/coo" className="text-slate-400 hover:text-white text-sm transition-colors">For COOs</Link></li>
                <li><Link to="/solutions/cbo" className="text-slate-400 hover:text-white text-sm transition-colors">For CBOs</Link></li>
              </ul>
            </div>
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">Company</h4>
              <ul className="space-y-2">
                <li><a href="mailto:sanjeev@agenticorg.ai" className="text-slate-400 hover:text-white text-sm transition-colors">Contact</a></li>
                <li><span className="text-slate-400 text-sm">AgenticOrg</span></li>
                <li><span className="text-slate-400 text-sm">Bengaluru, India</span></li>
              </ul>
            </div>
          </div>
          <div className="border-t border-slate-800 pt-8 flex flex-col sm:flex-row items-center justify-between gap-4">
            <p className="text-sm text-slate-500">&copy; 2026 AgenticOrg. All rights reserved.</p>
            <div className="flex items-center gap-6">
              <a href="/privacy" className="text-sm text-slate-500 hover:text-slate-300 transition-colors">Privacy</a>
              <a href="/terms" className="text-sm text-slate-500 hover:text-slate-300 transition-colors">Terms</a>
            </div>
          </div>
        </div>
      </footer>

      {showDemo && <DemoModal onClose={() => setShowDemo(false)} />}
    </div>
  );
}
