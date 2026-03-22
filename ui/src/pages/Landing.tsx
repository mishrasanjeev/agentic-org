import { useState, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import ROICalculator from "../components/ROICalculator";

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
function BrowserFrame({ src, title, className = "" }: {
  src: string;
  title: string;
  className?: string;
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
      {/* Content */}
      <div className="relative w-full" style={{ paddingBottom: "56.25%" }}>
        <iframe
          src={src}
          title={title}
          className="absolute inset-0 w-full h-full border-0"
          loading="lazy"
          sandbox="allow-scripts allow-same-origin"
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const LOGO_BAR = [
  { name: "Oracle", short: "OC" },
  { name: "SAP", short: "SAP" },
  { name: "Salesforce", short: "SF" },
  { name: "Slack", short: "SL" },
  { name: "GSTN", short: "GS" },
  { name: "Darwinbox", short: "DB" },
  { name: "Stripe", short: "ST" },
  { name: "HubSpot", short: "HS" },
];

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
    description: "6 finance agents handle AP, AR, Bank Reconciliation (99.7% match rate), Tax Filing, Month-end Close, and FP&A forecasting.",
    agents: ["Accounts Payable", "Accounts Receivable", "Reconciliation", "Tax Filing", "Month-end Close", "FP&A"],
    metric: "\u20B969,800/month saved on early-payment discounts alone",
  },
  {
    role: "CHRO",
    gradient: "from-blue-500 to-indigo-600",
    pain: "Onboard in hours, not weeks",
    description: "6 HR agents manage Onboarding, Payroll (847 employees), Talent Acquisition, Performance Reviews, L&D, and Offboarding.",
    agents: ["Onboarding", "Payroll", "Talent Acquisition", "Performance", "L&D", "Offboarding"],
    metric: "Zero payroll errors with automated PF/ESI/TDS",
  },
  {
    role: "CMO",
    gradient: "from-purple-500 to-pink-600",
    pain: "Launch campaigns while you sleep",
    description: "5 marketing agents run Campaign Management, Content Generation, SEO Optimization, CRM Nurturing, and Brand Monitoring.",
    agents: ["Campaign Mgmt", "Content Gen", "SEO", "CRM Nurture", "Brand Monitor"],
    metric: "3.2x ROI on automated multi-channel campaigns",
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
/*  Landing Page                                                       */
/* ------------------------------------------------------------------ */
export default function Landing() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const closeMobile = useCallback(() => setMobileMenuOpen(false), []);

  return (
    <div className="min-h-screen font-sans text-slate-900 antialiased">

      {/* ============================================================ */}
      {/* 1. NAVBAR                                                     */}
      {/* ============================================================ */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-slate-900/90 backdrop-blur-md border-b border-slate-700/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm">
              AO
            </div>
            <span className="text-white font-semibold text-lg">AgenticOrg</span>
          </div>

          {/* Desktop links */}
          <div className="hidden md:flex items-center gap-8">
            <a href="#platform" className="text-slate-300 hover:text-white text-sm transition-colors">Platform</a>
            <a href="#solutions" className="text-slate-300 hover:text-white text-sm transition-colors">Solutions</a>
            <a href="#roi-calculator" className="text-slate-300 hover:text-white text-sm transition-colors">Pricing</a>
            <a href="#how-it-works" className="text-slate-300 hover:text-white text-sm transition-colors">Resources</a>
          </div>

          {/* Right CTAs */}
          <div className="flex items-center gap-3">
            <Link
              to="/login"
              className="hidden sm:inline-flex border border-slate-500 text-slate-300 hover:text-white hover:border-white px-4 py-2 rounded-lg text-sm font-medium transition-all"
            >
              Sign In
            </Link>
            <a
              href="mailto:hello@agenticorg.ai?subject=Book a Demo"
              className="hidden sm:inline-flex bg-gradient-to-r from-blue-500 to-violet-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25"
            >
              Book a Demo
            </a>
            <button onClick={() => setMobileMenuOpen(!mobileMenuOpen)} className="md:hidden text-white p-2" aria-label="Menu">
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
          <div className="md:hidden bg-slate-900 border-t border-slate-700/50 px-4 py-4 space-y-3">
            <a href="#platform" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Platform</a>
            <a href="#solutions" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Solutions</a>
            <a href="#roi-calculator" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Pricing</a>
            <a href="#how-it-works" onClick={closeMobile} className="block text-slate-300 hover:text-white text-sm">Resources</a>
            <Link to="/login" onClick={closeMobile} className="block border border-slate-500 text-slate-300 px-4 py-2 rounded-lg text-sm font-medium text-center mt-2">Sign In</Link>
            <a href="mailto:hello@agenticorg.ai?subject=Book a Demo" onClick={closeMobile} className="block bg-gradient-to-r from-blue-500 to-violet-600 text-white px-4 py-2 rounded-lg text-sm font-medium text-center">Book a Demo</a>
          </div>
        )}
      </nav>

      {/* ============================================================ */}
      {/* 2. HERO                                                       */}
      {/* ============================================================ */}
      <section className="relative min-h-screen flex items-center overflow-hidden bg-slate-900">
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
              <span className="text-slate-300 text-sm">Now Live &mdash; 24 AI Agents Across 5 Departments</span>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-white leading-tight tracking-tight">
              Your Back Office{" "}
              <span className="bg-gradient-to-r from-blue-400 via-purple-400 to-emerald-400 bg-clip-text text-transparent">
                Runs Itself.
              </span>
            </h1>

            <p className="mt-6 text-lg text-slate-400 max-w-xl leading-relaxed">
              AI agents that process invoices, run payroll, launch campaigns, and resolve incidents
              &mdash; with human approval on every critical decision.
            </p>

            {/* CTAs */}
            <div className="mt-8 flex flex-col sm:flex-row items-start gap-4">
              <Link
                to="/login"
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-violet-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25"
              >
                Start Free
              </Link>
              <a
                href="#demo"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
              >
                Watch Demo
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                </svg>
              </a>
            </div>

            <p className="mt-4 text-sm text-slate-500">
              No credit card required &middot; Deploy in 5 minutes &middot; SOC-2 ready
            </p>
          </div>

          {/* Right — Product screenshot */}
          <div className="hidden lg:block">
            <BrowserFrame
              src="https://app.agenticorg.ai/dashboard"
              title="app.agenticorg.ai/dashboard"
            />
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 3. LOGO BAR                                                   */}
      {/* ============================================================ */}
      <section className="py-10 bg-white border-b border-slate-100">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <p className="text-center text-sm text-slate-400 mb-6">Trusted by teams using</p>
          <div className="flex flex-wrap items-center justify-center gap-6 sm:gap-10">
            {LOGO_BAR.map((l) => (
              <div key={l.name} className="flex items-center gap-2 text-slate-400">
                <div className="w-8 h-8 rounded bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-500">
                  {l.short}
                </div>
                <span className="text-sm font-medium">{l.name}</span>
              </div>
            ))}
          </div>
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
                <div className="bg-white rounded-2xl p-8 border border-slate-200 text-center hover:shadow-lg transition-all duration-300">
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
      {/* 5. PLATFORM OVERVIEW                                          */}
      {/* ============================================================ */}
      <section id="platform" className="py-24 bg-white scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">One Platform. 24 Agents. Complete Automation.</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Everything you need to deploy, monitor, and govern autonomous AI agents at enterprise scale.
              </p>
            </div>
          </FadeIn>

          <div className="grid lg:grid-cols-3 gap-8">
            {/* Agent Fleet */}
            <FadeIn delay={0}>
              <div className="space-y-4">
                <BrowserFrame
                  src="https://app.agenticorg.ai/dashboard/agents"
                  title="app.agenticorg.ai/dashboard/agents"
                />
                <h3 className="text-xl font-bold text-slate-900">Agent Fleet</h3>
                <p className="text-slate-600 text-sm leading-relaxed">
                  View, configure, and deploy 24 pre-built agents across Finance, HR, Marketing, Ops, and Back Office. Each agent comes with domain-specific tools, memory, and safety guardrails.
                </p>
              </div>
            </FadeIn>

            {/* Live Observatory */}
            <FadeIn delay={150}>
              <div className="space-y-4">
                <BrowserFrame
                  src="https://app.agenticorg.ai/dashboard/observatory"
                  title="app.agenticorg.ai/dashboard/observatory"
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
                  src="https://app.agenticorg.ai/dashboard/approvals"
                  title="app.agenticorg.ai/dashboard/approvals"
                />
                <h3 className="text-xl font-bold text-slate-900">HITL Approvals</h3>
                <p className="text-slate-600 text-sm leading-relaxed">
                  Human-in-the-loop governance for every critical decision. Approve, reject, or override agent actions with full context. No agent acts without your say on high-stakes operations.
                </p>
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
                <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden hover:shadow-lg transition-all duration-300 h-full">
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
                { num: "1", title: "Sign in & pick your agents", desc: "Choose from 24 pre-built agents across 5 domains. Each agent is production-ready with domain-specific tools and safety guardrails." },
                { num: "2", title: "Connect your systems", desc: "42 connectors for SAP, Oracle, GSTN, Darwinbox, Slack, and more. Plug into your existing infrastructure in minutes, not months." },
                { num: "3", title: "Agents work, you approve", desc: "Agents automate the repetitive work. You approve critical decisions via HITL governance. Full audit trail on every action." },
              ].map((step, i) => (
                <FadeIn key={step.num} delay={i * 150}>
                  <div className="text-center">
                    <div className="relative z-10 w-12 h-12 rounded-full bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center text-white font-bold text-lg mx-auto mb-6 shadow-lg shadow-blue-500/25">
                      {step.num}
                    </div>
                    <h3 className="text-lg font-bold text-slate-900 mb-3">{step.title}</h3>
                    <p className="text-slate-600 text-sm leading-relaxed">{step.desc}</p>
                  </div>
                </FadeIn>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 8. LIVE DEMO                                                  */}
      {/* ============================================================ */}
      <section id="demo" className="py-24 bg-slate-900 scroll-mt-16">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-12">
              <h2 className="text-3xl sm:text-4xl font-bold text-white">See It In Action</h2>
              <p className="mt-4 text-lg text-slate-400">
                This is a live production system. Not a mockup. Not a prototype.
              </p>
            </div>
          </FadeIn>

          <FadeIn>
            <BrowserFrame
              src="https://app.agenticorg.ai/dashboard/observatory"
              title="app.agenticorg.ai/dashboard/observatory"
            />
          </FadeIn>

          <FadeIn>
            <div className="text-center mt-10">
              <p className="text-slate-400 mb-6">This is real. Not a mockup. Login and try it yourself.</p>
              <Link
                to="/login"
                className="inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-violet-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25"
              >
                Try Live Demo
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
                title: "Tenant Isolation",
                desc: "Multi-org data segregation with row-level security. Complete compute and data isolation between tenants.",
              },
            ].map((f, i) => (
              <FadeIn key={f.title} delay={i * 100}>
                <div className="bg-white rounded-2xl p-6 border border-slate-200 hover:border-slate-300 hover:shadow-lg transition-all duration-300 h-full">
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
      {/* 12. FINAL CTA                                                 */}
      {/* ============================================================ */}
      <section className="py-24 bg-slate-900">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <FadeIn>
            <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
              Stop paying people to do what AI agents can do better.
            </h2>
            <p className="text-lg text-slate-400 mb-10">
              24 agents. 42 connectors. Deploy in 5 minutes.
            </p>

            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <Link
                to="/login"
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-violet-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-violet-700 transition-all shadow-lg shadow-blue-500/25"
              >
                Start Free
              </Link>
              <a
                href="mailto:hello@agenticorg.ai?subject=Book a Demo"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
              >
                Book a Demo
              </a>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ============================================================ */}
      {/* 13. FOOTER                                                    */}
      {/* ============================================================ */}
      <footer className="bg-slate-950 border-t border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
          <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-8 mb-12">
            {/* Brand */}
            <div className="lg:col-span-1">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm">
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
                <li><a href="#roi-calculator" className="text-slate-400 hover:text-white text-sm transition-colors">ROI Calculator</a></li>
                <li><a href="#demo" className="text-slate-400 hover:text-white text-sm transition-colors">Live Demo</a></li>
                <li>
                  <a href="https://github.com/mishrasanjeev/agentic-org" target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-white text-sm transition-colors">
                    GitHub
                  </a>
                </li>
              </ul>
            </div>

            {/* Company */}
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">Company</h4>
              <ul className="space-y-2">
                <li><a href="mailto:hello@agenticorg.ai" className="text-slate-400 hover:text-white text-sm transition-colors">Contact</a></li>
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
              <a href="mailto:hello@agenticorg.ai" className="text-sm text-slate-500 hover:text-slate-300 transition-colors">Privacy</a>
              <a href="mailto:hello@agenticorg.ai" className="text-sm text-slate-500 hover:text-slate-300 transition-colors">Terms</a>
              <span className="text-sm text-slate-600">agenticorg.ai</span>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
