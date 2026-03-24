import { useState, useEffect, useCallback } from "react";

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const STATS = [
  { value: "500+", label: "tasks automated daily" },
  { value: "99.7%", label: "reconciliation accuracy" },
  { value: "4.8/5", label: "customer rating" },
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
    name: "Rajesh Mehta",
    role: "CFO",
    company: "Larsen Manufacturing",
    initials: "RM",
    gradient: "from-blue-500 to-cyan-500",
    quote:
      "Our month-end close went from 5 days to 18 hours. The AP Processor catches GST mismatches we used to find weeks later.",
    result: "72% faster close cycle",
  },
  {
    name: "Ananya Sharma",
    role: "CHRO",
    company: "Nexgen Fintech",
    initials: "AS",
    gradient: "from-purple-500 to-pink-500",
    quote:
      "Onboarding that took our HR team 2 weeks now happens in a day. PF and ESI calculations are 100% accurate.",
    result: "Zero payroll errors in 6 months",
  },
  {
    name: "Vikrant Desai",
    role: "COO",
    company: "CloudServe India",
    initials: "VD",
    gradient: "from-orange-500 to-red-500",
    quote:
      "Support triage went from 40% mis-route rate to near zero. The P1 war room auto-creation alone saved us 3 outages.",
    result: "88% first-contact resolution",
  },
  {
    name: "Priya Nair",
    role: "CMO",
    company: "ShopLocal",
    initials: "PN",
    gradient: "from-emerald-500 to-teal-500",
    quote:
      "Campaign setup that used to take our team a full week launches overnight. ROI tracking is automatic.",
    result: "3.2x campaign ROI improvement",
  },
  {
    name: "Suresh Iyer",
    role: "VP Finance",
    company: "GreenEnergy Corp",
    initials: "SI",
    gradient: "from-indigo-500 to-violet-500",
    quote:
      "Bank reconciliation runs at 4 AM every day. We went from 3 FTEs reconciling to zero manual work.",
    result: "\u20B969,800/month saved in early-pay discounts",
  },
];

/* ------------------------------------------------------------------ */
/*  Star Rating                                                        */
/* ------------------------------------------------------------------ */
function Stars() {
  return (
    <div className="flex gap-0.5">
      {[...Array(5)].map((_, i) => (
        <svg
          key={i}
          className="w-4 h-4 text-yellow-400"
          fill="currentColor"
          viewBox="0 0 20 20"
        >
          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
        </svg>
      ))}
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
      {/* Stars */}
      <Stars />

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
            Trusted by Teams Across India
          </h2>
          <p className="text-lg text-slate-400 max-w-2xl mx-auto">
            Enterprise leaders share how AgenticOrg transformed their operations.
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
              aria-label={`Show testimonial ${idx + 1}`}
            />
          ))}
        </div>
      </div>
    </section>
  );
}
