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
  { value: "500+", label: "Resumes per open position to screen manually", color: "text-red-500" },
  { value: "5 days", label: "Average time to fully onboard a new hire", color: "text-orange-500" },
  { value: "3 errors", label: "Payroll mistakes per month causing employee distrust", color: "text-red-500" },
];

const FEATURES = [
  {
    title: "Recruitment Engine",
    description: "Screen 500 resumes per hour with AI scoring. Auto-shortlist candidates, schedule interviews, and generate offer letters. Reduce time-to-hire from 45 to 12 days.",
    icon: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z",
    gradient: "from-blue-500 to-teal-600",
  },
  {
    title: "Day-1 Onboarding",
    description: "Automated onboarding workflow: document collection, IT provisioning, compliance training, buddy assignment, and welcome kit — all completed before the employee walks in.",
    icon: "M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z",
    gradient: "from-blue-500 to-cyan-600",
  },
  {
    title: "Zero-Error Payroll",
    description: "AI validates every payslip before processing. Auto-calculate PF, ESI, PT, TDS. Handle reimbursements, bonuses, and arrears with zero manual errors.",
    icon: "M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z",
    gradient: "from-emerald-500 to-teal-600",
  },
  {
    title: "Performance Management",
    description: "Continuous feedback loops with AI-generated insights. OKR tracking, 360-degree reviews, bell curve analytics, and automatic PIP identification and tracking.",
    icon: "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z",
    gradient: "from-orange-500 to-red-600",
  },
  {
    title: "Learning & Development",
    description: "AI-curated learning paths based on role, skill gaps, and career goals. Track completion, certifications, and ROI of training programs across the organization.",
    icon: "M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253",
    gradient: "from-amber-500 to-orange-600",
  },
  {
    title: "Statutory Compliance",
    description: "Auto-file EPFO, ESI, and Professional Tax returns. Track compliance deadlines, generate Form 12BB, and maintain audit-ready records for all statutory obligations.",
    icon: "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z",
    gradient: "from-cyan-500 to-teal-600",
  },
];

const HOW_IT_WORKS = [
  { step: "1", title: "Connect HR Systems", description: "Link Darwinbox, Keka, or your existing HRMS. Import employee data, org structure, and policies in minutes." },
  { step: "2", title: "Configure Agents", description: "Set up recruitment, payroll, and compliance agents. Define approval workflows, escalation rules, and automation preferences." },
  { step: "3", title: "Shadow & Validate", description: "Agents run alongside your HR team for 1 week. Every action is reviewed and validated before going live." },
  { step: "4", title: "Scale HR Ops", description: "Promote to active. Agents handle recruitment, onboarding, payroll, and compliance autonomously across all locations." },
];

const TRUST_LOGOS = [
  { name: "Darwinbox", abbr: "DB" },
  { name: "Keka", abbr: "KK" },
  { name: "EPFO", abbr: "EP" },
  { name: "LinkedIn", abbr: "LI" },
  { name: "Greenhouse", abbr: "GH" },
  { name: "ESI Portal", abbr: "ES" },
];

const KPI_CARDS = [
  { label: "Headcount", value: "1,247", change: "+32 this quarter", positive: true },
  { label: "Attrition Rate", value: "8.2%", change: "-3.1%", positive: true },
  { label: "eNPS Score", value: "72", change: "+14 pts", positive: true },
  { label: "Time-to-Hire", value: "12 days", change: "-33 days", positive: true },
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
        body: JSON.stringify({ ...form, source: "chro-solution" }),
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
    "w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 outline-none transition-all";

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
            <p className="text-slate-600">We will contact you within 24 hours to set up your HR automation trial.</p>
            <button onClick={onClose} className="mt-6 bg-gradient-to-r from-blue-500 to-teal-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-teal-700 transition-all">
              Close
            </button>
          </div>
        ) : (
          <>
            <h3 className="text-xl font-bold text-slate-900 mb-1">Book a Demo</h3>
            <p className="text-sm text-slate-500 mb-6">See how AI agents can transform your HR operations.</p>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="chro-name" className="block text-sm font-medium text-slate-700 mb-1">Name <span className="text-red-500">*</span></label>
                <input id="chro-name" required type="text" placeholder="Your full name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="chro-email" className="block text-sm font-medium text-slate-700 mb-1">Work Email <span className="text-red-500">*</span></label>
                <input id="chro-email" required type="email" placeholder="you@company.com" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="chro-company" className="block text-sm font-medium text-slate-700 mb-1">Company</label>
                <input id="chro-company" type="text" placeholder="Your company name" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="chro-role" className="block text-sm font-medium text-slate-700 mb-1">Your Role</label>
                <select id="chro-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className={fieldClass}>
                  <option value="">Select role</option>
                  <option value="chro">CHRO</option>
                  <option value="vp-hr">VP HR</option>
                  <option value="hr-director">HR Director</option>
                  <option value="hr-manager">HR Manager</option>
                  <option value="other">Other</option>
                </select>
              </div>
              {error && <p className="text-sm text-red-600">{error}</p>}
              <button type="submit" disabled={submitting} className="w-full bg-gradient-to-r from-blue-500 to-teal-600 text-white px-6 py-3 rounded-lg text-sm font-semibold hover:from-blue-600 hover:to-teal-700 transition-all shadow-lg shadow-cyan-500/25 disabled:opacity-60 disabled:cursor-not-allowed">
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
/*  CHRO Solution Page                                                 */
/* ------------------------------------------------------------------ */
export default function CHROSolution() {
  const [showDemo, setShowDemo] = useState(false);

  return (
    <div className="min-h-screen font-sans text-slate-900 antialiased overflow-x-hidden">
      <Helmet>
        <title>AI-Powered Virtual HR Team for CHROs | AgenticOrg</title>
        <meta name="description" content="Screen 500 resumes per hour, onboard in 1 day, zero payroll errors. AI agents for recruitment, onboarding, payroll, performance, L&D, and compliance." />
        <link rel="canonical" href="https://agenticorg.ai/solutions/chro" />
      </Helmet>

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
            <button onClick={() => setShowDemo(true)} className="bg-gradient-to-r from-blue-500 to-teal-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-teal-700 transition-all shadow-lg shadow-cyan-500/25">
              Start Free Trial
            </button>
          </div>
        </div>
      </nav>

      {/* HERO */}
      <section className="relative min-h-screen flex items-center overflow-hidden bg-slate-900">
        <div className="absolute inset-0">
          <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900" />
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-violet-500/20 rounded-full blur-3xl animate-pulse" />
          <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
        </div>
        <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: "linear-gradient(rgba(255,255,255,.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.1) 1px, transparent 1px)", backgroundSize: "64px 64px" }} />

        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-16">
          <div className="max-w-4xl mx-auto text-center">
            <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
              <span className="w-2 h-2 bg-violet-400 rounded-full animate-pulse" />
              <span className="text-slate-300 text-sm">Built for CHROs &amp; HR Leaders</span>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-white leading-tight tracking-tight">
              AI-Powered{" "}
              <span className="bg-gradient-to-r from-blue-400 via-cyan-300 to-emerald-400 bg-clip-text text-transparent">
                Virtual HR Team
              </span>
            </h1>

            <p className="mt-6 text-lg sm:text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
              Screen 500 resumes per hour, onboard employees on day 1, and run payroll with zero errors. AI agents that handle every HR function at scale.
            </p>

            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-teal-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-teal-700 transition-all shadow-lg shadow-cyan-500/25"
              >
                Start Free Trial
              </button>
              <a
                href="mailto:sanjeev@agenticorg.ai?subject=CHRO%20Solution%20Demo"
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
                Your HR team is drowning in admin work
              </h2>
              <p className="mt-4 text-lg text-slate-500">
                These numbers are typical for a growing Indian company with 500-5,000 employees.
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
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Your Complete Virtual HR Team</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Six AI-powered agents covering the full employee lifecycle, from hiring to compliance.
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
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Real-Time CHRO Dashboard</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Every people metric that matters, updated in real time across all locations and departments.
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
                <span className="w-2 h-2 bg-violet-400 rounded-full animate-pulse" />
                <span className="text-slate-300 text-sm">Go Live in Under a Week</span>
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-white">How It Works</h2>
              <p className="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">
                Four simple steps from signup to full HR automation.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-4 gap-6">
            {HOW_IT_WORKS.map((s, i) => (
              <FadeIn key={s.step} delay={i * 100}>
                <div className="relative bg-slate-800/50 border border-slate-700 rounded-xl p-6 hover:border-violet-500/50 transition-colors h-full">
                  <div className="w-10 h-10 rounded-full bg-violet-500/20 text-violet-400 flex items-center justify-center text-lg font-bold mb-4">{s.step}</div>
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
            <div className="bg-gradient-to-br from-cyan-50 via-white to-teal-50 rounded-3xl border border-slate-200 p-8 sm:p-12 text-center">
              <div className="inline-flex items-center gap-2 bg-violet-100 text-violet-700 rounded-full px-4 py-1.5 text-sm font-medium mb-6">
                CHRO Suite &mdash; Enterprise Ready
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 mb-4">
                Transform Your HR Operations
              </h2>
              <p className="text-lg text-slate-600 mb-6 max-w-xl mx-auto">
                Get your virtual HR team running in under a week. Full recruitment, payroll, and compliance automation.
              </p>
              <ul className="grid sm:grid-cols-2 gap-3 mb-8 max-w-lg mx-auto text-left">
                {[
                  "500 resumes/hr screening",
                  "Day-1 ready onboarding",
                  "Zero-error payroll",
                  "EPFO/ESI/PT auto-filing",
                  "360-degree performance",
                  "AI learning paths",
                  "SOC2 audit trail",
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
                  className="inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-teal-600 text-white px-10 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-teal-700 transition-all shadow-lg shadow-cyan-500/25"
                >
                  Start Free Trial
                </button>
                <a
                  href="mailto:sanjeev@agenticorg.ai?subject=CHRO%20Solution%20Demo"
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
            <div className="bg-gradient-to-br from-cyan-50 via-white to-teal-50 rounded-3xl border border-slate-200 overflow-hidden">
              <div className="p-8 sm:p-12">
                <div className="text-center mb-12">
                  <div className="inline-flex items-center gap-2 bg-violet-100 text-violet-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
                    Built for Indian HR Teams
                  </div>
                  <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Trusted Integrations</h2>
                  <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                    Native integrations with India&apos;s leading HR platforms and government compliance portals.
                  </p>
                </div>

                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {TRUST_LOGOS.map((c, i) => (
                    <FadeIn key={c.name} delay={i * 75}>
                      <div className="flex items-center gap-4 bg-white/80 rounded-xl px-5 py-4 border border-slate-100 hover:shadow-md transition-all duration-300">
                        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-400 to-teal-500 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
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
              Stop losing top talent to slow HR processes
            </h2>
            <p className="text-lg text-slate-400 mb-10">
              Join CHROs who have cut time-to-hire from 45 to 12 days and achieved zero payroll errors.
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-teal-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-teal-700 transition-all shadow-lg shadow-cyan-500/25"
              >
                Start Free Trial
              </button>
              <a
                href="mailto:sanjeev@agenticorg.ai?subject=CHRO%20Solution%20Demo"
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
              <p className="text-sm text-slate-400 leading-relaxed">AI-Powered Virtual HR Team for CHROs.</p>
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
