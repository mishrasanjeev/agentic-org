import { useState, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";

/* ------------------------------------------------------------------ */
/*  useInView — Intersection Observer hook for scroll animations       */
/* ------------------------------------------------------------------ */
function useInView(threshold = 0.15): { ref: (el: HTMLDivElement | null) => void; visible: boolean } {
  const [visible, setVisible] = useState(false);
  const obsRef = useRef<IntersectionObserver | null>(null);

  const setRef = useCallback(
    (el: HTMLDivElement | null) => {
      if (obsRef.current) {
        obsRef.current.disconnect();
        obsRef.current = null;
      }
      if (!el) return;
      const obs = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) {
            setVisible(true);
            obs.disconnect();
          }
        },
        { threshold },
      );
      obs.observe(el);
      obsRef.current = obs;
    },
    [threshold],
  );

  return { ref: setRef, visible };
}

/* ------------------------------------------------------------------ */
/*  FadeIn — scroll-triggered fade                                     */
/* ------------------------------------------------------------------ */
function FadeIn({
  children,
  className = "",
  delay = 0,
}: {
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
/*  SVG Icons                                                          */
/* ------------------------------------------------------------------ */
function ShieldIcon({ className = "w-6 h-6" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1.5}
        d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
      />
    </svg>
  );
}

function KeyIcon({ className = "w-6 h-6" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1.5}
        d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z"
      />
    </svg>
  );
}

function RobotIcon({ className = "w-6 h-6" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1.5}
        d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714a2.25 2.25 0 00.659 1.591L19 14.5m-4.75-11.396c.251.023.501.05.75.082M12 12.75h.008v.008H12v-.008z"
      />
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1.5}
        d="M8 17h8m-8 0a2 2 0 01-2-2v-2h12v2a2 2 0 01-2 2m-8 0v3m8-3v3M9 10h.01M15 10h.01"
      />
    </svg>
  );
}

function ClipboardCheckIcon({ className = "w-6 h-6" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1.5}
        d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"
      />
    </svg>
  );
}

function AuditLogIcon({ className = "w-6 h-6" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1.5}
        d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"
      />
    </svg>
  );
}

function CheckCircle({ className = "w-5 h-5 text-emerald-500" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  );
}

function XCircle({ className = "w-5 h-5 text-red-500" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  );
}

function WarningIcon({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.999L13.732 4.001c-.77-1.333-2.694-1.333-3.464 0L3.34 16.001c-.77 1.332.192 2.999 1.732 2.999z"
      />
    </svg>
  );
}

function LayersIcon({ className = "w-6 h-6" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1.5}
        d="M6.429 9.75L2.25 12l4.179 2.25m0-4.5l5.571 3 5.571-3m-11.142 0L2.25 7.5 12 2.25l9.75 5.25-4.179 2.25m0 0l4.179 2.25L12 17.25 6.429 14.25m5.571 3l5.571-3m-11.142 0l5.571 3m0 0L12 21.75l9.75-5.25"
      />
    </svg>
  );
}

function LockIcon({ className = "w-6 h-6" }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1.5}
        d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"
      />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Page Component                                                     */
/* ------------------------------------------------------------------ */
export default function HowGrantexWorks() {
  return (
    <>
      <Helmet>
        <title>How Grantex Works | AgenticOrg</title>
        <meta
          name="description"
          content="Learn how AgenticOrg uses Grantex to enforce fine-grained permissions on every AI agent tool call. Manifest-based, offline JWT, sub-millisecond checks."
        />
      </Helmet>

      <div className="min-h-screen bg-white text-slate-900">
        {/* ─── Nav ─── */}
        <nav className="fixed top-0 inset-x-0 z-50 bg-slate-900/95 backdrop-blur border-b border-slate-800">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
            <Link to="/" className="text-white font-bold text-lg tracking-tight flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-xs font-black">
                A
              </div>
              AgenticOrg
            </Link>
            <div className="flex items-center gap-4">
              <Link to="/pricing" className="text-sm text-slate-300 hover:text-white transition-colors">
                Pricing
              </Link>
              <Link
                to="/signup"
                className="text-sm bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-4 py-2 rounded-lg font-medium hover:from-blue-600 hover:to-cyan-600 transition-all"
              >
                Get Started
              </Link>
            </div>
          </div>
        </nav>

        {/* ============================================================ */}
        {/* S1: HERO                                                     */}
        {/* ============================================================ */}
        <section className="relative pt-16 min-h-[85vh] flex items-center overflow-hidden bg-slate-900">
          {/* Animated background */}
          <div className="absolute inset-0">
            <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900" />
            <div className="absolute inset-0 opacity-30">
              <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-blue-500/20 rounded-full blur-3xl animate-pulse" />
              <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
              <div className="absolute top-1/2 left-1/2 w-64 h-64 bg-emerald-500/15 rounded-full blur-3xl animate-pulse" style={{ animationDelay: "2s" }} />
            </div>
            {/* Grid overlay */}
            <div
              className="absolute inset-0 opacity-[0.03]"
              style={{
                backgroundImage: "linear-gradient(rgba(255,255,255,.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.1) 1px, transparent 1px)",
                backgroundSize: "60px 60px",
              }}
            />
          </div>

          <div className="relative max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center py-24">
            <FadeIn>
              <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-blue-500/10 border border-blue-500/20 mb-8">
                <ShieldIcon className="w-4 h-4 text-blue-400" />
                <span className="text-sm font-medium text-blue-300">Enterprise-Grade Agent Security</span>
              </div>
            </FadeIn>

            <FadeIn delay={100}>
              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-white leading-tight tracking-tight">
                How AgenticOrg Agents{" "}
                <span className="bg-gradient-to-r from-blue-400 via-purple-400 to-emerald-400 bg-clip-text text-transparent">
                  Access Your Data Safely
                </span>
              </h1>
            </FadeIn>

            <FadeIn delay={200}>
              <p className="mt-6 text-lg sm:text-xl text-slate-300 max-w-2xl mx-auto leading-relaxed">
                Every tool call is verified. Every permission is checked.{" "}
                <span className="text-white font-semibold">Every action is audited.</span>
              </p>
            </FadeIn>

            <FadeIn delay={300}>
              <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
                <a
                  href="#how-it-works"
                  className="bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-8 py-3.5 rounded-xl font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25 text-sm"
                >
                  See How It Works
                </a>
                <Link
                  to="/dashboard/enforce-audit"
                  className="border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl font-semibold hover:border-slate-400 hover:text-white transition-all text-sm"
                >
                  View Audit Logs
                </Link>
              </div>
            </FadeIn>

            {/* Shield animation */}
            <FadeIn delay={500}>
              <div className="mt-16 flex justify-center">
                <div className="relative">
                  <div className="w-24 h-24 rounded-2xl bg-gradient-to-br from-blue-500/20 to-teal-500/20 border border-blue-500/30 flex items-center justify-center animate-pulse">
                    <ShieldIcon className="w-12 h-12 text-blue-400" />
                  </div>
                  <div className="absolute -top-2 -right-2 w-6 h-6 rounded-full bg-emerald-500 flex items-center justify-center">
                    <svg className="w-3.5 h-3.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                    </svg>
                  </div>
                </div>
              </div>
            </FadeIn>
          </div>
        </section>

        {/* ============================================================ */}
        {/* S2: THE PROBLEM                                              */}
        {/* ============================================================ */}
        <section className="relative py-24 bg-gradient-to-b from-slate-50 to-white overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-red-300 to-transparent" />
          <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
            <FadeIn>
              <div className="text-center mb-16">
                <span className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-red-50 border border-red-200 text-red-600 text-sm font-medium mb-6">
                  <WarningIcon className="w-4 h-4" />
                  The Risk
                </span>
                <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-900">
                  The Problem: Agents Without Guardrails
                </h2>
                <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                  Without proper enforcement, AI agents can do things you never intended.
                </p>
              </div>
            </FadeIn>

            <div className="grid md:grid-cols-2 gap-12 items-center">
              {/* Visual */}
              <FadeIn delay={100}>
                <div className="relative">
                  <div className="bg-gradient-to-br from-red-50 to-orange-50 border-2 border-dashed border-red-200 rounded-2xl p-8 text-center">
                    {/* Agent icon freely accessing */}
                    <div className="flex items-center justify-center gap-6 mb-6">
                      <div className="w-16 h-16 rounded-2xl bg-slate-800 flex items-center justify-center shadow-lg">
                        <RobotIcon className="w-8 h-8 text-blue-400" />
                      </div>
                      <div className="flex flex-col items-center">
                        <div className="text-2xl text-red-400 animate-pulse">---&gt;</div>
                        <span className="text-xs text-red-500 font-medium mt-1">Unrestricted</span>
                      </div>
                      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-amber-100 to-orange-100 border border-amber-200 flex items-center justify-center shadow-lg">
                        <svg className="w-8 h-8 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125v-3.75" />
                        </svg>
                      </div>
                    </div>
                    <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-red-100 border border-red-200">
                      <XCircle className="w-5 h-5 text-red-500" />
                      <span className="text-sm font-semibold text-red-700">No Permission Checks</span>
                    </div>
                  </div>
                </div>
              </FadeIn>

              {/* Bullet points */}
              <FadeIn delay={200}>
                <div className="space-y-6">
                  {[
                    {
                      icon: <XCircle className="w-6 h-6 text-red-500" />,
                      title: "Agents can exceed their mandate",
                      desc: 'An agent told to READ your contacts could DELETE them instead. There\'s nothing stopping it.',
                      color: "border-red-200 bg-red-50",
                    },
                    {
                      icon: <XCircle className="w-6 h-6 text-orange-500" />,
                      title: "Revoked access doesn't stop a running agent",
                      desc: "You revoke a token, but the agent already cached its permissions. It keeps going.",
                      color: "border-orange-200 bg-orange-50",
                    },
                    {
                      icon: <XCircle className="w-6 h-6 text-amber-600" />,
                      title: "Permission checks guessed from tool names",
                      desc: '"process_refund" sounds harmless, so it\'s treated as a READ. But it actually moves money.',
                      color: "border-amber-200 bg-amber-50",
                    },
                  ].map((item, i) => (
                    <div key={i} className={`flex gap-4 p-4 rounded-xl border ${item.color}`}>
                      <div className="flex-shrink-0 mt-0.5">{item.icon}</div>
                      <div>
                        <h4 className="font-bold text-slate-900 text-sm">{item.title}</h4>
                        <p className="text-slate-600 text-sm mt-1 leading-relaxed">{item.desc}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </FadeIn>
            </div>
          </div>
        </section>

        {/* ============================================================ */}
        {/* S3: HOW IT WORKS — STEP BY STEP                              */}
        {/* ============================================================ */}
        <section id="how-it-works" className="relative py-24 bg-white overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-blue-300 to-transparent" />
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            <FadeIn>
              <div className="text-center mb-20">
                <span className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-blue-50 border border-blue-200 text-blue-600 text-sm font-medium mb-6">
                  <ShieldIcon className="w-4 h-4" />
                  The Solution
                </span>
                <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-900">
                  How Grantex Protects You
                </h2>
                <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                  Five layers of enforcement between every agent and your data.
                </p>
              </div>
            </FadeIn>

            {/* Vertical pipeline */}
            <div className="relative">
              {/* Connecting line */}
              <div className="absolute left-8 md:left-1/2 top-0 bottom-0 w-px bg-gradient-to-b from-blue-300 via-purple-300 to-emerald-300 hidden md:block" />

              {/* Step 1 */}
              <FadeIn delay={0}>
                <div className="relative flex flex-col md:flex-row items-start md:items-center gap-6 md:gap-12 mb-20">
                  <div className="md:w-1/2 md:text-right md:pr-12">
                    <div className="inline-flex items-center gap-2 text-sm font-bold text-blue-600 mb-3">
                      <span className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center text-sm font-black">1</span>
                      You Grant a Scoped Token
                    </div>
                    <p className="text-slate-600 leading-relaxed">
                      When you create an agent, Grantex creates a <strong>grant token</strong> — like a keycard that only opens specific doors.
                    </p>
                    <p className="text-slate-500 text-sm mt-2 italic">
                      "This agent can READ Salesforce contacts but NOTHING else."
                    </p>
                  </div>
                  {/* Center dot */}
                  <div className="hidden md:flex absolute left-1/2 -translate-x-1/2 w-4 h-4 rounded-full bg-blue-500 border-4 border-white shadow-lg z-10" />
                  <div className="md:w-1/2 md:pl-12">
                    <div className="bg-slate-900 rounded-xl p-5 font-mono text-sm shadow-xl border border-slate-700">
                      <div className="text-slate-400 text-xs mb-2">// Grant token scopes</div>
                      <div className="flex items-center gap-2">
                        <KeyIcon className="w-4 h-4 text-amber-400 flex-shrink-0" />
                        <span className="text-emerald-400">tool:salesforce:read:*</span>
                      </div>
                      <div className="flex items-center gap-2 mt-1.5">
                        <LockIcon className="w-4 h-4 text-red-400 flex-shrink-0" />
                        <span className="text-red-400 line-through opacity-60">tool:salesforce:delete:*</span>
                      </div>
                      <div className="flex items-center gap-2 mt-1.5">
                        <LockIcon className="w-4 h-4 text-red-400 flex-shrink-0" />
                        <span className="text-red-400 line-through opacity-60">tool:salesforce:write:*</span>
                      </div>
                    </div>
                  </div>
                </div>
              </FadeIn>

              {/* Step 2 */}
              <FadeIn delay={100}>
                <div className="relative flex flex-col md:flex-row-reverse items-start md:items-center gap-6 md:gap-12 mb-20">
                  <div className="md:w-1/2 md:text-left md:pl-12">
                    <div className="inline-flex items-center gap-2 text-sm font-bold text-purple-600 mb-3">
                      <span className="w-8 h-8 rounded-full bg-purple-600 text-white flex items-center justify-center text-sm font-black">2</span>
                      Agent Asks to Use a Tool
                    </div>
                    <p className="text-slate-600 leading-relaxed">
                      The AI agent reasons about its task and decides it needs to call a tool — perhaps <code className="px-1.5 py-0.5 rounded bg-slate-100 text-sm font-mono">delete_contact</code>.
                    </p>
                  </div>
                  <div className="hidden md:flex absolute left-1/2 -translate-x-1/2 w-4 h-4 rounded-full bg-purple-500 border-4 border-white shadow-lg z-10" />
                  <div className="md:w-1/2 md:pr-12 md:text-right">
                    <div className="bg-gradient-to-br from-slate-800 to-slate-900 rounded-xl p-5 shadow-xl border border-slate-700 inline-block text-left">
                      <div className="flex items-start gap-3">
                        <div className="w-10 h-10 rounded-xl bg-purple-500/20 flex items-center justify-center flex-shrink-0">
                          <RobotIcon className="w-5 h-5 text-purple-400" />
                        </div>
                        <div>
                          <div className="text-xs text-slate-400 mb-1">Agent reasoning:</div>
                          <div className="bg-slate-700/50 rounded-lg px-3 py-2 text-sm text-slate-200 max-w-xs">
                            "I should clean up the CRM. Let me call <span className="text-amber-300 font-mono">delete_contact</span> to remove this old record."
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </FadeIn>

              {/* Step 3 */}
              <FadeIn delay={200}>
                <div className="relative flex flex-col md:flex-row items-start md:items-center gap-6 md:gap-12 mb-20">
                  <div className="md:w-1/2 md:text-right md:pr-12">
                    <div className="inline-flex items-center gap-2 text-sm font-bold text-emerald-600 mb-3">
                      <span className="w-8 h-8 rounded-full bg-emerald-600 text-white flex items-center justify-center text-sm font-black">3</span>
                      Grantex Checks the Manifest
                    </div>
                    <p className="text-slate-600 leading-relaxed">
                      Before the tool runs, Grantex looks up <code className="px-1.5 py-0.5 rounded bg-slate-100 text-sm font-mono">delete_contact</code> in its <strong>manifest</strong> and sees it requires <strong>DELETE</strong> permission. The agent only has <strong>READ</strong>.
                    </p>
                    <p className="text-red-600 font-bold mt-2 text-lg">DENIED.</p>
                  </div>
                  <div className="hidden md:flex absolute left-1/2 -translate-x-1/2 w-4 h-4 rounded-full bg-emerald-500 border-4 border-white shadow-lg z-10" />
                  <div className="md:w-1/2 md:pl-12">
                    <div className="bg-white rounded-xl border-2 border-slate-200 shadow-xl overflow-hidden">
                      <div className="bg-slate-50 px-5 py-3 border-b border-slate-200">
                        <div className="flex items-center gap-2">
                          <ClipboardCheckIcon className="w-4 h-4 text-slate-400" />
                          <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">Manifest Lookup</span>
                        </div>
                      </div>
                      <div className="p-5 space-y-3 font-mono text-sm">
                        <div className="flex items-center gap-3">
                          <span className="text-slate-400">Tool:</span>
                          <span className="text-slate-900 font-semibold">delete_contact</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-slate-400">Requires:</span>
                          <span className="px-2 py-0.5 rounded bg-red-100 text-red-700 font-bold text-xs">DELETE</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-slate-400">Agent has:</span>
                          <span className="px-2 py-0.5 rounded bg-emerald-100 text-emerald-700 font-bold text-xs">READ</span>
                        </div>
                        <div className="h-px bg-slate-200" />
                        <div className="flex items-center gap-3">
                          <span className="text-slate-400">Result:</span>
                          <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-red-50 border border-red-200">
                            <XCircle className="w-4 h-4 text-red-500" />
                            <span className="text-red-700 font-bold text-xs">DENIED</span>
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </FadeIn>

              {/* Step 4 */}
              <FadeIn delay={300}>
                <div className="relative flex flex-col md:flex-row-reverse items-start md:items-center gap-6 md:gap-12 mb-20">
                  <div className="md:w-1/2 md:text-left md:pl-12">
                    <div className="inline-flex items-center gap-2 text-sm font-bold text-indigo-600 mb-3">
                      <span className="w-8 h-8 rounded-full bg-indigo-600 text-white flex items-center justify-center text-sm font-black">4</span>
                      Permission Hierarchy
                    </div>
                    <p className="text-slate-600 leading-relaxed">
                      Higher permissions include lower ones. A <strong>WRITE</strong> token can also <strong>READ</strong>. But a <strong>READ</strong> token can never WRITE, DELETE, or do ADMIN tasks.
                    </p>
                  </div>
                  <div className="hidden md:flex absolute left-1/2 -translate-x-1/2 w-4 h-4 rounded-full bg-indigo-500 border-4 border-white shadow-lg z-10" />
                  <div className="md:w-1/2 md:pr-12">
                    {/* Pyramid */}
                    <div className="flex flex-col items-center gap-2">
                      {[
                        { label: "ADMIN", color: "bg-purple-600", width: "w-32", textColor: "text-white", desc: "Full control" },
                        { label: "DELETE", color: "bg-red-500", width: "w-44", textColor: "text-white", desc: "Remove records" },
                        { label: "WRITE", color: "bg-blue-500", width: "w-56", textColor: "text-white", desc: "Create & update" },
                        { label: "READ", color: "bg-emerald-500", width: "w-68", textColor: "text-white", desc: "View only" },
                      ].map((level, i) => (
                        <div key={i} className="flex items-center gap-3 w-full max-w-sm">
                          <div className={`${level.color} ${level.textColor} ${level.width} py-2.5 rounded-lg text-center font-bold text-sm shadow-lg transition-all hover:scale-105`}>
                            {level.label}
                          </div>
                          <span className="text-xs text-slate-400 hidden sm:inline">{level.desc}</span>
                        </div>
                      ))}
                      <div className="mt-2 flex items-center gap-2 text-xs text-slate-400">
                        <LayersIcon className="w-4 h-4" />
                        Higher levels include all permissions below
                      </div>
                    </div>
                  </div>
                </div>
              </FadeIn>

              {/* Step 5 */}
              <FadeIn delay={400}>
                <div className="relative flex flex-col md:flex-row items-start md:items-center gap-6 md:gap-12">
                  <div className="md:w-1/2 md:text-right md:pr-12">
                    <div className="inline-flex items-center gap-2 text-sm font-bold text-teal-600 mb-3">
                      <span className="w-8 h-8 rounded-full bg-teal-600 text-white flex items-center justify-center text-sm font-black">5</span>
                      All Decisions Are Logged
                    </div>
                    <p className="text-slate-600 leading-relaxed">
                      Every allow and deny is recorded with <strong>timestamp, agent, tool, connector, and reason</strong>. Compliance-ready, always auditable.
                    </p>
                  </div>
                  <div className="hidden md:flex absolute left-1/2 -translate-x-1/2 w-4 h-4 rounded-full bg-teal-500 border-4 border-white shadow-lg z-10" />
                  <div className="md:w-1/2 md:pl-12">
                    <div className="bg-slate-900 rounded-xl p-5 shadow-xl border border-slate-700 font-mono text-xs">
                      <div className="flex items-center gap-2 mb-3">
                        <AuditLogIcon className="w-4 h-4 text-teal-400" />
                        <span className="text-teal-400 font-bold text-xs uppercase tracking-wider">Audit Log</span>
                      </div>
                      {[
                        { time: "14:23:01", agent: "sales-agent", tool: "get_contact", result: "ALLOW", color: "text-emerald-400" },
                        { time: "14:23:03", agent: "sales-agent", tool: "list_deals", result: "ALLOW", color: "text-emerald-400" },
                        { time: "14:23:05", agent: "sales-agent", tool: "delete_contact", result: "DENY", color: "text-red-400" },
                        { time: "14:23:05", agent: "sales-agent", tool: "update_deal", result: "DENY", color: "text-red-400" },
                      ].map((entry, i) => (
                        <div key={i} className="flex items-center gap-3 py-1.5 border-b border-slate-800 last:border-0">
                          <span className="text-slate-500">{entry.time}</span>
                          <span className="text-slate-400">{entry.agent}</span>
                          <span className="text-slate-300">{entry.tool}</span>
                          <span className={`ml-auto font-bold ${entry.color}`}>{entry.result}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </FadeIn>
            </div>
          </div>
        </section>

        {/* ============================================================ */}
        {/* S4: BEFORE VS AFTER                                          */}
        {/* ============================================================ */}
        <section className="relative py-24 bg-slate-50 overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent" />
          <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
            <FadeIn>
              <div className="text-center mb-16">
                <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-900">
                  Before vs After
                </h2>
                <p className="mt-4 text-lg text-slate-500">
                  A clear comparison of what changes with Grantex.
                </p>
              </div>
            </FadeIn>

            <div className="grid md:grid-cols-2 gap-8">
              {/* Before */}
              <FadeIn delay={100}>
                <div className="relative h-full rounded-2xl border-2 border-red-200 bg-gradient-to-br from-red-50 to-orange-50 p-8 shadow-sm">
                  <div className="absolute -top-4 left-6">
                    <span className="bg-red-500 text-white text-xs font-bold px-4 py-1.5 rounded-full uppercase tracking-wider shadow-lg">
                      Before Grantex
                    </span>
                  </div>
                  <div className="mt-4 space-y-5">
                    {[
                      { text: "Permission guessed from tool name keywords", detail: '"process_refund" classified as a harmless READ' },
                      { text: "No enforcement layer in LangGraph pipeline", detail: "Agents call tools directly with no middleware" },
                      { text: "Keyword matching is brittle and incomplete", detail: '"generate_invoice" might mean READ or WRITE' },
                      { text: "No audit trail of permission decisions", detail: "Impossible to prove compliance after the fact" },
                    ].map((item, i) => (
                      <div key={i} className="flex gap-3">
                        <XCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
                        <div>
                          <p className="text-sm font-semibold text-slate-800">{item.text}</p>
                          <p className="text-xs text-slate-500 mt-0.5">{item.detail}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </FadeIn>

              {/* After */}
              <FadeIn delay={200}>
                <div className="relative h-full rounded-2xl border-2 border-emerald-200 bg-gradient-to-br from-emerald-50 to-teal-50 p-8 shadow-sm">
                  <div className="absolute -top-4 left-6">
                    <span className="bg-emerald-500 text-white text-xs font-bold px-4 py-1.5 rounded-full uppercase tracking-wider shadow-lg">
                      After Grantex
                    </span>
                  </div>
                  <div className="mt-4 space-y-5">
                    {[
                      { text: "Manifest-based: each tool declares its required permission", detail: "No guessing. delete_contact = DELETE. Period." },
                      { text: "Offline JWT verification at the LangGraph boundary", detail: "No network call needed. Tokens are self-contained." },
                      { text: "Sub-millisecond enforcement on every tool call", detail: "<1ms overhead. Your agents stay fast." },
                      { text: "Connector manifests for every native integration", detail: "Every tool in every connector is mapped and enforced." },
                    ].map((item, i) => (
                      <div key={i} className="flex gap-3">
                        <CheckCircle className="w-5 h-5 text-emerald-500 flex-shrink-0 mt-0.5" />
                        <div>
                          <p className="text-sm font-semibold text-slate-800">{item.text}</p>
                          <p className="text-xs text-slate-500 mt-0.5">{item.detail}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </FadeIn>
            </div>
          </div>
        </section>

        {/* ============================================================ */}
        {/* S5: REAL EXAMPLE FLOW                                        */}
        {/* ============================================================ */}
        <section className="relative py-24 bg-white overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-purple-300 to-transparent" />
          <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
            <FadeIn>
              <div className="text-center mb-16">
                <span className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-purple-50 border border-purple-200 text-purple-600 text-sm font-medium mb-6">
                  <RobotIcon className="w-4 h-4" />
                  Real Example
                </span>
                <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-900">
                  See It In Action
                </h2>
                <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
                  A real agent flow showing exactly how Grantex makes decisions.
                </p>
              </div>
            </FadeIn>

            {/* Flow steps */}
            <div className="space-y-6">
              {[
                {
                  step: "User creates Sales Agent",
                  detail: "Token scopes: tool:salesforce:read:*",
                  icon: <KeyIcon className="w-5 h-5 text-blue-500" />,
                  bg: "bg-blue-50 border-blue-200",
                  accent: "text-blue-700",
                },
                {
                  step: 'Agent thinks: "I need to get contacts"',
                  detail: "Calls get_contact",
                  icon: <RobotIcon className="w-5 h-5 text-purple-500" />,
                  bg: "bg-purple-50 border-purple-200",
                  accent: "text-purple-700",
                },
                {
                  step: "Grantex checks: get_contact needs READ",
                  detail: "Agent has READ  -->  ALLOWED",
                  icon: <CheckCircle className="w-5 h-5 text-emerald-500" />,
                  bg: "bg-emerald-50 border-emerald-200",
                  accent: "text-emerald-700",
                  badge: { text: "ALLOWED", color: "bg-emerald-100 text-emerald-700 border-emerald-300" },
                },
                {
                  step: 'Agent thinks: "Let me delete this old contact"',
                  detail: "Calls delete_contact",
                  icon: <RobotIcon className="w-5 h-5 text-purple-500" />,
                  bg: "bg-purple-50 border-purple-200",
                  accent: "text-purple-700",
                },
                {
                  step: "Grantex checks: delete_contact needs DELETE",
                  detail: "Agent has READ  -->  DENIED",
                  icon: <XCircle className="w-5 h-5 text-red-500" />,
                  bg: "bg-red-50 border-red-200",
                  accent: "text-red-700",
                  badge: { text: "DENIED", color: "bg-red-100 text-red-700 border-red-300" },
                },
                {
                  step: 'Agent responds to user',
                  detail: '"I don\'t have permission to delete contacts."',
                  icon: <ShieldIcon className="w-5 h-5 text-teal-500" />,
                  bg: "bg-teal-50 border-teal-200",
                  accent: "text-teal-700",
                },
              ].map((item, i) => (
                <FadeIn key={i} delay={i * 80}>
                  <div className="flex items-start gap-4">
                    {/* Connector line + dot */}
                    <div className="flex flex-col items-center flex-shrink-0">
                      <div className={`w-10 h-10 rounded-xl border ${item.bg} flex items-center justify-center`}>
                        {item.icon}
                      </div>
                      {i < 5 && (
                        <div className="w-px h-6 bg-slate-200 mt-1" />
                      )}
                    </div>
                    {/* Content */}
                    <div className={`flex-1 p-4 rounded-xl border ${item.bg}`}>
                      <div className="flex items-center gap-3 flex-wrap">
                        <p className={`text-sm font-bold ${item.accent}`}>{item.step}</p>
                        {item.badge && (
                          <span className={`text-xs font-bold px-2.5 py-0.5 rounded-full border ${item.badge.color}`}>
                            {item.badge.text}
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-slate-500 mt-1 font-mono">{item.detail}</p>
                    </div>
                  </div>
                </FadeIn>
              ))}
            </div>
          </div>
        </section>

        {/* ============================================================ */}
        {/* S6: KEY NUMBERS                                              */}
        {/* ============================================================ */}
        <section className="relative py-24 bg-slate-900 overflow-hidden">
          <div className="absolute inset-0 opacity-20">
            <div className="absolute top-1/3 left-1/4 w-72 h-72 bg-blue-500/30 rounded-full blur-3xl" />
            <div className="absolute bottom-1/3 right-1/4 w-72 h-72 bg-purple-500/30 rounded-full blur-3xl" />
          </div>
          <div className="relative max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
            <FadeIn>
              <div className="text-center mb-16">
                <h2 className="text-3xl sm:text-4xl font-extrabold text-white">
                  By the Numbers
                </h2>
              </div>
            </FadeIn>

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
              {[
                {
                  value: "53",
                  label: "Connector Manifests",
                  sublabel: "Every tool mapped",
                  gradient: "from-blue-500 to-blue-600",
                },
                {
                  value: "<1ms",
                  label: "Per Check",
                  sublabel: "Offline JWT verification",
                  gradient: "from-blue-500 to-blue-600",
                },
                {
                  value: "4-Level",
                  label: "Permission Hierarchy",
                  sublabel: "READ / WRITE / DELETE / ADMIN",
                  gradient: "from-emerald-500 to-emerald-600",
                },
                {
                  value: "100%",
                  label: "Accuracy",
                  sublabel: "Zero false positives",
                  gradient: "from-amber-500 to-orange-500",
                },
              ].map((stat, i) => (
                <FadeIn key={i} delay={i * 100}>
                  <div className="relative group">
                    <div className="absolute inset-0 bg-gradient-to-br from-white/5 to-white/0 rounded-2xl" />
                    <div className="relative rounded-2xl border border-white/10 bg-white/5 backdrop-blur-sm p-6 text-center hover:border-white/20 transition-all hover:bg-white/10">
                      <div className={`text-3xl sm:text-4xl font-black bg-gradient-to-r ${stat.gradient} bg-clip-text text-transparent`}>
                        {stat.value}
                      </div>
                      <div className="mt-2 text-sm font-bold text-white">{stat.label}</div>
                      <div className="mt-1 text-xs text-slate-400">{stat.sublabel}</div>
                    </div>
                  </div>
                </FadeIn>
              ))}
            </div>
          </div>
        </section>

        {/* ============================================================ */}
        {/* S7: CTA                                                      */}
        {/* ============================================================ */}
        <section className="relative py-24 bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 overflow-hidden">
          <div className="absolute inset-0 opacity-20">
            <div className="absolute top-0 left-1/3 w-96 h-96 bg-blue-500/30 rounded-full blur-3xl animate-pulse" />
            <div className="absolute bottom-0 right-1/3 w-96 h-96 bg-purple-500/30 rounded-full blur-3xl animate-pulse" style={{ animationDelay: "1.5s" }} />
          </div>
          <div className="relative max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
            <FadeIn>
              <ShieldIcon className="w-12 h-12 text-blue-400 mx-auto mb-6" />
              <h2 className="text-3xl sm:text-4xl font-extrabold text-white">
                Ready to deploy AI agents with{" "}
                <span className="bg-gradient-to-r from-blue-400 to-emerald-400 bg-clip-text text-transparent">
                  enterprise-grade security?
                </span>
              </h2>
              <p className="mt-6 text-lg text-slate-300 max-w-xl mx-auto leading-relaxed">
                Every tool call verified. Every permission enforced. Every action audited. Start building with confidence.
              </p>
            </FadeIn>

            <FadeIn delay={200}>
              <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
                <Link
                  to="/signup"
                  className="bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-8 py-3.5 rounded-xl font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25 text-sm"
                >
                  Get Started Free
                </Link>
                <Link
                  to="/dashboard"
                  className="border border-white/20 text-white px-8 py-3.5 rounded-xl font-semibold hover:bg-white/10 transition-all text-sm"
                >
                  Go to Dashboard
                </Link>
              </div>
            </FadeIn>
          </div>
        </section>

        {/* ─── Footer ─── */}
        <footer className="bg-slate-900 border-t border-slate-800 py-8">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <div className="w-6 h-6 rounded bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-[10px] font-black text-white">
                A
              </div>
              AgenticOrg
            </div>
            <div className="flex items-center gap-6 text-sm text-slate-500">
              <Link to="/" className="hover:text-slate-300 transition-colors">Home</Link>
              <Link to="/pricing" className="hover:text-slate-300 transition-colors">Pricing</Link>
              <Link to="/blog" className="hover:text-slate-300 transition-colors">Blog</Link>
              <Link to="/resources" className="hover:text-slate-300 transition-colors">Resources</Link>
            </div>
          </div>
        </footer>
      </div>
    </>
  );
}
