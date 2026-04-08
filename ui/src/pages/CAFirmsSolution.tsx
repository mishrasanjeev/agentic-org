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

/* ------------------------------------------------------------------ */
/*  Check icon                                                         */
/* ------------------------------------------------------------------ */
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
  { value: "72 hours", label: "Average month-end close cycle", color: "text-red-500" },
  { value: "\u20B912L/year", label: "Lost to missed early-payment discounts", color: "text-orange-500" },
  { value: "5 days", label: "To reconcile bank statements manually", color: "text-red-500" },
];

const FEATURES = [
  {
    title: "Multi-Client Management",
    description: "One login, 7+ client companies, instant switching. Manage all your clients from a single dashboard with company-level isolation and role-based access.",
    icon: "M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4",
    gradient: "from-blue-500 to-cyan-600",
  },
  {
    title: "GST Compliance",
    description: "Auto GSTR-1/3B/9 filing with DSC, 2A reconciliation. GSTN portal integration with Adaequare GSP for seamless filing and real-time ITC matching.",
    icon: "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
    gradient: "from-orange-500 to-red-600",
  },
  {
    title: "TDS Automation",
    description: "Form 26Q/24Q auto-generation, Form 16A issuance, and 26AS reconciliation. Ensure timely TDS compliance with zero manual effort.",
    icon: "M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z",
    gradient: "from-purple-500 to-pink-600",
  },
  {
    title: "Bank Reconciliation",
    description: "Auto-match with Account Aggregator integration. Flag stale items, match 99.7% transactions automatically, escalate breaks to partners.",
    icon: "M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z",
    gradient: "from-emerald-500 to-teal-600",
  },
  {
    title: "Month-End Close",
    description: "7-step workflow with CFO sign-off. Reduce close cycle from 5 days to 4 hours with automated checklists, reconciliation, and approval chains.",
    icon: "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z",
    gradient: "from-amber-500 to-orange-600",
  },
  {
    title: "Audit Trail",
    description: "SOC2 compliant, HMAC-signed, partner approval tracking. Every action logged with immutable audit entries for regulatory compliance.",
    icon: "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z",
    gradient: "from-indigo-500 to-violet-600",
  },
];

const HOW_IT_WORKS = [
  { step: "1", title: "Onboard Client", description: "Enter GSTIN, PAN, TAN, bank details. System auto-validates and creates the company profile." },
  { step: "2", title: "Connect Systems", description: "Link Tally, bank accounts (via AA), GSTN, and EPFO. One-click OAuth or bridge setup." },
  { step: "3", title: "Configure Agents", description: "Assign GST, TDS, Recon, and Close agents. Set thresholds, approval chains, and auto-file preferences." },
  { step: "4", title: "Go Live", description: "Agents start working in shadow mode first. Review outputs, then promote to active. Full automation in under a week." },
];

const TRUST_LOGOS = [
  { name: "Tally", abbr: "TL" },
  { name: "GSTN", abbr: "GS" },
  { name: "EPFO", abbr: "EP" },
  { name: "HDFC Bank", abbr: "HD" },
  { name: "SBI", abbr: "SB" },
  { name: "Adaequare", abbr: "AD" },
];

/* ------------------------------------------------------------------ */
/*  Demo Modal                                                         */
/* ------------------------------------------------------------------ */
function DemoModal({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ name: "", email: "", firm: "", clients: "" });
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
        body: JSON.stringify({ ...form, source: "ca-firms-solution" }),
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
            <p className="text-slate-600">We will contact you within 24 hours to set up your CA firm trial.</p>
            <button onClick={onClose} className="mt-6 bg-gradient-to-r from-blue-500 to-violet-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-violet-700 transition-all">
              Close
            </button>
          </div>
        ) : (
          <>
            <h3 className="text-xl font-bold text-slate-900 mb-1">Start Your Free Trial</h3>
            <p className="text-sm text-slate-500 mb-6">Get CA Pack access for your firm. No credit card required.</p>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="ca-name" className="block text-sm font-medium text-slate-700 mb-1">Name <span className="text-red-500">*</span></label>
                <input id="ca-name" required type="text" placeholder="Your full name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="ca-email" className="block text-sm font-medium text-slate-700 mb-1">Work Email <span className="text-red-500">*</span></label>
                <input id="ca-email" required type="email" placeholder="you@cafirm.com" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="ca-firm" className="block text-sm font-medium text-slate-700 mb-1">Firm Name</label>
                <input id="ca-firm" type="text" placeholder="Your CA firm name" value={form.firm} onChange={(e) => setForm({ ...form, firm: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="ca-clients" className="block text-sm font-medium text-slate-700 mb-1">Number of Clients</label>
                <select id="ca-clients" value={form.clients} onChange={(e) => setForm({ ...form, clients: e.target.value })} className={fieldClass}>
                  <option value="">Select range</option>
                  <option value="1-5">1-5 clients</option>
                  <option value="6-15">6-15 clients</option>
                  <option value="16-30">16-30 clients</option>
                  <option value="30+">30+ clients</option>
                </select>
              </div>
              {error && <p className="text-sm text-red-600">{error}</p>}
              <button type="submit" disabled={submitting} className="w-full bg-gradient-to-r from-blue-500 to-violet-600 text-white px-6 py-3 rounded-lg text-sm font-semibold hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25 disabled:opacity-60 disabled:cursor-not-allowed">
                {submitting ? "Submitting..." : "Start Free Trial"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  CA Firms Solution Page                                             */
/* ------------------------------------------------------------------ */
export default function CAFirmsSolution() {
  const [showDemo, setShowDemo] = useState(false);

  return (
    <div className="min-h-screen font-sans text-slate-900 antialiased overflow-x-hidden">
      <Helmet>
        <title>AI-Powered Virtual Employees for Chartered Accountant Firms | AgenticOrg</title>
        <meta name="description" content="Manage 20+ clients from one dashboard. Automate GST filing, TDS compliance, bank reconciliation, and month-end close. Built for Indian CA firms." />
        <link rel="canonical" href="https://agenticorg.ai/solutions/ca-firms" />
      </Helmet>

      {/* ============================================================ */}
      {/* NAVBAR                                                        */}
      {/* ============================================================ */}
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
            <button onClick={() => setShowDemo(true)} className="bg-gradient-to-r from-blue-500 to-violet-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25">
              Start Free Trial
            </button>
          </div>
        </div>
      </nav>

      {/* ============================================================ */}
      {/* HERO                                                          */}
      {/* ============================================================ */}
      <section className="relative min-h-screen flex items-center overflow-hidden bg-slate-900">
        <div className="absolute inset-0">
          <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900" />
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-blue-500/20 rounded-full blur-3xl animate-pulse" />
          <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
        </div>
        <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: "linear-gradient(rgba(255,255,255,.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.1) 1px, transparent 1px)", backgroundSize: "64px 64px" }} />

        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-16">
          <div className="max-w-4xl mx-auto text-center">
            <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
              <span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
              <span className="text-slate-300 text-sm">Built for Indian CA Firms</span>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-white leading-tight tracking-tight">
              AI-Powered Virtual Employees for{" "}
              <span className="bg-gradient-to-r from-orange-400 via-white to-green-400 bg-clip-text text-transparent">
                Chartered Accountant Firms
              </span>
            </h1>

            <p className="mt-6 text-lg sm:text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
              Manage 20+ clients from one dashboard. Automate GST filing, TDS compliance, bank reconciliation, and month-end close.
            </p>

            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-violet-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25"
              >
                Start Free Trial
              </button>
              <Link
                to="/login?demo=true"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
              >
                Try Demo
              </Link>
            </div>

            <p className="mt-4 text-sm text-slate-500">No credit card required &middot; 14-day free trial &middot; Cancel anytime</p>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* PAIN POINTS                                                   */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16 max-w-3xl mx-auto">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 leading-tight">
                Your firm is losing time and money to manual compliance work
              </h2>
              <p className="mt-4 text-lg text-slate-500">
                These numbers are typical for a mid-size CA practice with 10-20 clients.
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

      {/* ============================================================ */}
      {/* FEATURES GRID                                                 */}
      {/* ============================================================ */}
      <section className="py-24 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Everything Your CA Firm Needs</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Six AI-powered modules that cover the complete compliance lifecycle for every client.
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

      {/* ============================================================ */}
      {/* HOW IT WORKS                                                  */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-900">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
                <span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
                <span className="text-slate-300 text-sm">Go Live in Under a Week</span>
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-white">How It Works</h2>
              <p className="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">
                Four simple steps from signup to full automation.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-4 gap-6">
            {HOW_IT_WORKS.map((s, i) => (
              <FadeIn key={s.step} delay={i * 100}>
                <div className="relative bg-slate-800/50 border border-slate-700 rounded-xl p-6 hover:border-blue-500/50 transition-colors h-full">
                  <div className="w-10 h-10 rounded-full bg-blue-500/20 text-blue-400 flex items-center justify-center text-lg font-bold mb-4">{s.step}</div>
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

      {/* ============================================================ */}
      {/* PRICING CTA                                                   */}
      {/* ============================================================ */}
      <section className="py-24 bg-white">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="bg-gradient-to-br from-blue-50 via-white to-purple-50 rounded-3xl border border-slate-200 p-8 sm:p-12 text-center">
              <div className="inline-flex items-center gap-2 bg-blue-100 text-blue-700 rounded-full px-4 py-1.5 text-sm font-medium mb-6">
                CA Pack &mdash; Paid Add-on
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 mb-4">
                <span className="text-5xl sm:text-6xl font-extrabold bg-gradient-to-r from-blue-600 to-violet-600 bg-clip-text text-transparent">
                  &#8377;4,999
                </span>
                <span className="text-lg text-slate-500 font-normal">/month per client</span>
              </h2>
              <p className="text-sm text-slate-500 mb-2">Separate paid add-on for your AgenticOrg subscription</p>
              <p className="text-lg text-slate-600 mb-2 max-w-xl mx-auto">
                Includes all 6 modules: Multi-Client Management, GST Compliance, TDS Automation, Bank Reconciliation, Month-End Close, and Audit Trail.
              </p>
              <p className="text-sm font-medium text-emerald-600 mb-4">14-day free trial included</p>
              <ul className="grid sm:grid-cols-2 gap-3 mb-8 max-w-lg mx-auto text-left">
                {[
                  "Unlimited agent runs",
                  "Up to 50 clients",
                  "Tally + GSTN integration",
                  "Account Aggregator",
                  "DSC digital signing",
                  "SOC2 audit trail",
                  "Priority email support",
                  "14-day free trial",
                  "Partner self-approval",
                  "Bulk filing approval",
                  "Compliance calendar with alerts",
                  "GSTN credential vault",
                  "Partner dashboard",
                ].map((item) => (
                  <li key={item} className="flex items-center gap-2 text-sm text-slate-700">
                    <CheckIcon className="w-4 h-4 text-emerald-500 flex-shrink-0" />
                    {item}
                  </li>
                ))}
              </ul>
              <button
                onClick={() => setShowDemo(true)}
                className="inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-violet-600 text-white px-10 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25"
              >
                Start Free Trial
              </button>
              <p className="mt-3 text-sm text-slate-500">No credit card required. Cancel anytime.</p>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* TRUST / BUILT FOR INDIA                                       */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="bg-gradient-to-br from-orange-50 via-white to-green-50 rounded-3xl border border-slate-200 overflow-hidden">
              <div className="p-8 sm:p-12">
                <div className="text-center mb-12">
                  <div className="inline-flex items-center gap-2 bg-orange-100 text-orange-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
                    Built for Indian CA Firms
                  </div>
                  <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">
                    Trusted Integrations
                  </h2>
                  <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                    Native integrations with India&apos;s most critical government and business platforms. Production-tested and compliance-ready.
                  </p>
                </div>

                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {TRUST_LOGOS.map((c, i) => (
                    <FadeIn key={c.name} delay={i * 75}>
                      <div className="flex items-center gap-4 bg-white/80 rounded-xl px-5 py-4 border border-slate-100 hover:shadow-md transition-all duration-300">
                        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-orange-400 to-green-500 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
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

      {/* ============================================================ */}
      {/* FINAL CTA                                                     */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-900">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <FadeIn>
            <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
              Stop doing compliance manually. Let AI agents handle it.
            </h2>
            <p className="text-lg text-slate-400 mb-10">
              Join CA firms that have cut month-end close from 5 days to 4 hours.
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-violet-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25"
              >
                Start Free Trial
              </button>
              <Link
                to="/signup"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
              >
                Sign Up Free
              </Link>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* FOOTER                                                        */}
      {/* ============================================================ */}
      <footer className="bg-slate-950 border-t border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-8 mb-12">
            <div>
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm">AO</div>
                <span className="text-white font-semibold">AgenticOrg</span>
              </div>
              <p className="text-sm text-slate-400 leading-relaxed">AI-Powered Virtual Employees for Chartered Accountant Firms.</p>
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
                <li><Link to="/dashboard/companies" className="text-slate-400 hover:text-white text-sm transition-colors">Companies</Link></li>
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
