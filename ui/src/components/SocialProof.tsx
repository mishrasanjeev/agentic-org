import { useState, useEffect, useCallback } from "react";

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const STATS = [
  { value: "HITL", label: "approval for critical actions" },
  { value: "Shadow", label: "validation before promotion" },
  { value: "Evidence", label: "required for readiness claims" },
];

interface Testimonial {
  name: string;
  role: string;
  company: string;
  initials: string;
  gradient: string;
  quote: string;
  result: string;
}

const TESTIMONIALS: Testimonial[] = [
  {
    name: "Finance workflow",
    role: "Illustrative CFO scenario",
    company: "Sample manufacturer",
    initials: "FW",
    gradient: "from-blue-500 to-cyan-500",
    quote:
      "An AP reviewer surfaces invoice and GST mismatches while a governed close checklist keeps approvals and exceptions visible.",
    result: "Illustrative workflow — results vary",
  },
  {
    name: "People workflow",
    role: "Illustrative CHRO scenario",
    company: "Sample services company",
    initials: "PW",
    gradient: "from-purple-500 to-pink-500",
    quote:
      "An onboarding coordinator routes document, provisioning, and policy tasks while payroll exceptions remain subject to review.",
    result: "Illustrative workflow — results vary",
  },
  {
    name: "Operations workflow",
    role: "Illustrative COO scenario",
    company: "Sample technology company",
    initials: "OW",
    gradient: "from-orange-500 to-red-500",
    quote:
      "A support triage agent proposes priority and routing, then escalates high-impact incidents to the designated human owner.",
    result: "Illustrative workflow — results vary",
  },
  {
    name: "Marketing workflow",
    role: "Illustrative CMO scenario",
    company: "Sample retailer",
    initials: "MW",
    gradient: "from-emerald-500 to-teal-500",
    quote:
      "A campaign agent prepares audience, copy, and measurement drafts, with spend and publishing held for explicit approval.",
    result: "Illustrative workflow — results vary",
  },
  {
    name: "Reconciliation workflow",
    role: "Illustrative finance scenario",
    company: "Sample energy company",
    initials: "RW",
    gradient: "from-indigo-500 to-violet-500",
    quote:
      "A reconciliation agent proposes matches, flags stale items, and sends unresolved exceptions to an accountable reviewer.",
    result: "Illustrative workflow — results vary",
  },
];

/* ------------------------------------------------------------------ */
/*  Star Rating                                                        */
/* ------------------------------------------------------------------ */
function ScenarioBadge() {
  return (
    <div className="inline-flex rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-cyan-300">
      Illustrative scenario
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Single Testimonial Card                                            */
/* ------------------------------------------------------------------ */
function TestimonialCard({
  testimonial,
  isActive,
}: {
  testimonial: Testimonial;
  isActive: boolean;
}) {
  return (
    <div
      className={`bg-slate-800/60 backdrop-blur-sm border border-slate-700/50 rounded-2xl p-6 sm:p-8 transition-all duration-700 ease-in-out ${
        isActive
          ? "opacity-100 scale-100 translate-y-0"
          : "opacity-0 scale-95 translate-y-4 absolute inset-0 pointer-events-none"
      }`}
    >
      <ScenarioBadge />

      {/* Quote */}
      <blockquote className="mt-4 text-white text-base sm:text-lg leading-relaxed font-medium">
        &ldquo;{testimonial.quote}&rdquo;
      </blockquote>

      {/* Result badge */}
      <div className="mt-4 inline-flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 rounded-full px-4 py-1.5">
        <svg
          className="w-4 h-4 text-emerald-400 flex-shrink-0"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
          />
        </svg>
        <span className="text-sm font-semibold text-emerald-400">
          {testimonial.result}
        </span>
      </div>

      {/* Author */}
      <div className="mt-6 flex items-center gap-3">
        <div
          className={`w-10 h-10 rounded-full bg-gradient-to-br ${testimonial.gradient} flex items-center justify-center text-white text-sm font-bold flex-shrink-0`}
        >
          {testimonial.initials}
        </div>
        <div>
          <div className="text-white font-semibold text-sm">
            {testimonial.name}
          </div>
          <div className="text-slate-400 text-xs">
            {testimonial.role}, {testimonial.company}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SocialProof — main export                                          */
/* ------------------------------------------------------------------ */
export default function SocialProof() {
  // Show 3 cards at a time on large screens, rotate the "middle" card
  // On mobile, show 1 card at a time
  const [activeSet, setActiveSet] = useState(0);
  const [isPaused, setIsPaused] = useState(false);

  // We show 3 cards, cycling through sets of 3
  // Set 0: indices 0,1,2   Set 1: indices 1,2,3   etc.
  // Actually, let's rotate one card at a time for a smoother feel:
  // We'll show 3 visible cards, and every 5 seconds shift which 3 are visible.
  const totalTestimonials = TESTIMONIALS.length;

  const getVisibleIndices = useCallback(
    (base: number): number[] => {
      return [
        base % totalTestimonials,
        (base + 1) % totalTestimonials,
        (base + 2) % totalTestimonials,
      ];
    },
    [totalTestimonials]
  );

  useEffect(() => {
    if (isPaused) return;
    const timer = setInterval(() => {
      setActiveSet((prev) => (prev + 1) % totalTestimonials);
    }, 5000);
    return () => clearInterval(timer);
  }, [isPaused, totalTestimonials]);

  const visibleIndices = getVisibleIndices(activeSet);

  return (
    <section className="py-24 bg-slate-900 scroll-mt-16">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* -------- Section Header -------- */}
        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
            Explore Governed Operating Scenarios
          </h2>
          <p className="text-lg text-slate-400 max-w-2xl mx-auto">
            These fictional examples show intended workflows, not customer testimonials or
            guaranteed outcomes. Availability depends on configuration and readiness evidence.
          </p>
        </div>

        {/* -------- Stat Bar -------- */}
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4 sm:gap-0 mb-16">
          {STATS.map((stat, i) => (
            <div
              key={stat.label}
              className={`flex items-center gap-3 px-6 sm:px-8 py-3 ${
                i < STATS.length - 1
                  ? "sm:border-r sm:border-slate-700"
                  : ""
              }`}
            >
              <span className="text-2xl sm:text-3xl font-extrabold text-white">
                {stat.value}
              </span>
              <span className="text-sm text-slate-400">{stat.label}</span>
            </div>
          ))}
        </div>

        {/* -------- Testimonial Cards — Desktop: 3 visible -------- */}
        <div
          className="hidden lg:grid lg:grid-cols-3 gap-6"
          onMouseEnter={() => setIsPaused(true)}
          onMouseLeave={() => setIsPaused(false)}
        >
          {visibleIndices.map((idx) => (
            <div key={`desktop-${idx}-${TESTIMONIALS[idx].name}`} className="relative">
              <TestimonialCard testimonial={TESTIMONIALS[idx]} isActive />
            </div>
          ))}
        </div>

        {/* -------- Testimonial Cards — Mobile/Tablet: 1 visible -------- */}
        <div
          className="lg:hidden relative min-h-[320px]"
          onMouseEnter={() => setIsPaused(true)}
          onMouseLeave={() => setIsPaused(false)}
        >
          {TESTIMONIALS.map((t, idx) => (
            <TestimonialCard
              key={t.name}
              testimonial={t}
              isActive={idx === visibleIndices[0]}
            />
          ))}
        </div>

        {/* -------- Dot Navigation -------- */}
        <div className="flex items-center justify-center gap-2 mt-8">
          {TESTIMONIALS.map((_, idx) => (
            <button
              key={idx}
              onClick={() => setActiveSet(idx)}
              className={`w-2.5 h-2.5 rounded-full transition-all duration-300 ${
                visibleIndices.includes(idx)
                  ? "bg-blue-500 w-6"
                  : "bg-slate-600 hover:bg-slate-500"
              }`}
              aria-label={`Show illustrative scenario ${idx + 1}`}
            />
          ))}
        </div>
      </div>
    </section>
  );
}
