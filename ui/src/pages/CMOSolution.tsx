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
  { value: "\u20B93,200", label: "Average customer acquisition cost bleeding your budget", color: "text-red-500" },
  { value: "34%", label: "Email open rate ceiling that no amount of A/B testing can break", color: "text-orange-500" },
  { value: "40%", label: "Of marketing budget wasted on underperforming channels", color: "text-red-500" },
];

const FEATURES = [
  {
    title: "Campaign Management",
    description: "AI-optimized campaigns that deliver 3.2x ROAS. Auto-allocate budget across channels, A/B test creatives at scale, and pause underperformers in real time.",
    icon: "M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z",
    gradient: "from-pink-500 to-rose-600",
  },
  {
    title: "Content Factory",
    description: "Generate blog posts, social media content, ad copy, and landing pages. AI matches brand voice, optimizes for SEO, and adapts content for each channel automatically.",
    icon: "M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z",
    gradient: "from-blue-500 to-cyan-600",
  },
  {
    title: "SEO Autopilot",
    description: "Automated keyword research, content gap analysis, and on-page optimization. Track rankings, generate sitemaps, and build backlink strategies — all on autopilot.",
    icon: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z",
    gradient: "from-emerald-500 to-teal-600",
  },
  {
    title: "Email Marketing",
    description: "AI-crafted drip sequences with dynamic personalization. Smart send-time optimization, subject line testing, and automated list hygiene push open rates past 34%.",
    icon: "M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z",
    gradient: "from-orange-500 to-red-600",
  },
  {
    title: "Account-Based Marketing",
    description: "AI identifies high-value accounts, personalizes outreach, and orchestrates multi-touch campaigns across email, ads, and social. Track account engagement scores in real time.",
    icon: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z",
    gradient: "from-amber-500 to-orange-600",
  },
  {
    title: "Brand Monitoring",
    description: "Track brand mentions, sentiment, and share of voice across social media, news, and review sites. Get instant alerts on negative sentiment and competitive moves.",
    icon: "M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z",
    gradient: "from-cyan-500 to-teal-600",
  },
];

const HOW_IT_WORKS = [
  { step: "1", title: "Connect Channels", description: "Link Google Ads, Meta, HubSpot, Mailchimp, and analytics. One-click OAuth setup takes under 10 minutes." },
  { step: "2", title: "Set Goals", description: "Define target CAC, ROAS, and pipeline goals. AI creates a budget allocation plan optimized for your objectives." },
  { step: "3", title: "Launch & Optimize", description: "AI agents create campaigns, test variations, and reallocate budget to winners. Every decision is explained and auditable." },
  { step: "4", title: "Scale Revenue", description: "Expand to new channels and audiences. Agents identify lookalike segments and scale what works automatically." },
];

const TRUST_LOGOS = [
  { name: "HubSpot", abbr: "HS" },
  { name: "Google Ads", abbr: "GA" },
  { name: "Meta Ads", abbr: "MT" },
  { name: "Mailchimp", abbr: "MC" },
  { name: "Brandwatch", abbr: "BW" },
  { name: "Semrush", abbr: "SR" },
];

const KPI_CARDS = [
  { label: "CAC", value: "\u20B91,840", change: "-42% vs last Q", positive: true },
  { label: "MQLs / SQLs", value: "2,140 / 648", change: "+38% pipeline", positive: true },
  { label: "ROAS", value: "3.2x", change: "+1.4x improvement", positive: true },
  { label: "Pipeline Value", value: "\u20B94.2 Cr", change: "+67% QoQ", positive: true },
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
        body: JSON.stringify({ ...form, source: "cmo-solution" }),
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
    "w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:border-pink-500 focus:ring-2 focus:ring-pink-500/20 outline-none transition-all";

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
            <p className="text-slate-600">We will contact you within 24 hours to set up your marketing automation trial.</p>
            <button onClick={onClose} className="mt-6 bg-gradient-to-r from-pink-500 to-rose-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:from-pink-600 hover:to-rose-700 transition-all">
              Close
            </button>
          </div>
        ) : (
          <>
            <h3 className="text-xl font-bold text-slate-900 mb-1">Book a Demo</h3>
            <p className="text-sm text-slate-500 mb-6">See how AI agents can transform your marketing operations.</p>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="cmo-name" className="block text-sm font-medium text-slate-700 mb-1">Name <span className="text-red-500">*</span></label>
                <input id="cmo-name" required type="text" placeholder="Your full name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="cmo-email" className="block text-sm font-medium text-slate-700 mb-1">Work Email <span className="text-red-500">*</span></label>
                <input id="cmo-email" required type="email" placeholder="you@company.com" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="cmo-company" className="block text-sm font-medium text-slate-700 mb-1">Company</label>
                <input id="cmo-company" type="text" placeholder="Your company name" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className={fieldClass} />
              </div>
              <div>
                <label htmlFor="cmo-role" className="block text-sm font-medium text-slate-700 mb-1">Your Role</label>
                <select id="cmo-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className={fieldClass}>
                  <option value="">Select role</option>
                  <option value="cmo">CMO</option>
                  <option value="vp-marketing">VP Marketing</option>
                  <option value="marketing-director">Marketing Director</option>
                  <option value="growth-lead">Growth Lead</option>
                  <option value="other">Other</option>
                </select>
              </div>
              {error && <p className="text-sm text-red-600">{error}</p>}
              <button type="submit" disabled={submitting} className="w-full bg-gradient-to-r from-pink-500 to-rose-600 text-white px-6 py-3 rounded-lg text-sm font-semibold hover:from-pink-600 hover:to-rose-700 transition-all shadow-lg shadow-pink-500/25 disabled:opacity-60 disabled:cursor-not-allowed">
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
/*  CMO Solution Page                                                  */
/* ------------------------------------------------------------------ */
export default function CMOSolution() {
  const [showDemo, setShowDemo] = useState(false);

  return (
    <div className="min-h-screen font-sans text-slate-900 antialiased overflow-x-hidden">
      <Helmet>
        <title>AI-Powered Virtual Marketing Team for CMOs | AgenticOrg</title>
        <meta name="description" content="Cut CAC by 42%, achieve 3.2x ROAS, and scale content across channels. AI agents for campaign management, SEO, email marketing, ABM, and brand monitoring." />
        <link rel="canonical" href="https://agenticorg.ai/solutions/cmo" />
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
            <button onClick={() => setShowDemo(true)} className="bg-gradient-to-r from-pink-500 to-rose-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:from-pink-600 hover:to-rose-700 transition-all shadow-lg shadow-pink-500/25">
              Start Free Trial
            </button>
          </div>
        </div>
      </nav>

      {/* HERO */}
      <section className="relative min-h-screen flex items-center overflow-hidden bg-slate-900">
        <div className="absolute inset-0">
          <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900" />
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-pink-500/20 rounded-full blur-3xl animate-pulse" />
          <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-rose-500/20 rounded-full blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
        </div>
        <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: "linear-gradient(rgba(255,255,255,.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.1) 1px, transparent 1px)", backgroundSize: "64px 64px" }} />

        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-16">
          <div className="max-w-4xl mx-auto text-center">
            <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
              <span className="w-2 h-2 bg-pink-400 rounded-full animate-pulse" />
              <span className="text-slate-300 text-sm">Built for CMOs &amp; Marketing Leaders</span>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-white leading-tight tracking-tight">
              AI-Powered{" "}
              <span className="bg-gradient-to-r from-pink-400 via-rose-300 to-orange-400 bg-clip-text text-transparent">
                Virtual Marketing Team
              </span>
            </h1>

            <p className="mt-6 text-lg sm:text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
              Cut CAC by 42%, achieve 3.2x ROAS, and scale content across every channel. AI agents that run campaigns, optimize spend, and generate pipeline 24/7.
            </p>

            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-pink-500 to-rose-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-pink-600 hover:to-rose-700 transition-all shadow-lg shadow-pink-500/25"
              >
                Start Free Trial
              </button>
              <a
                href="mailto:sanjeev@agenticorg.ai?subject=CMO%20Solution%20Demo"
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
                Your marketing budget is leaking money
              </h2>
              <p className="mt-4 text-lg text-slate-500">
                These numbers are typical for Indian B2B and D2C companies spending INR 20L+/month on marketing.
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
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Your Complete Virtual Marketing Team</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Six AI-powered agents that cover every aspect of modern marketing, from content to conversion.
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
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Real-Time CMO Dashboard</h2>
              <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                Every marketing metric that drives revenue, updated in real time across all channels.
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

          <FadeIn delay={400}>
            <div className="mt-12 bg-white rounded-2xl border border-slate-200 p-6 max-w-3xl mx-auto">
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-pink-500 to-rose-600 flex items-center justify-center text-white font-bold text-sm">CH</div>
                <div>
                  <p className="font-semibold text-slate-900">ROAS by Channel</p>
                  <p className="text-xs text-slate-500">Performance across active channels &middot; Last 30 days</p>
                </div>
              </div>
              <div className="space-y-3">
                {[
                  { channel: "Google Ads", roas: "4.1x", width: "82%", color: "from-blue-500 to-cyan-500" },
                  { channel: "Meta Ads", roas: "3.6x", width: "72%", color: "from-cyan-500 to-blue-500" },
                  { channel: "Email", roas: "8.2x", width: "100%", color: "from-emerald-500 to-teal-500" },
                  { channel: "SEO", roas: "5.4x", width: "66%", color: "from-amber-500 to-orange-500" },
                ].map((ch) => (
                  <div key={ch.channel} className="flex items-center gap-4">
                    <span className="text-sm text-slate-600 w-24 flex-shrink-0">{ch.channel}</span>
                    <div className="flex-1 h-3 bg-slate-100 rounded-full overflow-hidden">
                      <div className={`h-full bg-gradient-to-r ${ch.color} rounded-full transition-all duration-500`} style={{ width: ch.width }} />
                    </div>
                    <span className="text-sm font-semibold text-slate-900 w-12 text-right">{ch.roas}</span>
                  </div>
                ))}
              </div>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section className="py-24 bg-slate-900">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <FadeIn>
            <div className="text-center mb-16">
              <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
                <span className="w-2 h-2 bg-pink-400 rounded-full animate-pulse" />
                <span className="text-slate-300 text-sm">Go Live in Under a Week</span>
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-white">How It Works</h2>
              <p className="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">
                Four simple steps from signup to full marketing automation.
              </p>
            </div>
          </FadeIn>

          <div className="grid md:grid-cols-4 gap-6">
            {HOW_IT_WORKS.map((s, i) => (
              <FadeIn key={s.step} delay={i * 100}>
                <div className="relative bg-slate-800/50 border border-slate-700 rounded-xl p-6 hover:border-pink-500/50 transition-colors h-full">
                  <div className="w-10 h-10 rounded-full bg-pink-500/20 text-pink-400 flex items-center justify-center text-lg font-bold mb-4">{s.step}</div>
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
            <div className="bg-gradient-to-br from-pink-50 via-white to-rose-50 rounded-3xl border border-slate-200 p-8 sm:p-12 text-center">
              <div className="inline-flex items-center gap-2 bg-pink-100 text-pink-700 rounded-full px-4 py-1.5 text-sm font-medium mb-6">
                CMO Suite &mdash; Enterprise Ready
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 mb-4">
                Transform Your Marketing Operations
              </h2>
              <p className="text-lg text-slate-600 mb-6 max-w-xl mx-auto">
                Get your virtual marketing team running in under a week. Full campaign, content, and analytics automation.
              </p>
              <ul className="grid sm:grid-cols-2 gap-3 mb-8 max-w-lg mx-auto text-left">
                {[
                  "3.2x ROAS improvement",
                  "42% lower CAC",
                  "AI content factory",
                  "Multi-channel campaigns",
                  "ABM at scale",
                  "SEO autopilot",
                  "Brand monitoring",
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
                  className="inline-flex items-center justify-center bg-gradient-to-r from-pink-500 to-rose-600 text-white px-10 py-3.5 rounded-xl text-base font-semibold hover:from-pink-600 hover:to-rose-700 transition-all shadow-lg shadow-pink-500/25"
                >
                  Start Free Trial
                </button>
                <a
                  href="mailto:sanjeev@agenticorg.ai?subject=CMO%20Solution%20Demo"
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
            <div className="bg-gradient-to-br from-pink-50 via-white to-rose-50 rounded-3xl border border-slate-200 overflow-hidden">
              <div className="p-8 sm:p-12">
                <div className="text-center mb-12">
                  <div className="inline-flex items-center gap-2 bg-pink-100 text-pink-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
                    Built for Growth-Focused Marketing Teams
                  </div>
                  <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">Trusted Integrations</h2>
                  <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                    Native integrations with the world&apos;s leading marketing platforms and ad networks.
                  </p>
                </div>

                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {TRUST_LOGOS.map((c, i) => (
                    <FadeIn key={c.name} delay={i * 75}>
                      <div className="flex items-center gap-4 bg-white/80 rounded-xl px-5 py-4 border border-slate-100 hover:shadow-md transition-all duration-300">
                        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-pink-400 to-rose-500 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
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
              Stop wasting budget on underperforming campaigns
            </h2>
            <p className="text-lg text-slate-400 mb-10">
              Join CMOs who have cut CAC by 42% and scaled pipeline value by 67% with AI-powered marketing agents.
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => setShowDemo(true)}
                className="w-full sm:w-auto inline-flex items-center justify-center bg-gradient-to-r from-pink-500 to-rose-600 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-pink-600 hover:to-rose-700 transition-all shadow-lg shadow-pink-500/25"
              >
                Start Free Trial
              </button>
              <a
                href="mailto:sanjeev@agenticorg.ai?subject=CMO%20Solution%20Demo"
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
              <p className="text-sm text-slate-400 leading-relaxed">AI-Powered Virtual Marketing Team for CMOs.</p>
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
