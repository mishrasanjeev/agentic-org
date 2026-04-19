import { useState, useRef, useCallback, useEffect, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";

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
  { value: "88%", label: "Of support tickets mis-routed on first attempt", color: "text-red-500" },
  { value: "4-hour", label: "Average mean time to resolution (MTTR)", color: "text-orange-500" },
  { value: "15%", label: "Vendor SLA breaches going undetected each quarter", color: "text-red-500" },
];

const FEATURES = [
  {
    title: "Support Triage",
    description: "88% auto-classification accuracy on first touch. AI reads tickets, assigns priority, routes to the right team, and suggests resolution — all in under 30 seconds.",
    icon: "M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z",
    gradient: "from-cyan-500 to-blue-600",
  },
  {
    title: "IT Ops & Incident Mgmt",
    description: "Slash MTTR from 4 hours to 15 minutes. AI monitors systems, auto-creates incidents, triggers runbooks, and escalates to on-call engineers with full context.",
    icon: "M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z",
    gradient: "from-orange-500 to-red-600",
  },
  {
    title: "Vendor Management",
    description: "Track vendor performance, contracts, and SLA compliance in real time. Auto-flag breaches, generate scorecards, and trigger renewal workflows 90 days in advance.",
    icon: "M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4",
    gradient: "from-emerald-500 to-teal-600",
  },
  {
    title: "Contract Intelligence",
    description: "AI reads and extracts key terms from contracts. Auto-flag risky clauses, track obligations, and surface renewal dates. No more missed deadlines or unfavorable auto-renewals.",
    icon: "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
    gradient: "from-purple-500 to-pink-600",
  },
  {
    title: "Compliance Guard",
    description: "Continuous compliance monitoring against SOC2, ISO 27001, and internal policies. Auto-generate evidence, track control effectiveness, and prepare for audits year-round.",
    icon: "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z",
    gradient: "from-amber-500 to-orange-600",
  },
  {
    title: "Facilities & Assets",
    description: "Track physical assets, maintenance schedules, and workspace utilization. AI predicts maintenance needs, optimizes space allocation, and manages work orders automatically.",
    icon: "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6",
    gradient: "from-indigo-500 to-violet-600",
  },
];

const HOW_IT_WORKS = [
  { step: "1", title: "Connect Tools", description: "Link Jira, PagerDuty, Zendesk, and ServiceNow. AI agents start ingesting historical data and learning your patterns." },
  { step: "2", title: "Set SLAs", description: "Define response times, resolution targets, and escalation rules. AI agents configure themselves based on your operational standards." },
  { step: "3", title: "Shadow Operations", description: "Agents run in parallel for 1 week, triaging tickets, monitoring systems, and flagging issues — all reviewed before taking action." },
  { step: "4", title: "Scale & Optimize", description: "Promote to active. Agents handle triage, incident response, and vendor management autonomously. MTTR drops within the first week." },
];

const TRUST_LOGOS = [
  { name: "Jira", abbr: "JR" },
  { name: "PagerDuty", abbr: "PD" },
  { name: "Zendesk", abbr: "ZD" },
  { name: "ServiceNow", abbr: "SN" },
  { name: "Slack", abbr: "SL" },
  { name: "Datadog", abbr: "DD" },
];

const KPI_CARDS = [
  { label: "Active Incidents", value: "3", change: "-12 vs last week", positive: true },
  { label: "MTTR", value: "15 min", change: "-3h 45m", positive: true },
  { label: "CSAT Score", value: "94.2%", change: "+8.3%", positive: true },
  { label: "SLA Compliance", value: "99.1%", change: "+14.1%", positive: true },
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
        body: JSON.stringify({ ...form, source: "coo-solution" }),
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
    "w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-500/20 outline-none transition-all";

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
            <p className="text-slate-600">We will contact you within 24 hours to set up your operations automation trial.</p>
            <button onClick={onClose} className="mt-6 bg-gradient-to-r from-cyan-500 to-blue-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:from-cyan-600 hover:to-blue-700 transition-all">
              Close
            </button>
          </div>
        ) : (
          <>
            <h3 className="text-xl font-bold text-slate-900 mb-1">Book a Demo</h3>
            <p className="text-sm text-slate-500 mb-6">See how AI agents can transform your operations.</p>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="coo-name" className="block text-sm font-medium text-slate-700 mb-1">Name <span className="text-red-500">*</span></label>
                <input id="coo-name" required type="text" placeholder="Your full name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="coo-email" className="block text-sm font-medium text-slate-700 mb-1">Work Email <span className="text-red-500">*</span></label>
                <input id="coo-email" required type="email" placeholder="you@company.com" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="coo-company" className="block text-sm font-medium text-slate-700 mb-1">Company</label>
                <input id="coo-company" type="text" placeholder="Your company name" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="coo-role" className="block text-sm font-medium text-slate-700 mb-1">Your Role</label>
                <select id="coo-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className={fieldClass}>
                  <option value="">Select role</option>
                  <option value="coo">COO</option>
                  <option value="vp-ops">VP Operations</option>
                  <option value="ops-director">Operations Director</option>
                  <option value="it-director">IT Director</option>
                  <option value="other">Other</option>
                </select>
              </div>
              {error && <p className="text-sm text-red-600">{error}</p>}
              <button type="submit" disabled={submitting} className="w-full bg-gradient-to-r from-cyan-500 to-blue-600 text-white px-6 py-3 rounded-lg text-sm font-semibold hover:from-cyan-600 hover:to-blue-700 transition-all shadow-lg shadow-cyan-500/25 disabled:opacity-60 disabled:cursor-not-allowed">
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
/*  COO Solution Page                                                  */
/* ------------------------------------------------------------------ */
export default function COOSolution() {
  const [showDemo, setShowDemo] = useState(false);

  return (
    <div className="min-h-screen font-sans text-slate-900 antialiased overflow-x-hidden">
      <Helmet>
        <title>AI-Powered Virtual Operations Team for COOs | AgenticOrg</title>
        <meta name="description" content="Cut MTTR from 4 hours to 15 minutes. Auto-triage 88% of tickets, monitor vendor SLAs, and ensure compliance. AI agents built for COOs and operations leaders." />
        <link rel="canonical" href="https://agenticorg.ai/solutions/coo" />
      </Helmet>

      {/* NAVBAR */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-slate-900/90 backdrop-blur-md border-b border-slate-700/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm">AO</div>
            <span className="text-white font-semibold text-lg">AgenticOrg</span>
          </Link>
          <div className="hidden md:flex items-center gap-8">
            <Link to="/" className="text-slate-300 hover:text-white text-sm transition-colors">Home</Link>
            <Link to="/pricing" className="text-slate-300 hover:text-white text-sm transition-colors">Pricing</Link>
            <Link to="/blog" className="text-slate-300 hover:text-white text-sm transition-colors">Blog</Link>
          </div>
          <div className="flex items-center gap-3">
            <Link to="/login" className="hidden sm:inline-flex border border-slate-500 text-slate-300 hover:text-white hover:border-white px-4 py-2 rounded-lg text-sm font-medium transition-all">Sign In</Link>
            <button onClick={() => setShowDemo(true)} className="bg-gradient-to-r from-cyan-500 to-blue-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:from-cyan-600 hover:to-blue-700 transition-all shadow-lg shadow-cyan-500/25">
              Start Free Trial
            </button>
          </div>
        </div>
      </nav>

      {/* HERO */}
      <section className="relative min-h-screen flex items-center overflow-hidden bg-slate-900">
        <div className="absolute inset-0">
          <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900" />
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-cyan-500/20 rounded-full blur-3xl animate-pulse" />
          <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-blue-500/20 rounded-full blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
        </div>
        <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: "linear-gradient(rgba(255,255,255,.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.1) 1px, transparent 1px)", backgroundSize: "64px 64px" }} />

        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-16">
          <div className="max-w-4xl mx-auto text-center">
            <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
              <span className="w-2 h-2 bg-cyan-400 rounded-full animate-pulse" />
              <span className="text-slate-300 text-sm">Built for COOs &amp; Operations Leaders</span>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-white leading-tight tracking-tight">
              AI-Powered{" "}
              <span className="bg-gradient-to-r from-cyan-400 via-blue-300 to-indigo-400 bg-clip-text text-transparent">
                Virtual Operations Team
              </span>
            </h1>

            <p className="mt-6 text-lg sm:text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
              Cut MTTR from 4 hours to 15 minutes. Auto-triage tickets, monitor vendor SLAs, and ensure compliance with AI agents that never sleep.
            </p>

            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-cyan-500 to-blue-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-cyan-600 hover:to-blue-700 transition-all shadow-lg shadow-cyan-500/25"
              >
                Start Free Trial
              </button>
              <a
                href="mailto:sanjeev@agenticorg.ai?subject=COO%20Solution%20Demo"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
              >
                Book a Demo
              </a>
            </div>

            <p className="mt-4 text-sm text-slate-500">No credit card required &middot; 14-day free trial &middot; Cancel anytime</p>
          </div>
        </div>
      </section>

      {/* PAIN POINTS */}
      <section className="py-24 bg-slate-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16 max-w-3xl mx-auto">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 leading-tight">
                Your operations are held together by manual processes
              </h2>
              <p className="mt-4 text-lg text-slate-500">
                These numbers are typical for companies with 200+ employees and 50+ vendor relationships.
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
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Your Complete Virtual Operations Team</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Six AI-powered agents that cover every aspect of operations, from support triage to facility management.
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
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Real-Time COO Dashboard</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Every operational metric at a glance. No more digging through Jira, Zendesk, and spreadsheets.
              </p>
            </div>
          </FadeIn>

          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {KPI_CARDS.map((kpi, i) => (
              <FadeIn key={kpi.label} delay={i * 100}>
                <div className="bg-white rounded-2xl p-6 border border-slate-200 hover:shadow-lg transition-all duration-300">
                  <p className="text-sm text-slate-500 mb-1">{kpi.label}</p>
                  <p className="text-3xl font-bold text-slate-900 mb-2">{kpi.value}</p>
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
                <span className="w-2 h-2 bg-cyan-400 rounded-full animate-pulse" />
                <span className="text-slate-300 text-sm">Go Live in Under a Week</span>
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-white">How It Works</h2>
              <p className="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">
                Four simple steps from signup to full operations automation.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-4 gap-6">
            {HOW_IT_WORKS.map((s, i) => (
              <FadeIn key={s.step} delay={i * 100}>
                <div className="relative bg-slate-800/50 border border-slate-700 rounded-xl p-6 hover:border-cyan-500/50 transition-colors h-full">
                  <div className="w-10 h-10 rounded-full bg-cyan-500/20 text-cyan-400 flex items-center justify-center text-lg font-bold mb-4">{s.step}</div>
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
            <div className="bg-gradient-to-br from-cyan-50 via-white to-blue-50 rounded-3xl border border-slate-200 p-8 sm:p-12 text-center">
              <div className="inline-flex items-center gap-2 bg-cyan-100 text-cyan-700 rounded-full px-4 py-1.5 text-sm font-medium mb-6">
                COO Suite &mdash; Enterprise Ready
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 mb-4">
                Transform Your Operations
              </h2>
              <p className="text-lg text-slate-600 mb-6 max-w-xl mx-auto">
                Get your virtual operations team running in under a week. Full triage, incident, and compliance automation.
              </p>
              <ul className="grid sm:grid-cols-2 gap-3 mb-8 max-w-lg mx-auto text-left">
                {[
                  "88% auto-triage accuracy",
                  "MTTR: 4hr to 15min",
                  "Vendor SLA monitoring",
                  "Contract intelligence",
                  "SOC2/ISO compliance",
                  "Asset management",
                  "Real-time dashboards",
                  "14-day free trial",
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
                  className="inline-flex items-center justify-center bg-gradient-to-r from-cyan-500 to-blue-600 text-white px-10 py-3.5 rounded-xl text-base font-semibold hover:from-cyan-600 hover:to-blue-700 transition-all shadow-lg shadow-cyan-500/25"
                >
                  Start Free Trial
                </button>
                <a
                  href="mailto:sanjeev@agenticorg.ai?subject=COO%20Solution%20Demo"
                  className="inline-flex items-center justify-center border border-slate-300 text-slate-700 px-10 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-100 transition-all"
                >
                  Book a Demo
                </a>
              </div>
              <p className="mt-3 text-sm text-slate-500">No credit card required. Cancel anytime.</p>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* TRUST / INTEGRATIONS */}
      <section className="py-24 bg-slate-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="bg-gradient-to-br from-cyan-50 via-white to-blue-50 rounded-3xl border border-slate-200 overflow-hidden">
              <div className="p-8 sm:p-12">
                <div className="text-center mb-12">
                  <div className="inline-flex items-center gap-2 bg-cyan-100 text-cyan-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
                    Built for Ops-First Organizations
                  </div>
                  <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Trusted Integrations</h2>
                  <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                    Native integrations with the world&apos;s leading operations, ITSM, and monitoring platforms.
                  </p>
                </div>

                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {TRUST_LOGOS.map((c, i) => (
                    <FadeIn key={c.name} delay={i * 75}>
                      <div className="flex items-center gap-4 bg-white/80 rounded-xl px-5 py-4 border border-slate-100 hover:shadow-md transition-all duration-300">
                        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-cyan-400 to-blue-500 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
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
              Stop firefighting. Let AI agents handle operations.
            </h2>
            <p className="text-lg text-slate-400 mb-10">
              Join COOs who have cut MTTR from 4 hours to 15 minutes and achieved 99.1% SLA compliance.
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-cyan-500 to-blue-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-cyan-600 hover:to-blue-700 transition-all shadow-lg shadow-cyan-500/25"
              >
                Start Free Trial
              </button>
              <a
                href="mailto:sanjeev@agenticorg.ai?subject=COO%20Solution%20Demo"
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
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm">AO</div>
                <span className="text-white font-semibold">AgenticOrg</span>
              </div>
              <p className="text-sm text-slate-400 leading-relaxed">AI-Powered Virtual Operations Team for COOs.</p>
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
                <li><span className="text-slate-400 text-sm">Edumatica Pvt Ltd</span></li>
                <li><span className="text-slate-400 text-sm">Bengaluru, India</span></li>
              </ul>
            </div>
          </div>
          <div className="border-t border-slate-800 pt-8 flex flex-col sm:flex-row items-center justify-between gap-4">
            <p className="text-sm text-slate-500">&copy; 2026 AgenticOrg &middot; Edumatica Pvt Ltd. All rights reserved.</p>
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
