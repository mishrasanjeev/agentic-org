import { useState } from "react";
import { Link } from "react-router-dom";
import ROICalculator from "../components/ROICalculator";

/* ------------------------------------------------------------------ */
/*  Landing page for agenticorg.ai                                    */
/* ------------------------------------------------------------------ */

const STATS = [
  { value: "24", label: "Agents" },
  { value: "42", label: "Connectors" },
  { value: "161", label: "Tests" },
  { value: "18", label: "Schemas" },
  { value: "Apache 2.0", label: "License" },
];

const STEPS = [
  {
    num: "01",
    emoji: "\u{1F916}",
    title: "Deploy Agents",
    desc: "Spin up any of 24 pre-built AI agents across Finance, HR, Marketing, Ops, and Back Office domains in minutes.",
  },
  {
    num: "02",
    emoji: "\u{1F50C}",
    title: "Connect Systems",
    desc: "Link to 42 enterprise connectors including SAP, Salesforce, GSTN, Darwinbox, Slack, and more.",
  },
  {
    num: "03",
    emoji: "\u26A1",
    title: "Automate Workflows",
    desc: "Orchestrate multi-agent workflows with HITL governance, shadow mode, and real-time monitoring.",
  },
];

const DOMAINS = [
  {
    emoji: "\u{1F4B0}",
    name: "Finance",
    count: 6,
    capabilities: ["Invoice Processing", "Expense Auditing", "Tax Filing (GSTN)", "Revenue Forecasting"],
    color: "from-emerald-500 to-teal-600",
  },
  {
    emoji: "\u{1F465}",
    name: "HR",
    count: 6,
    capabilities: ["Talent Acquisition", "Onboarding", "Payroll (EPFO)", "Performance", "L&D", "Offboarding"],
    color: "from-blue-500 to-indigo-600",
  },
  {
    emoji: "\u{1F4E3}",
    name: "Marketing",
    count: 5,
    capabilities: ["Content Generation", "SEO Optimization", "Campaign Analytics", "Social Scheduling"],
    color: "from-purple-500 to-pink-600",
  },
  {
    emoji: "\u2699\uFE0F",
    name: "Operations",
    count: 5,
    capabilities: ["Support Triage", "Vendor Management", "Contract Intelligence", "Compliance Guard", "IT Ops"],
    color: "from-orange-500 to-red-600",
  },
  {
    emoji: "\u{1F4CB}",
    name: "Back Office",
    count: 3,
    capabilities: ["Legal Ops", "Risk Sentinel", "Facilities Management"],
    color: "from-cyan-500 to-blue-600",
  },
];

const LAYERS = [
  { name: "API Gateway", desc: "REST + WebSocket ingress with rate limiting and auth" },
  { name: "Orchestration", desc: "DAG-based workflow engine with parallel execution" },
  { name: "Agent Runtime", desc: "24 domain agents with tool-use and memory" },
  { name: "HITL Governance", desc: "Human-in-the-loop approvals, shadow mode, kill switch" },
  { name: "Connector Hub", desc: "42 pre-built integrations with retry and circuit breakers" },
  { name: "Schema Registry", desc: "18 versioned schemas with validation and migration" },
  { name: "Observability", desc: "Structured logging, metrics, tracing, and audit trail" },
  { name: "Infrastructure", desc: "Multi-tenant isolation, auto-scaling, cost controls" },
];

const FEATURES = [
  { emoji: "\u{1F6E1}\uFE0F", title: "HITL Governance", desc: "Every critical agent action requires human approval before execution." },
  { emoji: "\u{1F47B}", title: "Shadow Mode", desc: "Run agents in observation mode to validate outputs before going live." },
  { emoji: "\u{1F510}", title: "PII Masking", desc: "Automatic detection and redaction of sensitive data across all pipelines." },
  { emoji: "\u{1F3E2}", title: "Tenant Isolation", desc: "Complete data and compute isolation for multi-tenant deployments." },
  { emoji: "\u{1F4B8}", title: "Cost Controls", desc: "Per-agent and per-tenant budgets with real-time spend tracking." },
  { emoji: "\u{1F4C8}", title: "Auto-Scaling", desc: "Dynamic scaling based on queue depth and agent utilization metrics." },
  { emoji: "\u{1F4DD}", title: "Audit Trail", desc: "Immutable log of every agent action, decision, and human override." },
  { emoji: "\u{1F6A8}", title: "50 Error Codes", desc: "Granular error taxonomy for precise debugging and alerting." },
];

const INDIA_CONNECTORS = [
  { name: "GSTN", desc: "GST filing & reconciliation" },
  { name: "EPFO", desc: "Provident fund compliance" },
  { name: "Darwinbox", desc: "HR management platform" },
  { name: "Pine Labs Plural", desc: "Payment processing" },
  { name: "Tally", desc: "Accounting integration" },
  { name: "DigiLocker", desc: "Document verification" },
];

export default function Landing() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <div className="min-h-screen font-sans text-slate-900 antialiased">
      {/* NAV BAR */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-slate-900/80 backdrop-blur-md border-b border-slate-700/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm">
              AO
            </div>
            <span className="text-white font-semibold text-lg">AgenticOrg</span>
          </div>
          <div className="hidden md:flex items-center gap-8">
            <a href="#how-it-works" className="text-slate-300 hover:text-white text-sm transition-colors">How it Works</a>
            <a href="#agents" className="text-slate-300 hover:text-white text-sm transition-colors">Agents</a>
            <a href="#architecture" className="text-slate-300 hover:text-white text-sm transition-colors">Architecture</a>
            <a href="#features" className="text-slate-300 hover:text-white text-sm transition-colors">Features</a>
            <a href="#roi-calculator" className="text-slate-300 hover:text-white text-sm transition-colors">ROI</a>
            <Link to="/dashboard" className="text-slate-300 hover:text-white text-sm transition-colors">Dashboard</Link>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="https://github.com/mishrasanjeev/agentic-org"
              target="_blank"
              rel="noopener noreferrer"
              className="hidden sm:inline-flex bg-white text-slate-900 px-4 py-2 rounded-lg text-sm font-medium hover:bg-slate-100 transition-colors"
            >
              GitHub
            </a>
            {/* Mobile hamburger */}
            <button onClick={() => setMobileMenuOpen(!mobileMenuOpen)} className="md:hidden text-white p-2" aria-label="Menu">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                {mobileMenuOpen
                  ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  : <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                }
              </svg>
            </button>
          </div>
        </div>
        {/* Mobile menu dropdown */}
        {mobileMenuOpen && (
          <div className="md:hidden bg-slate-900 border-t border-slate-700/50 px-4 py-4 space-y-3">
            <a href="#how-it-works" onClick={() => setMobileMenuOpen(false)} className="block text-slate-300 hover:text-white text-sm">How it Works</a>
            <a href="#agents" onClick={() => setMobileMenuOpen(false)} className="block text-slate-300 hover:text-white text-sm">Agents</a>
            <a href="#architecture" onClick={() => setMobileMenuOpen(false)} className="block text-slate-300 hover:text-white text-sm">Architecture</a>
            <a href="#features" onClick={() => setMobileMenuOpen(false)} className="block text-slate-300 hover:text-white text-sm">Features</a>
            <a href="#roi-calculator" onClick={() => setMobileMenuOpen(false)} className="block text-slate-300 hover:text-white text-sm">ROI Calculator</a>
            <Link to="/dashboard" onClick={() => setMobileMenuOpen(false)} className="block text-slate-300 hover:text-white text-sm">Dashboard</Link>
            <a href="https://github.com/mishrasanjeev/agentic-org" target="_blank" rel="noopener noreferrer" className="block text-slate-300 hover:text-white text-sm">GitHub</a>
          </div>
        )}
      </nav>

      {/* HERO */}
      <section className="relative min-h-screen flex items-center justify-center overflow-hidden bg-slate-900">
        {/* Animated gradient background */}
        <div className="absolute inset-0">
          <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900" />
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-blue-500/20 rounded-full blur-3xl animate-pulse" />
          <div
            className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl animate-pulse"
            style={{ animationDelay: "1s" }}
          />
          <div
            className="absolute top-1/2 left-1/2 w-64 h-64 bg-emerald-500/10 rounded-full blur-3xl animate-pulse"
            style={{ animationDelay: "2s" }}
          />
        </div>

        {/* Grid pattern overlay */}
        <div
          className="absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.1) 1px, transparent 1px)",
            backgroundSize: "64px 64px",
          }}
        />

        <div className="relative z-10 max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 text-center pt-24">
          {/* Badge */}
          <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-8">
            <span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
            <span className="text-slate-300 text-sm">
              v2.0.0 &mdash; Open Source &middot; Apache 2.0
            </span>
          </div>

          <h1 className="text-4xl sm:text-5xl md:text-7xl font-extrabold text-white leading-tight tracking-tight">
            24 AI Agents.
            <br />
            <span className="bg-gradient-to-r from-blue-400 via-purple-400 to-emerald-400 bg-clip-text text-transparent">
              42 Enterprise Systems.
            </span>
            <br />
            One Platform.
          </h1>

          <p className="mt-6 text-lg sm:text-xl text-slate-400 max-w-2xl mx-auto leading-relaxed">
            The open-source enterprise AI agent platform with human-in-the-loop governance, shadow
            mode, and India-first connectors. Deploy, orchestrate, and monitor autonomous agents at
            scale.
          </p>

          {/* CTAs */}
          <div className="mt-10 flex flex-col sm:flex-row items-center justify-center gap-4">
            <a
              href="https://github.com/mishrasanjeev/agentic-org"
              target="_blank"
              rel="noopener noreferrer"
              className="w-full sm:w-auto inline-flex items-center justify-center gap-2 bg-white text-slate-900 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-100 transition-all shadow-lg shadow-white/10 hover:shadow-white/20"
            >
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
              </svg>
              View on GitHub
            </a>
            <a
              href="https://github.com/mishrasanjeev/agentic-org#readme"
              target="_blank"
              rel="noopener noreferrer"
              className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
            >
              Read Docs
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
              </svg>
            </a>
          </div>

          {/* Scroll indicator */}
          <div className="mt-20 animate-bounce">
            <svg className="w-6 h-6 mx-auto text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
            </svg>
          </div>
        </div>
      </section>

      {/* STATS BAR */}
      <section className="relative z-10 -mt-16">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="bg-white rounded-2xl shadow-xl shadow-slate-200/50 border border-slate-200 grid grid-cols-2 sm:grid-cols-5 divide-x divide-slate-100">
            {STATS.map((s) => (
              <div key={s.label} className="px-4 py-6 text-center">
                <div className="text-2xl sm:text-3xl font-extrabold text-slate-900">{s.value}</div>
                <div className="text-sm text-slate-500 mt-1">{s.label}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section id="how-it-works" className="py-24 bg-white scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">How It Works</h2>
            <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
              Get from zero to production-ready agent automation in three steps.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-8">
            {STEPS.map((step) => (
              <div key={step.num} className="relative group">
                <div className="bg-slate-50 rounded-2xl p-8 border border-slate-100 hover:border-slate-200 hover:shadow-lg transition-all duration-300 h-full">
                  <div className="text-5xl mb-4">{step.emoji}</div>
                  <div className="text-xs font-mono text-slate-400 mb-2">STEP {step.num}</div>
                  <h3 className="text-xl font-bold text-slate-900 mb-3">{step.title}</h3>
                  <p className="text-slate-600 leading-relaxed">{step.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* AGENT DOMAINS */}
      <section id="agents" className="py-24 bg-slate-50 scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Agent Domains</h2>
            <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
              24 purpose-built AI agents organized across 5 enterprise domains.
            </p>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {DOMAINS.map((d) => (
              <div
                key={d.name}
                className="bg-white rounded-2xl border border-slate-200 overflow-hidden hover:shadow-lg transition-all duration-300 group"
              >
                <div className={`h-2 bg-gradient-to-r ${d.color}`} />
                <div className="p-6">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-3">
                      <span className="text-3xl">{d.emoji}</span>
                      <h3 className="text-xl font-bold text-slate-900">{d.name}</h3>
                    </div>
                    <span className="bg-slate-100 text-slate-600 px-3 py-1 rounded-full text-sm font-medium">
                      {d.count} agents
                    </span>
                  </div>
                  <ul className="space-y-2">
                    {d.capabilities.map((c) => (
                      <li key={c} className="flex items-center gap-2 text-sm text-slate-600">
                        <svg
                          className="w-4 h-4 text-emerald-500 flex-shrink-0"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                        {c}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ARCHITECTURE */}
      <section id="architecture" className="py-24 bg-white scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">8-Layer Architecture</h2>
            <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
              Production-grade, layered design for reliability, security, and observability.
            </p>
          </div>

          <div className="max-w-3xl mx-auto space-y-3">
            {LAYERS.map((layer, i) => (
              <div
                key={layer.name}
                className="flex items-center gap-4 bg-slate-50 rounded-xl px-6 py-4 border border-slate-100 hover:border-slate-200 hover:shadow-md transition-all duration-300"
              >
                <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-gradient-to-br from-slate-800 to-slate-900 flex items-center justify-center text-white font-bold text-sm">
                  {i + 1}
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-slate-900">{layer.name}</h3>
                  <p className="text-sm text-slate-500">{layer.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FEATURES GRID */}
      <section id="features" className="py-24 bg-slate-50 scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Enterprise Features</h2>
            <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
              Built for production from day one. Security, governance, and observability are not
              afterthoughts.
            </p>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {FEATURES.map((f) => (
              <div
                key={f.title}
                className="bg-white rounded-2xl p-6 border border-slate-200 hover:border-slate-300 hover:shadow-lg transition-all duration-300"
              >
                <div className="text-3xl mb-4">{f.emoji}</div>
                <h3 className="font-bold text-slate-900 mb-2">{f.title}</h3>
                <p className="text-sm text-slate-500 leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* INDIA-FIRST */}
      <section className="py-24 bg-white scroll-mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
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
                  Native integrations with India&apos;s most critical government and business
                  platforms.
                </p>
              </div>

              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {INDIA_CONNECTORS.map((c) => (
                  <div
                    key={c.name}
                    className="flex items-center gap-4 bg-white/80 rounded-xl px-5 py-4 border border-slate-100 hover:shadow-md transition-all duration-300"
                  >
                    <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-orange-400 to-green-500 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
                      {c.name.slice(0, 2)}
                    </div>
                    <div>
                      <h3 className="font-semibold text-slate-900 text-sm">{c.name}</h3>
                      <p className="text-xs text-slate-500">{c.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ROI CALCULATOR */}
      <ROICalculator />

      {/* OPEN SOURCE */}
      <section className="py-24 bg-slate-900 scroll-mt-16">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          {/* Apache 2.0 badge */}
          <div className="inline-flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 rounded-full px-4 py-1.5 mb-8">
            <svg className="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
              />
            </svg>
            <span className="text-emerald-400 text-sm font-medium">Apache 2.0 Licensed</span>
          </div>

          <h2 className="text-3xl sm:text-4xl font-bold text-white mb-6">100% Open Source</h2>
          <p className="text-lg text-slate-400 max-w-2xl mx-auto mb-10 leading-relaxed">
            AgenticOrg is free and open source forever. Contribute agents, connectors, or
            improvements. Join a growing community building the future of enterprise AI automation.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <a
              href="https://github.com/mishrasanjeev/agentic-org"
              target="_blank"
              rel="noopener noreferrer"
              className="w-full sm:w-auto inline-flex items-center justify-center gap-2 bg-white text-slate-900 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-100 transition-all shadow-lg"
            >
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
              </svg>
              Star on GitHub
            </a>
            <a
              href="https://github.com/mishrasanjeev/agentic-org/blob/main/CONTRIBUTING.md"
              target="_blank"
              rel="noopener noreferrer"
              className="w-full sm:w-auto inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
            >
              Contribute
            </a>
          </div>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="bg-slate-950 border-t border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-8 mb-12">
            {/* Brand */}
            <div className="sm:col-span-2 lg:col-span-1">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm">
                  AO
                </div>
                <span className="text-white font-semibold">AgenticOrg</span>
              </div>
              <p className="text-sm text-slate-400 leading-relaxed">
                Enterprise AI Agent Platform.
                <br />
                Open source. Production-ready.
              </p>
              <p className="text-sm text-slate-500 mt-3">agenticorg.ai</p>
            </div>

            {/* Documentation */}
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">
                Documentation
              </h4>
              <ul className="space-y-2">
                <li>
                  <a
                    href="https://github.com/mishrasanjeev/agentic-org#readme"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-400 hover:text-white text-sm transition-colors"
                  >
                    Docs
                  </a>
                </li>
                <li>
                  <a
                    href="https://github.com/mishrasanjeev/agentic-org/blob/main/docs/architecture.md"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-400 hover:text-white text-sm transition-colors"
                  >
                    Architecture
                  </a>
                </li>
                <li>
                  <a
                    href="https://github.com/mishrasanjeev/agentic-org/blob/main/docs/api-reference.md"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-400 hover:text-white text-sm transition-colors"
                  >
                    API Reference
                  </a>
                </li>
                <li>
                  <a
                    href="https://github.com/mishrasanjeev/agentic-org/blob/main/docs/agents-guide.md"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-400 hover:text-white text-sm transition-colors"
                  >
                    Agents Guide
                  </a>
                </li>
                <li>
                  <a
                    href="https://github.com/mishrasanjeev/agentic-org/blob/main/docs/why-agenticorg.md"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-400 hover:text-white text-sm transition-colors"
                  >
                    Why AgenticOrg?
                  </a>
                </li>
              </ul>
            </div>

            {/* Community */}
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">
                Community
              </h4>
              <ul className="space-y-2">
                <li>
                  <a
                    href="https://github.com/mishrasanjeev/agentic-org"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-400 hover:text-white text-sm transition-colors"
                  >
                    GitHub
                  </a>
                </li>
                <li>
                  <a
                    href="https://github.com/mishrasanjeev/agentic-org/blob/main/CONTRIBUTING.md"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-400 hover:text-white text-sm transition-colors"
                  >
                    Contributing
                  </a>
                </li>
                <li>
                  <a
                    href="https://github.com/mishrasanjeev/agentic-org/blob/main/LICENSE"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-400 hover:text-white text-sm transition-colors"
                  >
                    License
                  </a>
                </li>
              </ul>
            </div>

            {/* Platform */}
            <div>
              <h4 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider">
                Platform
              </h4>
              <ul className="space-y-2">
                <li>
                  <Link to="/dashboard" className="text-slate-400 hover:text-white text-sm transition-colors">
                    Dashboard
                  </Link>
                </li>
                <li>
                  <Link to="/dashboard/agents" className="text-slate-400 hover:text-white text-sm transition-colors">
                    Agents
                  </Link>
                </li>
                <li>
                  <Link to="/dashboard/workflows" className="text-slate-400 hover:text-white text-sm transition-colors">
                    Workflows
                  </Link>
                </li>
                <li>
                  <Link to="/dashboard/connectors" className="text-slate-400 hover:text-white text-sm transition-colors">
                    Connectors
                  </Link>
                </li>
              </ul>
            </div>
          </div>

          <div className="border-t border-slate-800 pt-8 flex flex-col sm:flex-row items-center justify-between gap-4">
            <p className="text-sm text-slate-500">
              &copy; 2026 AgenticOrg Contributors. Apache 2.0 License.
            </p>
            <p className="text-sm text-slate-600">agenticorg.ai</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
