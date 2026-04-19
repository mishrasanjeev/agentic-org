import { useState, useRef, useCallback, useEffect, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import ROICalculator from "../components/ROICalculator";
import AgentActivityTicker from "../components/AgentActivityTicker";
import AgentsInAction from "../components/AgentsInAction";
import WorkflowAnimation from "../components/WorkflowAnimation";
import InteractiveDemo from "../components/InteractiveDemo";
import SocialProof from "../components/SocialProof";
import { useProductFacts } from "@/lib/productFacts";

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

/* Wrapper component that fades children in on scroll */
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
/*  Browser Chrome Frame — wraps iframes in a macOS-style window       */
/* ------------------------------------------------------------------ */
function BrowserFrame({ src, title, alt, className = "", loading = "lazy" }: {
  src: string;
  title: string;
  alt?: string;
  className?: string;
  loading?: "lazy" | "eager";
}) {
  return (
    <div className={`rounded-xl overflow-hidden shadow-2xl border border-slate-700 bg-slate-800 ${className}`}>
      {/* Title bar */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 border-b border-slate-700">
        <div className="flex gap-1.5">
          <div className="w-3 h-3 rounded-full bg-red-500/80" />
          <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
          <div className="w-3 h-3 rounded-full bg-green-500/80" />
        </div>
        <div className="flex-1 mx-4">
          <div className="bg-slate-700/60 rounded-md px-3 py-1 text-xs text-slate-400 text-center truncate">
            {title}
          </div>
        </div>
      </div>
      {/* Content — static screenshot */}
      <img
        src={src}
        alt={alt || title}
        className="w-full h-auto block"
        loading={loading}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const PAIN_STATS = [
  { value: "72 hours", label: "Average month-end close cycle", color: "text-red-500" },
  { value: "\u20B912L/year", label: "Lost to missed early-payment discounts", color: "text-orange-500" },
  { value: "40%", label: "Of support tickets mis-routed on first attempt", color: "text-red-500" },
];

const ROLE_CARDS = [
  {
    role: "CFO",
    gradient: "from-emerald-500 to-teal-600",
    pain: "Close the books in 1 day, not 5",
    description: "10 finance agents handle AP, AR, Treasury, Expense Management, Bank Reconciliation, Tax Filing, Rev Rec, Fixed Assets, Month-end Close, and FP&A.",
    agents: ["Accounts Payable", "Accounts Receivable", "Treasury", "Reconciliation", "Tax Filing", "Month-end Close", "FP&A", "Expense Mgmt", "Rev Rec", "Fixed Assets"],
    metric: "Shadow mode measures exact ROI with your real data before go-live",
  },
  {
    role: "CHRO",
    gradient: "from-blue-500 to-cyan-500",
    pain: "Onboard in hours, not weeks",
    description: "6 HR agents manage Onboarding, Payroll (847 employees), Talent Acquisition, Performance Reviews, L&D, and Offboarding.",
    agents: ["Onboarding", "Payroll", "Talent Acquisition", "Performance", "L&D", "Offboarding"],
    metric: "Zero payroll errors with automated PF/ESI/TDS",
  },
  {
    role: "CMO",
    gradient: "from-blue-600 to-emerald-500",
    pain: "Launch campaigns while you sleep",
    description: "9 marketing agents run Campaign Management, A/B Testing, Email Drip Sequences, ABM with Intent Data (Bombora/G2/TrustRadius), Content Generation, SEO Optimization, CRM Nurturing, Brand Monitoring, and Competitive Intel.",
    agents: ["Campaign Mgmt", "A/B Testing", "Email Drip", "ABM + Intent", "Content Gen", "SEO", "CRM Nurture", "Brand Monitor", "Competitive Intel"],
    metric: "A/B auto-winner selection + CMO override on multi-channel campaigns",
  },
  {
    role: "COO",
    gradient: "from-orange-500 to-red-600",
    pain: "Resolve P1s before the CEO asks",
    description: "5 ops agents handle Support Triage (88% auto-classify), IT Ops, Compliance Guard (98.5%), Contract Intelligence, and Vendor Management.",
    agents: ["Support Triage", "IT Ops", "Compliance", "Contracts", "Vendor Mgmt"],
    metric: "42 tickets triaged per day with zero manual routing",
  },
];

const INDIA_CONNECTORS = [
  { name: "GSTN", desc: "GST filing & reconciliation" },
  { name: "EPFO", desc: "Provident fund compliance" },
  { name: "Darwinbox", desc: "HR management platform" },
  { name: "Pine Labs Plural", desc: "Payment processing" },
  { name: "Tally", desc: "Accounting integration" },
  { name: "DigiLocker", desc: "Document verification" },
];

/* ------------------------------------------------------------------ */
/*  Check icon reused across sections                                  */
/* ------------------------------------------------------------------ */
function CheckIcon({ className = "w-4 h-4 text-emerald-500" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  DemoModal — Book-a-Demo form overlay                               */
/* ------------------------------------------------------------------ */
function DemoModal({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ name: "", email: "", company: "", role: "", phone: "" });
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  // Close on Escape key
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
        body: JSON.stringify(form),
      });
      if (!res.ok) throw new Error("Request failed");
      import("@/components/Analytics").then(m => m.trackEvent("demo_request", { company: form.company || "", role: form.role || "" })).catch(() => {});
      setDone(true);
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const fieldClass =
    "w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 outline-none transition-all duration-200 ease-out-quart";

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/70 backdrop-blur-sm px-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="demo-modal-title"
    >
      <div className="relative w-full max-w-md rounded-2xl bg-white shadow-2xl p-8 animate-in fade-in zoom-in" role="document">
        {/* Close button */}
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
              <svg className="w-8 h-8 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h3 className="text-xl font-bold text-slate-900 mb-2">Thanks!</h3>
            <p className="text-slate-600">We'll contact you within 24 hours.</p>
            <button
              onClick={onClose}
              className="mt-6 inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-cyan-600 transition-all duration-200 ease-out-quart"
            >
              Close
            </button>
          </div>
        ) : (
          <>
            <h3 id="demo-modal-title" className="text-xl font-bold text-slate-900 mb-1">Book a Demo</h3>
            <p className="text-sm text-slate-500 mb-6">See AgenticOrg in action. Fill out the form and we'll schedule a call.</p>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="demo-name" className="block text-sm font-medium text-slate-700 mb-1">Name <span className="text-red-500">*</span></label>
                <input
                  id="demo-name"
                  required
                  type="text"
                  placeholder="Your full name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className={fieldClass}
                />
              </div>

              <div>
                <label htmlFor="demo-email" className="block text-sm font-medium text-slate-700 mb-1">Work Email <span className="text-red-500">*</span></label>
                <input
                  id="demo-email"
                  required
                  type="email"
                  placeholder="you@company.com"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  className={fieldClass}
                />
              </div>

              <div>
                <label htmlFor="demo-company" className="block text-sm font-medium text-slate-700 mb-1">Company</label>
                <input
                  id="demo-company"
                  type="text"
                  placeholder="Company name"
                  value={form.company}
                  onChange={(e) => setForm({ ...form, company: e.target.value })}
                  className={fieldClass}
                />
              </div>

              <div>
                <label htmlFor="demo-role" className="block text-sm font-medium text-slate-700 mb-1">Role</label>
                <select
                  id="demo-role"
                  value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                  className={fieldClass}
                >
                  <option value="">Select your role</option>
                  <option value="CEO">CEO</option>
                  <option value="CFO">CFO</option>
                  <option value="CHRO">CHRO</option>
                  <option value="CMO">CMO</option>
                  <option value="COO">COO</option>
                  <option value="CTO">CTO</option>
                  <option value="Other">Other</option>
                </select>
              </div>

              <div>
                <label htmlFor="demo-phone" className="block text-sm font-medium text-slate-700 mb-1">Phone</label>
                <input
                  id="demo-phone"
                  type="tel"
                  placeholder="+91 98765 43210"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  className={fieldClass}
                />
              </div>

              {error && <p className="text-sm text-red-600">{error}</p>}

              <button
                type="submit"
                disabled={submitting}
                className="w-full bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-6 py-3 rounded-lg text-sm font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {submitting ? "Submitting..." : "Request Demo"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Landing Page                                                       */
/* ------------------------------------------------------------------ */
export default function Landing() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [showDemo, setShowDemo] = useState(false);
  const location = useLocation();
  const { facts } = useProductFacts();
  const connectorsText = facts.connector_count > 0 ? `${facts.connector_count}` : "50+";
  const agentsText = facts.agent_count > 0 ? `${facts.agent_count}` : "25+";
  const toolsText = facts.tool_count > 0 ? `${facts.tool_count}+` : "250+";
  const versionText = facts.version ? `v${facts.version}` : "";

  // Scroll to hash anchor on navigation (e.g. /#developers from another page)
  useEffect(() => {
    if (location.hash) {
      const el = document.querySelector(location.hash);
      if (el) {
        setTimeout(() => el.scrollIntoView({ behavior: "smooth" }), 100);
      }
    }
  }, [location.hash]);

  const closeMobile = useCallback(() => setMobileMenuOpen(false), []);

  return (
    <div className="min-h-screen font-sans text-slate-900 antialiased overflow-x-hidden">
      <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[100] focus:bg-blue-600 focus:text-white focus:px-4 focus:py-2 focus:rounded-lg">Skip to main content</a>
      <Helmet>
        <title>AgenticOrg — AI Virtual Employees for Enterprise | Create & Deploy AI Agents</title>
        <meta name="description" content="AI agents that reason AND act — pre-built agents across 6 domains, native connectors + 1000+ via Composio, CFO/CMO/ABM dashboards, A/B testing, email drip, NL Query (Cmd+K), scheduled reports. Create Jira tickets, read HubSpot CRM, file GST returns via real API calls. Human-in-the-loop governance. Start free." />
        <link rel="canonical" href="https://agenticorg.ai/" />
      </Helmet>

      {/* ============================================================ */}
      {/* 1. NAVBAR                                                     */}
      {/* ============================================================ */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-slate-900/90 backdrop-blur-md border-b border-slate-700/50" aria-label="Main navigation">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-sm">
              AO
            </div>
            <span className="text-white font-semibold text-lg">AgenticOrg</span>
          </div>

          {/* Desktop links */}
          <div className="hidden md:flex items-center gap-8">
            <a href="#platform" className="text-slate-300 hover:text-white text-sm transition-colors">Platform</a>
            <a href="#solutions" className="text-slate-300 hover:text-white text-sm transition-colors">Solutions</a>
            <Link to="/pricing" className="text-slate-300 hover:text-white text-sm transition-colors">Pricing</Link>
            <Link to="/playground" className="text-slate-300 hover:text-white text-sm transition-colors">Playground</Link>
            <Link to="/blog" className="text-slate-300 hover:text-white text-sm transition-colors">Blog</Link>
            <a href="#how-it-works" className="text-slate-300 hover:text-white text-sm transition-colors">Resources</a>
            <a href="#developers" className="text-slate-300 hover:text-white text-sm transition-colors">Developers</a>
          </div>

          {/* Right CTAs */}
          <div className="flex items-center gap-3">
            <Link
              to="/login"
              className="hidden sm:inline-flex border border-slate-500 text-slate-300 hover:text-white hover:border-white px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ease-out-quart"
            >
              Sign In
            </Link>
            <button
              onClick={() => setShowDemo(true)}
              className="hidden sm:inline-flex bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-5 py-2 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25"
            >
              Book a Demo
            </button>
            <button onClick={() => setMobileMenuOpen(!mobileMenuOpen)} className="md:hidden text-white p-2" aria-label="Toggle navigation menu" aria-expanded={mobileMenuOpen}>
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                {mobileMenuOpen
                  ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  : <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />}
              </svg>
            </button>
          </div>
        </div>

        {/* Mobile dropdown */}
        {mobileMenuOpen && (
          <div className="md:hidden bg-slate-900 border-t border-slate-700/50 px-4 py-4 space-y-3" role="navigation" aria-label="Mobile navigation">
            <a href="#platform" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Platform</a>
            <a href="#solutions" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Solutions</a>
            <Link to="/pricing" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Pricing</Link>
            <Link to="/playground" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Playground</Link>
            <Link to="/blog" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Blog</Link>
            <a href="#how-it-works" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Resources</a>
            <a href="#developers" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Developers</a>
            <Link to="/login" onClick={closeMobile} className="block border border-slate-500 text-slate-300 px-4 py-2 rounded-lg text-sm font-medium text-center mt-2">Sign In</Link>
            <button onClick={() => { closeMobile(); setShowDemo(true); }} className="block w-full bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-4 py-2 rounded-lg text-sm font-medium text-center">Book a Demo</button>
          </div>
        )}
      </nav>

      {/* ============================================================ */}
      {/* 2. HERO                                                       */}
      {/* ============================================================ */}
      <section id="main-content" className="relative min-h-screen flex items-center overflow-hidden bg-slate-900">
        {/* Animated bg */}
        <div className="absolute inset-0">
          <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900" />
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-blue-500/20 rounded-full blur-3xl animate-pulse" />
          <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
        </div>

        {/* Grid overlay */}
        <div
          className="absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage: "linear-gradient(rgba(255,255,255,.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.1) 1px, transparent 1px)",
            backgroundSize: "64px 64px",
          }}
        />

        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-16 grid lg:grid-cols-2 gap-12 items-center">
          {/* Left — Copy */}
          <div>
            {/* Badge */}
            <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
              <span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
              <span className="text-slate-300 text-sm" data-testid="landing-version-badge">
                {versionText ? `${versionText} — ` : ""}1000+ Integrations, {agentsText} AI Agents, Voice, Knowledge Base, Industry Packs
              </span>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-white leading-tight tracking-tight">
              Your Back Office{" "}
              <span className="bg-gradient-to-r from-blue-400 via-purple-400 to-emerald-400 bg-clip-text text-transparent">
                Runs Itself.
              </span>
            </h1>

            <p className="mt-6 text-lg text-slate-400 max-w-xl leading-relaxed">
              Name them. Train them. Deploy them. AI virtual employees that process invoices, run payroll, launch campaigns, and resolve incidents
              &mdash; with human approval on every critical decision.
            </p>

            {/* CTAs */}
            <div className="mt-8 flex flex-col sm:flex-row items-start gap-4">
              <Link
                to="/signup"
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25"
              >
                Start Free →
              </Link>
              <a
                href="#demo"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all duration-200 ease-out-quart"
              >
                Watch Demo
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                </svg>
              </a>
            </div>

            <p className="mt-4 text-sm text-slate-500">
              No credit card required &middot; Free to start &middot; Full audit trail built in
            </p>

            <div className="mt-6 flex flex-wrap items-center gap-4 text-sm text-slate-400">
              <span className="flex items-center gap-1.5"><svg className="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg> Upload your org chart CSV</span>
              <span className="flex items-center gap-1.5"><svg className="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg> Agents match your hierarchy</span>
              <span className="flex items-center gap-1.5"><svg className="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg> Human approval on every critical decision</span>
            </div>
          </div>

          {/* Right — Live agent activity ticker */}
          <div className="hidden lg:block">
            <AgentActivityTicker />
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 2b. VERSION RELEASE BANNER                                    */}
      {/* ============================================================ */}
      <section className="relative bg-slate-900 py-4 px-4 sm:px-6 lg:px-8 overflow-hidden">
        <div className="max-w-5xl mx-auto">
          <Link
            to="/pricing"
            className="group relative block rounded-2xl p-[1px] bg-gradient-to-r from-blue-500 via-purple-500 to-blue-500 bg-[length:200%_100%] animate-[shimmer_3s_linear_infinite] overflow-hidden"
          >
            <div className="relative flex flex-col sm:flex-row items-center gap-3 sm:gap-6 rounded-[15px] bg-slate-900/95 backdrop-blur px-5 py-3">
              {/* Version badge */}
              <span className="shrink-0 inline-flex items-center gap-1.5 bg-gradient-to-r from-blue-500 to-cyan-500 text-white text-xs font-bold px-3 py-1 rounded-full shadow-lg shadow-blue-500/30 animate-pulse" data-testid="landing-version-pill">
                <span className="w-1.5 h-1.5 bg-white rounded-full" />
                {versionText || "v…"}
              </span>

              {/* Description */}
              <p className="text-sm text-slate-300 text-center sm:text-left leading-snug">
                <span className="font-semibold text-white">Project Apex</span>
                {" "}&mdash; 1000+ Integrations, Voice Agents, Knowledge Base, Smart LLM Routing, and Industry Packs
              </p>

              {/* CTA */}
              <span className="shrink-0 text-sm font-semibold text-blue-400 group-hover:text-blue-300 transition-colors whitespace-nowrap">
                See What&apos;s New&nbsp;&rarr;
              </span>
            </div>
          </Link>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 3. LOGO BAR                                                   */}
      {/* ============================================================ */}
      <section className="py-10 bg-white border-b border-slate-100">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-6 text-center">
            <div className="bg-slate-50 rounded-xl p-4">
              <div className="text-3xl font-extrabold text-slate-900">50+</div>
              <p className="text-sm text-slate-500 mt-1">Pre-built AI Agents</p>
            </div>
            <div className="bg-slate-50 rounded-xl p-4">
              <div className="text-3xl font-extrabold text-slate-900">1000+</div>
              <p className="text-sm text-slate-500 mt-1">Connectors &amp; Tools</p>
            </div>
            <div className="bg-slate-50 rounded-xl p-4">
              <div className="text-3xl font-extrabold text-slate-900">20+</div>
              <p className="text-sm text-slate-500 mt-1">Workflows</p>
            </div>
            <div className="bg-slate-50 rounded-xl p-4">
              <div className="text-3xl font-extrabold text-emerald-600">4</div>
              <p className="text-sm text-slate-500 mt-1">Industry Packs</p>
            </div>
            <div className="bg-slate-50 rounded-xl p-4">
              <div className="text-3xl font-extrabold text-blue-600">100%</div>
              <p className="text-sm text-slate-500 mt-1">E2E Tests Passing</p>
            </div>
          </div>
          <p className="text-center text-xs text-slate-400 mt-4">Connects with SAP, Oracle, Salesforce, GSTN, EPFO, Darwinbox, Tally, Slack, Jira, HubSpot, Stripe & more</p>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 4. PROBLEM SECTION                                            */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-50 scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16 max-w-3xl mx-auto">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 leading-tight">
                Your finance team spends 5 days closing the books. Your HR manually onboards every hire. Your ops team drowns in tickets.
              </h2>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-3 gap-8">
            {PAIN_STATS.map((s, i) => (
              <FadeIn key={s.value} delay={i * 150}>
                <div className="bg-white rounded-2xl p-8 border border-slate-200 text-center hover:shadow-lg transition-all duration-300 ease-out-quart">
                  <div className={`text-4xl sm:text-5xl font-extrabold ${s.color} mb-3`}>{s.value}</div>
                  <p className="text-slate-600">{s.label}</p>
                </div>
              </FadeIn>
            ))}
          </div>

          <FadeIn>
            <div className="text-center mt-12">
              <a href="#platform" className="inline-flex items-center gap-2 text-blue-600 font-semibold hover:text-blue-700 transition-colors">
                See how AgenticOrg fixes this
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                </svg>
              </a>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 4b. AGENTS IN ACTION — Animated Virtual Employees             */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-900 scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-12">
              <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
                <span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
                <span className="text-slate-300 text-sm">Virtual Employees at Work</span>
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-white">Watch Your AI Team in Action</h2>
              <p className="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">
                Each virtual employee has a name, a specialization, and tailored instructions. Click any card to see them work.
              </p>
            </div>
          </FadeIn>

          <FadeIn delay={200}>
            <AgentsInAction />
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 5. PLATFORM OVERVIEW                                          */}
      {/* ============================================================ */}
      <section id="platform" className="py-24 bg-white scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">One Platform. Unlimited AI Employees. Complete Automation.</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                {agentsText} pre-built agents across 6 domains that reason with Gemini AND execute real actions &mdash; creating Jira tickets, reading CRM data, querying repos. {connectorsText} native connectors + 1000+ via Composio ({toolsText} native tools), A/B testing, email drip, ABM with intent data, SDK/MCP/API access. Not chatbots. Virtual employees.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-8">
            {/* Agent Fleet */}
            <FadeIn delay={0}>
              <div className="space-y-4">
                <BrowserFrame
                  src="/screenshots/agents.webp"
                  title="app.agenticorg.ai/dashboard/agents"
                  alt="Agent fleet management view — AI agents across Finance, HR, Marketing, and Ops"
                />
                <h3 className="text-xl font-bold text-slate-900">Agent Fleet</h3>
                <p className="text-slate-600 text-sm leading-relaxed">
                  50+ pre-built agents across Finance, HR, Marketing, Ops, and Back Office &mdash; each connected to real tools (Jira, HubSpot, GitHub). Create custom virtual employees with names, personas, and tool access.
                </p>
              </div>
            </FadeIn>

            {/* Live Observatory */}
            <FadeIn delay={150}>
              <div className="space-y-4">
                <BrowserFrame
                  src="/screenshots/observatory.webp"
                  title="app.agenticorg.ai/dashboard/observatory"
                  alt="Live observatory dashboard with real-time agent monitoring, throughput, and error tracking"
                />
                <h3 className="text-xl font-bold text-slate-900">Live Observatory</h3>
                <p className="text-slate-600 text-sm leading-relaxed">
                  Real-time monitoring of every agent action, decision, and escalation. Track throughput, latency, error rates, and cost across your entire agent fleet from a single pane of glass.
                </p>
              </div>
            </FadeIn>

            {/* HITL Approvals */}
            <FadeIn delay={300}>
              <div className="space-y-4">
                <BrowserFrame
                  src="/screenshots/approvals.webp"
                  title="app.agenticorg.ai/dashboard/approvals"
                  alt="Human-in-the-loop approval queue for reviewing and approving critical agent decisions"
                />
                <h3 className="text-xl font-bold text-slate-900">HITL Approvals</h3>
                <p className="text-slate-600 text-sm leading-relaxed">
                  Human-in-the-loop governance for every critical decision. Approve, reject, or override agent actions with full context. No agent acts without your say on high-stakes operations.
                </p>
              </div>
            </FadeIn>

            {/* Agent Creator */}
            <FadeIn delay={450}>
              <div className="space-y-4">
                <div className="rounded-xl overflow-hidden shadow-2xl border border-slate-700 bg-gradient-to-br from-blue-600 to-cyan-600 p-8 flex flex-col items-center justify-center min-h-[200px]">
                  <div className="w-16 h-16 rounded-full bg-white/20 flex items-center justify-center mb-4">
                    <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                  </div>
                  <p className="text-white font-semibold text-center">Create Your Own</p>
                  <p className="text-blue-100 text-xs text-center mt-1">No code required</p>
                </div>
                <h3 className="text-xl font-bold text-slate-900">Agent Creator</h3>
                <p className="text-slate-600 text-sm leading-relaxed">
                  Build custom AI virtual employees in minutes. Give them a name, a role, and tailored instructions through a guided wizard &mdash; no code required. 27 production-tested prompt templates included.
                </p>
              </div>
            </FadeIn>
          </div>

          {/* Org Chart Hierarchy — 3-card row */}
          <div className="grid md:grid-cols-3 gap-8 mt-16">
            <FadeIn delay={0}>
              <div className="bg-gradient-to-br from-slate-50 to-blue-50 rounded-2xl p-6 border border-slate-200 hover:shadow-lg transition-all duration-300 ease-out-quart h-full">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500 to-cyan-600 flex items-center justify-center mb-4">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                </div>
                <h3 className="text-lg font-bold text-slate-900 mb-2">Real Org Chart Hierarchy</h3>
                <p className="text-sm text-slate-600 leading-relaxed">
                  Mirror your company&apos;s department structure with AI agents. Visual tree view at <span className="font-mono text-xs bg-slate-100 px-1 py-0.5 rounded">/dashboard/org-chart</span> shows agent hierarchy per department &mdash; heads, seniors, specialists, and juniors.
                </p>
              </div>
            </FadeIn>

            <FadeIn delay={150}>
              <div className="bg-gradient-to-br from-slate-50 to-emerald-50 rounded-2xl p-6 border border-slate-200 hover:shadow-lg transition-all duration-300 ease-out-quart h-full">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center mb-4">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                  </svg>
                </div>
                <h3 className="text-lg font-bold text-slate-900 mb-2">CSV Bulk Import</h3>
                <p className="text-sm text-slate-600 leading-relaxed">
                  Upload your org chart and create 50+ agents in seconds. A single CSV defines the full hierarchy &mdash; VPs, Directors, Managers, Analysts &mdash; with parent-child relationships and escalation chains built automatically.
                </p>
              </div>
            </FadeIn>

            <FadeIn delay={300}>
              <div className="bg-gradient-to-br from-slate-50 to-teal-50 rounded-2xl p-6 border border-slate-200 hover:shadow-lg transition-all duration-300 ease-out-quart h-full">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-600 to-emerald-500 flex items-center justify-center mb-4">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                  </svg>
                </div>
                <h3 className="text-lg font-bold text-slate-900 mb-2">Smart Escalation</h3>
                <p className="text-sm text-slate-600 leading-relaxed">
                  Agents escalate to their parent agent, then the domain head, then a human &mdash; exactly like your real org. Junior agents defer to seniors, seniors defer to department heads, and department heads trigger HITL approval.
                </p>
              </div>
            </FadeIn>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 5b. GET STARTED IN 3 MINUTES                                 */}
      {/* ============================================================ */}
      <section className="py-24 bg-gradient-to-b from-white to-slate-50 scroll-mt-16">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <div className="inline-flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-full px-4 py-1.5 mb-6">
                <span className="w-2 h-2 bg-emerald-500 rounded-full" />
                <span className="text-emerald-700 text-sm font-medium">Live in under 3 minutes</span>
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">From Org Chart to AI Workforce — 3 Steps</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Give us your department structure. We'll give you AI employees for every role.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-3 gap-0 md:gap-0">
            {/* Step 1 */}
            <FadeIn delay={0}>
              <div className="relative bg-white rounded-2xl md:rounded-r-none border border-slate-200 p-8 h-full">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold text-lg">1</div>
                  <h3 className="text-lg font-bold text-slate-900">Sign Up & Upload</h3>
                </div>
                <p className="text-sm text-slate-600 mb-4">Create your org in 30 seconds. Then upload a CSV with your team structure — names, roles, reporting lines.</p>
                <div className="bg-slate-50 rounded-lg p-3 font-mono text-xs text-slate-500 border border-slate-100">
                  <div>name,role,domain,reports_to</div>
                  <div className="text-slate-700">VP Finance,fpa_agent,finance,</div>
                  <div className="text-slate-700">Priya,ap_processor,finance,VP Finance</div>
                  <div className="text-slate-700">Arjun,ap_processor,finance,VP Finance</div>
                </div>
                <Link to="/signup" className="inline-flex items-center gap-1 mt-4 text-sm font-semibold text-blue-600 hover:text-blue-700">
                  Create Free Account →
                </Link>
              </div>
            </FadeIn>

            {/* Step 2 */}
            <FadeIn delay={150}>
              <div className="relative bg-white border-y md:border border-slate-200 p-8 h-full">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-full bg-purple-100 flex items-center justify-center text-purple-700 font-bold text-lg">2</div>
                  <h3 className="text-lg font-bold text-slate-900">See Your Org Chart</h3>
                </div>
                <p className="text-sm text-slate-600 mb-4">Agents appear in a visual org chart — just like your real company. Hierarchy, escalation chains, department views.</p>
                <div className="bg-slate-900 rounded-lg p-4 text-center">
                  <div className="inline-block bg-emerald-500/20 border border-emerald-500/40 rounded-lg px-3 py-1.5 text-xs text-emerald-400 mb-2">VP Finance</div>
                  <div className="w-px h-4 bg-slate-600 mx-auto" />
                  <div className="flex gap-3 justify-center">
                    <div className="bg-blue-500/20 border border-blue-500/40 rounded-lg px-2 py-1 text-[10px] text-blue-400">Priya (AP)</div>
                    <div className="bg-blue-500/20 border border-blue-500/40 rounded-lg px-2 py-1 text-[10px] text-blue-400">Arjun (AP)</div>
                  </div>
                </div>
                <Link to="/login" className="inline-flex items-center gap-1 mt-4 text-sm font-semibold text-purple-600 hover:text-purple-700">
                  Try Demo Dashboard →
                </Link>
              </div>
            </FadeIn>

            {/* Step 3 */}
            <FadeIn delay={300}>
              <div className="relative bg-white rounded-2xl md:rounded-l-none border border-slate-200 p-8 h-full">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-full bg-emerald-100 flex items-center justify-center text-emerald-700 font-bold text-lg">3</div>
                  <h3 className="text-lg font-bold text-slate-900">Agents Start Working</h3>
                </div>
                <p className="text-sm text-slate-600 mb-4">Agents start in Shadow Mode — observe, learn, validate. Promote to Active when ready. You approve every critical decision.</p>
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-xs"><span className="w-2 h-2 rounded-full bg-yellow-500" /><span className="text-slate-600">Shadow: Agent observes, humans do the work</span></div>
                  <div className="flex items-center gap-2 text-xs"><span className="w-2 h-2 rounded-full bg-emerald-500" /><span className="text-slate-600">Active: Agent works, humans approve decisions</span></div>
                  <div className="flex items-center gap-2 text-xs"><span className="w-2 h-2 rounded-full bg-blue-500" /><span className="text-slate-600">Escalation: Junior → Senior → Head → Human</span></div>
                </div>
                <Link to="/playground" className="inline-flex items-center gap-1 mt-4 text-sm font-semibold text-emerald-600 hover:text-emerald-700">
                  Try in Playground →
                </Link>
              </div>
            </FadeIn>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 6. ROLE-SPECIFIC SECTIONS                                     */}
      {/* ============================================================ */}
      <section id="solutions" className="py-24 bg-slate-50 scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Built for Every Department Head</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Whether you run finance, HR, marketing, or operations &mdash; AgenticOrg has agents purpose-built for your biggest headaches.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-2 gap-8">
            {ROLE_CARDS.map((card, i) => (
              <FadeIn key={card.role} delay={i * 100}>
                <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden hover:shadow-lg transition-all duration-300 ease-out-quart h-full">
                  <div className={`h-2 bg-gradient-to-r ${card.gradient}`} />
                  <div className="p-8">
                    <div className="flex items-center gap-3 mb-4">
                      <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${card.gradient} flex items-center justify-center text-white font-bold text-sm`}>
                        {card.role}
                      </div>
                      <div>
                        <h3 className="text-lg font-bold text-slate-900">{card.role}</h3>
                        <p className="text-sm text-slate-500">{card.pain}</p>
                      </div>
                    </div>

                    <p className="text-slate-600 text-sm leading-relaxed mb-4">{card.description}</p>

                    <div className="flex flex-wrap gap-2 mb-4">
                      {card.agents.map((a) => (
                        <span key={a} className="bg-slate-100 text-slate-600 px-2.5 py-1 rounded-full text-xs font-medium">
                          {a}
                        </span>
                      ))}
                    </div>

                    <div className="flex items-center gap-2 bg-emerald-50 rounded-lg px-4 py-3">
                      <CheckIcon className="w-5 h-5 text-emerald-600 flex-shrink-0" />
                      <span className="text-sm font-medium text-emerald-800">{card.metric}</span>
                    </div>

                    <Link
                      to="/login"
                      className="inline-flex items-center gap-1 mt-4 text-sm font-semibold text-blue-600 hover:text-blue-700 transition-colors"
                    >
                      See {card.role} Dashboard
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                      </svg>
                    </Link>
                  </div>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 7. HOW IT WORKS                                               */}
      {/* ============================================================ */}
      <section id="how-it-works" className="py-24 bg-white scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Three Steps to Autonomous Operations</h2>
            </div>
          </FadeIn>

          <div className="relative max-w-4xl mx-auto">
            {/* Connecting line (desktop) */}
            <div className="hidden md:block absolute top-12 left-[calc(16.67%+24px)] right-[calc(16.67%+24px)] h-0.5 bg-slate-200" />

            <div className="grid md:grid-cols-3 gap-12">
              {[
                { num: "1", title: "Create or pick your agents", desc: `Choose from ${agentsText} pre-built agents, or create custom AI virtual employees with names, specializations, and tool access — all through a guided wizard.` },
                { num: "2", title: "Connect your systems", desc: `${connectorsText} connectors with ${toolsText} tools — SAP, Oracle, Jira, HubSpot, GitHub, GSTN, Darwinbox, Slack, Salesforce, and more. Configure auth, secrets, and health checks from the UI. Trigger workflows on email, schedule, webhook, or API events.` },
                { num: "3", title: "Agents work, you approve", desc: "Agents reason with Gemini, execute tool calls, then return results. You approve critical decisions via HITL governance. Access from dashboard, Python SDK (pip install agenticorg), TypeScript SDK (npm i agenticorg-sdk), CLI, or ChatGPT/Claude via MCP Server." },
              ].map((step, i) => (
                <FadeIn key={step.num} delay={i * 150}>
                  <div className="text-center">
                    <div className="relative z-10 w-12 h-12 rounded-full bg-gradient-to-br from-blue-500 to-cyan-500 flex items-center justify-center text-white font-bold text-lg mx-auto mb-6 shadow-lg shadow-blue-500/25">
                      {step.num}
                    </div>
                    <h3 className="text-lg font-bold text-slate-900 mb-3">{step.title}</h3>
                    <p className="text-slate-600 text-sm leading-relaxed">{step.desc}</p>
                  </div>
                </FadeIn>
              ))}
            </div>
          </div>

          {/* Workflow Animation */}
          <FadeIn delay={300}>
            <div className="mt-20">
              <h3 className="text-center text-xl font-bold text-slate-900 mb-2">How an Agent Processes a Task</h3>
              <p className="text-center text-sm text-slate-500 mb-8 max-w-lg mx-auto">Watch a real invoice flow through the agent pipeline &mdash; from arrival to approval.</p>
              <div className="bg-slate-900 rounded-2xl p-8">
                <WorkflowAnimation />
              </div>
            </div>
          </FadeIn>

          {/* Pricing strip */}
          <FadeIn>
            <div className="mt-16 bg-slate-50 rounded-2xl border border-slate-200 p-8">
              <div className="grid md:grid-cols-3 gap-6 text-center">
                <div>
                  <div className="text-2xl font-bold text-slate-900">Free</div>
                  <p className="text-sm text-slate-500 mt-1">50+ agents, 20 connectors, 500 tasks/day</p>
                  <Link to="/signup" className="inline-flex items-center justify-center mt-3 text-sm font-semibold text-blue-600 hover:text-blue-700">Start Free →</Link>
                </div>
                <div className="border-x border-slate-200 px-6">
                  <div className="text-2xl font-bold text-blue-600">Pro — $499/mo</div>
                  <p className="text-sm text-slate-500 mt-1">Unlimited agents, unlimited tasks, priority support</p>
                  <button onClick={() => setShowDemo(true)} className="inline-flex items-center justify-center mt-3 text-sm font-semibold text-blue-600 hover:text-blue-700">Get Started →</button>
                </div>
                <div>
                  <div className="text-2xl font-bold text-slate-900">Enterprise</div>
                  <p className="text-sm text-slate-500 mt-1">Custom SLA, on-premise, SSO, dedicated support</p>
                  <button onClick={() => setShowDemo(true)} className="inline-flex items-center justify-center mt-3 text-sm font-semibold text-blue-600 hover:text-blue-700">Contact Sales →</button>
                </div>
              </div>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 8. LIVE DEMO                                                  */}
      {/* ============================================================ */}
      <section id="demo" className="py-24 bg-slate-900 scroll-mt-16">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-4">
              <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
                <span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
                <span className="text-slate-300 text-sm">Live Agent Execution</span>
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-white">Watch Agents Think, Execute & Decide</h2>
              <p className="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">
                Real agent reasoning traces. Real tool calls. Real confidence scores. Pick a scenario and watch the full execution pipeline.
              </p>
            </div>
          </FadeIn>

          <FadeIn delay={200}>
            <InteractiveDemo />
          </FadeIn>

          <FadeIn>
            <div className="text-center mt-10 flex flex-col sm:flex-row items-center justify-center gap-4">
              <Link
                to="/playground"
                className="inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25"
              >
                Try It Yourself — Playground
              </Link>
              <Link
                to="/login"
                className="inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all duration-200 ease-out-quart"
              >
                Login to Full Dashboard
              </Link>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 9. TRUST & SECURITY                                           */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-50 scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Enterprise-Grade from Day One</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Security, governance, and compliance are not afterthoughts. They are the foundation.
              </p>
            </div>
          </FadeIn>

          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {[
              {
                icon: (
                  <svg className="w-8 h-8 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                ),
                title: "HITL Governance",
                desc: "Every critical decision requires human approval. Configurable thresholds per agent, per domain, per risk level.",
              },
              {
                icon: (
                  <svg className="w-8 h-8 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                  </svg>
                ),
                title: "Shadow Mode",
                desc: "Test agents in parallel before going live. Compare AI decisions against human decisions with zero production risk.",
              },
              {
                icon: (
                  <svg className="w-8 h-8 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                ),
                title: "Complete Audit Trail",
                desc: "WORM-compliant, 7-year retention, exportable evidence packages. Every agent action logged with full context.",
              },
              {
                icon: (
                  <svg className="w-8 h-8 text-orange-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                ),
                title: "Secure Authentication",
                desc: "Google OAuth, email/password login, secure password reset, org invitations with JWT tokens. Rate-limited, email-enumeration safe.",
              },
            ].map((f, i) => (
              <FadeIn key={f.title} delay={i * 100}>
                <div className="bg-white rounded-2xl p-6 border border-slate-200 hover:border-slate-300 hover:shadow-lg transition-all duration-300 ease-out-quart h-full">
                  <div className="mb-4">{f.icon}</div>
                  <h3 className="font-bold text-slate-900 mb-2">{f.title}</h3>
                  <p className="text-sm text-slate-500 leading-relaxed">{f.desc}</p>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 9b. SOCIAL PROOF / TESTIMONIALS                               */}
      {/* ============================================================ */}
      <FadeIn>
        <SocialProof />
      </FadeIn>

      {/* ============================================================ */}
      {/* 10. ROI CALCULATOR                                            */}
      {/* ============================================================ */}
      <ROICalculator />

      {/* ============================================================ */}
      {/* 11. INDIA-FIRST                                               */}
      {/* ============================================================ */}
      <section className="py-24 bg-white scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="bg-gradient-to-br from-orange-50 via-white to-green-50 rounded-3xl border border-slate-200 overflow-hidden">
              <div className="p-8 sm:p-12">
                <div className="text-center mb-12">
                  <div className="inline-flex items-center gap-2 bg-orange-100 text-orange-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
                    India-First Connectors
                  </div>
                  <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">
                    Built for Indian Enterprise
                  </h2>
                  <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                    Native integrations with India&apos;s most critical government and business platforms.
                    GSTN, EPFO, Darwinbox, Tally &mdash; all pre-built and production-tested.
                  </p>
                </div>

                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {INDIA_CONNECTORS.map((c, i) => (
                    <FadeIn key={c.name} delay={i * 75}>
                      <div className="flex items-center gap-4 bg-white/80 rounded-xl px-5 py-4 border border-slate-100 hover:shadow-md transition-all duration-300">
                        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-orange-400 to-green-500 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
                          {c.name.slice(0, 2)}
                        </div>
                        <div>
                          <h3 className="font-semibold text-slate-900 text-sm">{c.name}</h3>
                          <p className="text-xs text-slate-500">{c.desc}</p>
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
      {/* 11b. FOR CA FIRMS                                             */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-50 scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="bg-gradient-to-br from-blue-50 via-white to-teal-50 rounded-3xl border border-slate-200 overflow-hidden p-8 sm:p-12">
              <div className="grid lg:grid-cols-2 gap-12 items-center">
                <div>
                  <div className="inline-flex items-center gap-2 bg-blue-100 text-blue-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
                    CA Pack &mdash; Paid Add-on
                  </div>
                  <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 mb-4">
                    Built for Chartered Accountant Firms
                  </h2>
                  <p className="text-sm text-slate-500 mb-4">Separate paid add-on &middot; 14-day free trial &middot; INR 4,999/month per client</p>
                  <ul className="space-y-4 mb-8">
                    <li className="flex items-start gap-3">
                      <CheckIcon className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" />
                      <div>
                        <span className="font-semibold text-slate-900">Multi-Client Management</span>
                        <span className="text-slate-600"> &mdash; Manage 20+ clients from one dashboard. Instant company switching with isolated data.</span>
                      </div>
                    </li>
                    <li className="flex items-start gap-3">
                      <CheckIcon className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" />
                      <div>
                        <span className="font-semibold text-slate-900">GST &amp; TDS Automation</span>
                        <span className="text-slate-600"> &mdash; Auto GSTR-1/3B filing, TDS Form 26Q/24Q generation, 2A/26AS reconciliation with GSTN integration.</span>
                      </div>
                    </li>
                    <li className="flex items-start gap-3">
                      <CheckIcon className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" />
                      <div>
                        <span className="font-semibold text-slate-900">Bank Reconciliation</span>
                        <span className="text-slate-600"> &mdash; Auto-match 99.7% of bank transactions via Account Aggregator. Flag stale items and escalate to partners.</span>
                      </div>
                    </li>
                    <li className="flex items-start gap-3">
                      <CheckIcon className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" />
                      <div>
                        <span className="font-semibold text-slate-900">Partner Dashboard</span>
                        <span className="text-slate-600"> &mdash; Aggregate health scores, pending filings, compliance calendar, and revenue tracking across all clients.</span>
                      </div>
                    </li>
                  </ul>
                  <div className="flex flex-col sm:flex-row gap-3">
                    <Link
                      to="/solutions/ca-firms"
                      className="inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25"
                    >
                      Learn More
                    </Link>
                    <Link
                      to="/login?demo=true"
                      className="inline-flex items-center justify-center border border-slate-300 text-slate-700 px-6 py-3 rounded-xl text-sm font-semibold hover:bg-slate-100 transition-all duration-200 ease-out-quart"
                    >
                      Try Demo
                    </Link>
                  </div>
                </div>
                <div className="hidden lg:block">
                  <div className="bg-white rounded-2xl border border-slate-200 shadow-lg p-6 space-y-4">
                    <div className="flex items-center gap-3 mb-4">
                      <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500 to-cyan-500 flex items-center justify-center text-white font-bold text-sm">CA</div>
                      <div>
                        <p className="font-semibold text-slate-900">CA Firm Dashboard</p>
                        <p className="text-xs text-slate-500">7 clients &middot; 3 pending filings &middot; Health: 92%</p>
                      </div>
                    </div>
                    {[
                      { label: "GST Filed", value: "94%", color: "bg-emerald-500" },
                      { label: "Bank Recon", value: "99.7%", color: "bg-blue-500" },
                      { label: "Month Close", value: "4 hrs", color: "bg-purple-500" },
                    ].map((stat) => (
                      <div key={stat.label} className="flex items-center justify-between">
                        <span className="text-sm text-slate-600">{stat.label}</span>
                        <div className="flex items-center gap-2">
                          <div className="w-24 h-2 bg-slate-100 rounded-full overflow-hidden">
                            <div className={`h-full ${stat.color} rounded-full`} style={{ width: stat.value.includes("%") ? stat.value : "80%" }} />
                          </div>
                          <span className="text-sm font-semibold text-slate-900 w-14 text-right">{stat.value}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 11c. BUILT FOR EVERY C-SUITE EXECUTIVE                        */}
      {/* ============================================================ */}
      <section className="py-24 bg-white scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <div className="inline-flex items-center gap-2 bg-slate-100 text-slate-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
                One Platform, Every Function
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">
                Built for Every C-Suite Executive
              </h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Dedicated AI agent teams for every function. Each CxO gets a purpose-built solution with role-specific dashboards, KPIs, and automation workflows.
              </p>
            </div>
          </FadeIn>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-6">
            {[
              {
                role: "CFO",
                title: "Virtual Finance Team",
                description: "AP/AR automation, bank reconciliation, tax compliance, and month-end close in 4 hours.",
                gradient: "from-emerald-500 to-teal-600",
                icon: "M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
                link: "/solutions/cfo",
              },
              {
                role: "CHRO",
                title: "Virtual HR Team",
                description: "Screen 500 resumes/hr, day-1 onboarding, zero-error payroll, and EPFO/ESI compliance.",
                gradient: "from-blue-500 to-teal-600",
                icon: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z",
                link: "/solutions/chro",
              },
              {
                role: "CMO",
                title: "Virtual Marketing Team",
                description: "3.2x ROAS, 42% lower CAC, AI content factory, and multi-channel campaign automation.",
                gradient: "from-emerald-500 to-teal-600",
                icon: "M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z",
                link: "/solutions/cmo",
              },
              {
                role: "COO",
                title: "Virtual Operations Team",
                description: "88% auto-triage, MTTR from 4hr to 15min, vendor SLA monitoring, and compliance guard.",
                gradient: "from-cyan-500 to-blue-600",
                icon: "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z",
                link: "/solutions/coo",
              },
              {
                role: "CBO",
                title: "Virtual Business Ops Team",
                description: "Contract review in 2 days, continuous compliance, fraud detection, and data governance.",
                gradient: "from-amber-500 to-orange-600",
                icon: "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z",
                link: "/solutions/cbo",
              },
            ].map((card, i) => (
              <FadeIn key={card.role} delay={i * 100}>
                <Link
                  to={card.link}
                  className="group block bg-gradient-to-br from-slate-50 to-white rounded-2xl p-6 border border-slate-200 hover:shadow-xl hover:border-slate-300 transition-all duration-300 h-full"
                >
                  <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${card.gradient} flex items-center justify-center mb-4 group-hover:scale-110 transition-transform duration-300`}>
                    <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={card.icon} />
                    </svg>
                  </div>
                  <div className="inline-flex items-center gap-1.5 bg-slate-100 text-slate-700 rounded-full px-3 py-1 text-xs font-semibold mb-3">
                    For {card.role}s
                  </div>
                  <h3 className="text-base font-bold text-slate-900 mb-2">{card.title}</h3>
                  <p className="text-sm text-slate-600 leading-relaxed mb-4">{card.description}</p>
                  <span className="inline-flex items-center gap-1 text-sm font-semibold text-blue-600 group-hover:gap-2 transition-all duration-200 ease-out-quart">
                    Learn More
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </span>
                </Link>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 12. DEVELOPERS / SDK                                          */}
      {/* ============================================================ */}
      <section id="developers" className="py-24 bg-slate-900 scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
                <span className="w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
                <span className="text-slate-300 text-sm">Open-Source SDKs — Apache 2.0</span>
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-white">Build With AgenticOrg</h2>
              <p className="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">
                Python SDK, TypeScript SDK, CLI, and MCP Server. Run AI agents from your code, ChatGPT, Claude, or any MCP-compatible client.
              </p>
            </div>
          </FadeIn>

          {/* SDK Cards */}
          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 mb-16">
            {[
              {
                title: "Python SDK",
                badge: "PyPI",
                install: "pip install agenticorg",
                code: `from agenticorg import AgenticOrg

client = AgenticOrg(api_key="ao_sk_...")
result = client.agents.run(
  "ap_processor",
  inputs={"invoice_id": "INV-001"}
)
print(result.output)`,
                link: "https://pypi.org/project/agenticorg/",
                color: "from-yellow-500 to-yellow-600",
              },
              {
                title: "TypeScript SDK",
                badge: "npm",
                install: "npm i agenticorg-sdk",
                code: `import { AgenticOrg } from "agenticorg-sdk"

const client = new AgenticOrg({
  apiKey: "ao_sk_..."
})
const result = await client.agents.run(
  "recon_agent",
  { inputs: { bank_id: "SBI-001" } }
)`,
                link: "https://www.npmjs.com/package/agenticorg-sdk",
                color: "from-blue-500 to-blue-600",
              },
              {
                title: "CLI",
                badge: "Terminal",
                install: "pip install agenticorg",
                code: `$ agenticorg agents list
$ agenticorg agents run ap_processor \\
    --input invoice_id=INV-001
$ agenticorg sop deploy \\
    --file onboarding.pdf`,
                link: "https://github.com/mishrasanjeev/agentic-org",
                color: "from-emerald-500 to-emerald-600",
              },
              {
                title: "MCP Server",
                badge: "ChatGPT / Claude",
                install: "npx agenticorg-mcp-server",
                code: `// claude_desktop_config.json
{
  "mcpServers": {
    "agenticorg": {
      "command": "npx",
      "args": ["agenticorg-mcp-server"],
      "env": {
        "AGENTICORG_API_KEY": "ao_sk_..."
      }
    }
  }
}`,
                link: "https://www.npmjs.com/package/agenticorg-mcp-server",
                color: "from-blue-500 to-blue-600",
              },
            ].map((sdk, i) => (
              <FadeIn key={sdk.title} delay={i * 100}>
                <div className="bg-slate-800 rounded-2xl border border-slate-700 overflow-hidden h-full flex flex-col">
                  <div className="p-5 flex items-center justify-between border-b border-slate-700">
                    <h3 className="font-bold text-white">{sdk.title}</h3>
                    <span className={`text-xs font-semibold px-2.5 py-1 rounded-full bg-gradient-to-r ${sdk.color} text-white`}>{sdk.badge}</span>
                  </div>
                  <div className="p-4 flex-1 flex flex-col">
                    <div className="bg-slate-900 rounded-lg px-3 py-2 mb-3 flex items-center gap-2">
                      <span className="text-emerald-400 text-xs">$</span>
                      <code className="text-slate-300 text-xs font-mono">{sdk.install}</code>
                    </div>
                    <pre className="bg-slate-950 rounded-lg px-4 py-3 text-xs text-slate-300 font-mono overflow-x-auto flex-1 leading-relaxed"><code>{sdk.code}</code></pre>
                    <a
                      href={sdk.link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 mt-4 text-sm font-semibold text-blue-400 hover:text-blue-300 transition-colors"
                    >
                      View on {sdk.badge === "Terminal" ? "GitHub" : sdk.badge}
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                    </a>
                  </div>
                </div>
              </FadeIn>
            ))}
          </div>

          {/* Integration Protocols Row */}
          <FadeIn>
            <div className="grid md:grid-cols-3 gap-6 mb-12">
              <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6 text-center">
                <div className="w-12 h-12 rounded-full bg-blue-500/20 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-6 h-6 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                </div>
                <h3 className="font-bold text-white mb-2">A2A Protocol</h3>
                <p className="text-sm text-slate-400">Google's Agent-to-Agent protocol. Your agents publish Agent Cards, discovered by external agents automatically.</p>
              </div>
              <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6 text-center">
                <div className="w-12 h-12 rounded-full bg-purple-500/20 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-6 h-6 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>
                </div>
                <h3 className="font-bold text-white mb-2">MCP (Model Context Protocol)</h3>
                <p className="text-sm text-slate-400">Anthropic's MCP. Expose {toolsText} tools to ChatGPT, Claude Desktop, Cursor, Windsurf, or any MCP client.</p>
              </div>
              <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6 text-center">
                <div className="w-12 h-12 rounded-full bg-emerald-500/20 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-6 h-6 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
                </div>
                <h3 className="font-bold text-white mb-2">Grantex Scope Enforcement</h3>
                <p className="text-sm text-slate-400">Manifest-based permission enforcement with offline JWT verification. Every tool call checked against {connectorsText} connector manifests in &lt;1ms. Permission hierarchy: admin &gt; delete &gt; write &gt; read. Now with 1000+ tool manifests, voice agent security, and PII redaction before LLM.</p>
                <Link to="/how-grantex-works" className="inline-flex items-center gap-1 text-sm text-emerald-400 hover:text-emerald-300 font-medium mt-2 transition-colors">
                  Learn how it works <span aria-hidden="true">&rarr;</span>
                </Link>
              </div>
            </div>
          </FadeIn>

          {/* Integration Workflow Link */}
          <FadeIn>
            <div className="bg-gradient-to-r from-slate-800 to-slate-800/50 border border-slate-700 rounded-2xl p-8 mb-12">
              <div className="flex flex-col md:flex-row items-center gap-6">
                <div className="flex-1">
                  <h3 className="text-xl font-bold text-white mb-2">See It in Action: ChatGPT + Shopping Agent</h3>
                  <p className="text-slate-400 text-sm">Full end-to-end workflow — user asks ChatGPT to buy earbuds, ChatGPT discovers AgenticOrg via MCP, launches a Shopping Agent, gets HITL approval, places the order. With sequence diagrams and architecture stack.</p>
                </div>
                <Link
                  to="/integration-workflow"
                  className="inline-flex items-center gap-2 bg-gradient-to-r from-blue-600 to-emerald-500 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:from-blue-700 hover:to-emerald-600 transition-all shadow-lg whitespace-nowrap"
                >
                  View Full Workflow
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" /></svg>
                </Link>
              </div>
            </div>
          </FadeIn>

          {/* API Key CTA */}
          <FadeIn>
            <div className="text-center">
              <p className="text-slate-400 mb-4">Get your API key from the dashboard to start building.</p>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                <Link
                  to="/signup"
                  className="inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25"
                >
                  Get API Key — Free
                </Link>
                <a
                  href="https://github.com/mishrasanjeev/agentic-org"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all duration-200 ease-out-quart"
                >
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" /></svg>
                  View on GitHub
                </a>
              </div>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 12b. FEATURED: CA FIRM END-TO-END CASE STUDY                  */}
      {/* ============================================================ */}
      <section className="py-20 bg-gradient-to-b from-slate-950 to-slate-900">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-12">
              <span className="inline-block px-3 py-1 rounded-full bg-blue-500/10 text-blue-400 text-xs font-medium tracking-wide uppercase mb-4">Case Study</span>
              <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
                CA Firm Goes Live in 8 Days
              </h2>
              <p className="text-lg text-slate-400 max-w-2xl mx-auto">
                End-to-end automation: Invoice processing, bank reconciliation, GST filing, and Tally sync — all running on AI agents.
              </p>
            </div>
            <div className="grid md:grid-cols-4 gap-6 mb-10">
              {[
                { stage: "1", title: "Invoice", desc: "Create invoices in Zoho Books with GSTIN validation and HSN codes", connector: "Zoho Books" },
                { stage: "2", title: "Bank Reconcile", desc: "Fetch statements via Account Aggregator, auto-match with high accuracy", connector: "Finvu AA" },
                { stage: "3", title: "GST Filing", desc: "Push GSTR-1/3B to GSTN via Adaequare GSP with DSC digital signing", connector: "GSTN" },
                { stage: "4", title: "Tally Sync", desc: "Post vouchers to Tally Prime via XML/TDL protocol through local bridge", connector: "Tally Bridge" },
              ].map((s) => (
                <div key={s.stage} className="relative bg-slate-800/50 border border-slate-700 rounded-xl p-6 hover:border-blue-500/50 transition-colors">
                  <div className="w-8 h-8 rounded-full bg-blue-500/20 text-blue-400 flex items-center justify-center text-sm font-bold mb-3">{s.stage}</div>
                  <h3 className="text-white font-semibold text-lg mb-2">{s.title}</h3>
                  <p className="text-slate-400 text-sm mb-3">{s.desc}</p>
                  <span className="inline-block px-2 py-0.5 rounded bg-slate-700/50 text-slate-300 text-xs">{s.connector}</span>
                </div>
              ))}
            </div>
            <div className="text-center">
              <Link
                to="/blog/ca-firm-ai-agent-end-to-end"
                className="inline-flex items-center gap-2 text-blue-400 hover:text-blue-300 font-medium transition-colors"
              >
                Read the full case study
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
              </Link>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 13. FINAL CTA                                                 */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-900">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <FadeIn>
            <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
              Stop paying people to do what AI virtual employees can do better.
            </h2>
            <p className="text-lg text-slate-400 mb-10">
              {agentsText} agents that act. 1000+ integrations. {toolsText} native tools. Free to start.
            </p>

            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <Link
                to="/signup"
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25"
              >
                Start Free →
              </Link>
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all duration-200 ease-out-quart"
              >
                Book a Demo
              </button>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 13. FOOTER                                                    */}
      {/* ============================================================ */}
      <footer className="bg-slate-950 border-t border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
          <div className="grid sm:grid-cols-2 lg:grid-cols-6 gap-8 mb-12">
            {/* Brand */}
            <div className="lg:col-span-1">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-sm">
                  AO
                </div>
                <span className="text-white font-semibold">AgenticOrg</span>
              </div>
              <p className="text-sm text-slate-400 leading-relaxed">
                Enterprise AI Agent Platform.
                <br />
                Deploy. Automate. Govern.
              </p>
            </div>

            {/* Platform */}
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">Platform</h4>
              <ul className="space-y-2">
                <li><Link to="/login" className="text-slate-400 hover:text-white text-sm transition-colors">Dashboard</Link></li>
                <li><Link to="/login" className="text-slate-400 hover:text-white text-sm transition-colors">Agents</Link></li>
                <li><Link to="/login" className="text-slate-400 hover:text-white text-sm transition-colors">Workflows</Link></li>
                <li><Link to="/login" className="text-slate-400 hover:text-white text-sm transition-colors">Connectors</Link></li>
                <li><Link to="/login" className="text-slate-400 hover:text-white text-sm transition-colors">Observatory</Link></li>
              </ul>
            </div>

            {/* Solutions */}
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">Solutions</h4>
              <ul className="space-y-2">
                <li><a href="#solutions" className="text-slate-400 hover:text-white text-sm transition-colors">For CFOs</a></li>
                <li><a href="#solutions" className="text-slate-400 hover:text-white text-sm transition-colors">For CHROs</a></li>
                <li><a href="#solutions" className="text-slate-400 hover:text-white text-sm transition-colors">For CMOs</a></li>
                <li><a href="#solutions" className="text-slate-400 hover:text-white text-sm transition-colors">For COOs</a></li>
              </ul>
            </div>

            {/* Resources */}
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">Resources</h4>
              <ul className="space-y-2">
                <li><a href="#how-it-works" className="text-slate-400 hover:text-white text-sm transition-colors">How It Works</a></li>
                <li><Link to="/pricing" className="text-slate-400 hover:text-white text-sm transition-colors">Pricing</Link></li>
                <li><a href="#roi-calculator" className="text-slate-400 hover:text-white text-sm transition-colors">ROI Calculator</a></li>
                <li><a href="#demo" className="text-slate-400 hover:text-white text-sm transition-colors">Live Demo</a></li>
                <li><Link to="/blog" className="text-slate-400 hover:text-white text-sm transition-colors">Blog</Link></li>
                <li>
                  <a href="https://github.com/mishrasanjeev/agentic-org" target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-white text-sm transition-colors">
                    GitHub
                  </a>
                </li>
              </ul>
            </div>

            {/* Developers */}
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">Developers</h4>
              <ul className="space-y-2">
                <li><a href="#developers" className="text-slate-400 hover:text-white text-sm transition-colors">SDKs & APIs</a></li>
                <li><a href="https://pypi.org/project/agenticorg/" target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-white text-sm transition-colors">Python SDK (PyPI)</a></li>
                <li><a href="https://www.npmjs.com/package/agenticorg-sdk" target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-white text-sm transition-colors">TypeScript SDK (npm)</a></li>
                <li><a href="https://www.npmjs.com/package/agenticorg-mcp-server" target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-white text-sm transition-colors">MCP Server</a></li>
                <li><Link to="/login" className="text-slate-400 hover:text-white text-sm transition-colors">API Keys</Link></li>
              </ul>
            </div>

            {/* Company */}
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
            <p className="text-sm text-slate-500">
              &copy; 2026 AgenticOrg &middot; Edumatica Pvt Ltd. All rights reserved.
            </p>
            <div className="flex items-center gap-6">
              <a href="/privacy" className="text-sm text-slate-500 hover:text-slate-300 transition-colors">Privacy</a>
              <a href="/terms" className="text-sm text-slate-500 hover:text-slate-300 transition-colors">Terms</a>
              <span className="text-sm text-slate-600">agenticorg.ai</span>
            </div>
          </div>
        </div>
      </footer>

      {/* Demo modal */}
      {showDemo && <DemoModal onClose={() => setShowDemo(false)} />}
    </div>
  );
}
