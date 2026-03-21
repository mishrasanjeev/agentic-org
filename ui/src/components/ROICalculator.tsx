import { useState, useEffect, useRef, useCallback } from "react";

/* ------------------------------------------------------------------ */
/*  ROI Calculator — Interactive savings estimator for agenticorg.ai   */
/* ------------------------------------------------------------------ */

interface DomainSavings {
  domain: string;
  emoji: string;
  weeklyBase: number; // base weekly savings in INR for default inputs
  color: string;
  gradient: string;
  metrics: { label: string; before: string; after: string }[];
}

const DOMAIN_DATA: DomainSavings[] = [
  {
    domain: "Finance",
    emoji: "\u{1F4B0}",
    weeklyBase: 320000, // 3.2L
    color: "text-emerald-600",
    gradient: "from-emerald-500 to-teal-600",
    metrics: [
      { label: "Invoice Processing", before: "30 min/invoice", after: "2 min/invoice" },
      { label: "Bank Reconciliation", before: "T+2, 80% accuracy", after: "T+0, 99.7% accuracy" },
      { label: "Month-end Close", before: "D+7", after: "D+2" },
    ],
  },
  {
    domain: "HR",
    emoji: "\u{1F465}",
    weeklyBase: 180000, // 1.8L
    color: "text-blue-600",
    gradient: "from-blue-500 to-indigo-600",
    metrics: [
      { label: "Employee Onboarding", before: "3 days", after: "Instant (Day-0)" },
      { label: "Payroll Processing", before: "2 days", after: "4 hours" },
      { label: "Leave Management", before: "Manual approval chains", after: "Auto-routed" },
    ],
  },
  {
    domain: "Operations",
    emoji: "\u2699\uFE0F",
    weeklyBase: 210000, // 2.1L
    color: "text-orange-600",
    gradient: "from-orange-500 to-red-600",
    metrics: [
      { label: "L1 Support", before: "Manual triage", after: "65% auto-contained" },
      { label: "Vendor Onboarding", before: "3 days", after: "4 hours" },
      { label: "IT Incident Response", before: "Hours", after: "Minutes" },
    ],
  },
  {
    domain: "Marketing",
    emoji: "\u{1F4E3}",
    weeklyBase: 90000, // 0.9L
    color: "text-purple-600",
    gradient: "from-purple-500 to-pink-600",
    metrics: [
      { label: "Campaign Optimization", before: "Weekly manual review", after: "Real-time AI tuning" },
      { label: "Content Production", before: "5 days/piece", after: "Same-day" },
      { label: "SEO & Analytics", before: "Monthly reports", after: "Continuous monitoring" },
    ],
  },
];

const DEFAULT_EMPLOYEES = 500;
const DEFAULT_INVOICES = 2000;
const DEFAULT_TICKETS = 5000;
const DEFAULT_CLOSE_DAYS = 7;

/* Scaling multipliers relative to the default input values */
function computeScale(employees: number, invoices: number, tickets: number): Record<string, number> {
  return {
    Finance: invoices / DEFAULT_INVOICES,
    HR: employees / DEFAULT_EMPLOYEES,
    Operations: tickets / DEFAULT_TICKETS,
    Marketing: employees / DEFAULT_EMPLOYEES,
  };
}

function formatINR(value: number): string {
  if (value >= 10000000) {
    return `\u20B9${(value / 10000000).toFixed(1)} Cr`;
  }
  if (value >= 100000) {
    return `\u20B9${(value / 100000).toFixed(1)}L`;
  }
  if (value >= 1000) {
    return `\u20B9${(value / 1000).toFixed(1)}K`;
  }
  return `\u20B9${value.toFixed(0)}`;
}

/* ------------------------------------------------------------------ */
/*  Animated counter hook                                              */
/* ------------------------------------------------------------------ */
function useAnimatedValue(target: number, duration = 600): number {
  const [display, setDisplay] = useState(target);
  const animRef = useRef<number | null>(null);
  const startRef = useRef(target);
  const startTime = useRef(0);

  useEffect(() => {
    startRef.current = display;
    startTime.current = performance.now();

    const animate = (now: number) => {
      const elapsed = now - startTime.current;
      const progress = Math.min(elapsed / duration, 1);
      // Ease-out cubic
      const ease = 1 - Math.pow(1 - progress, 3);
      const current = startRef.current + (target - startRef.current) * ease;
      setDisplay(current);
      if (progress < 1) {
        animRef.current = requestAnimationFrame(animate);
      }
    };

    animRef.current = requestAnimationFrame(animate);
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration]);

  return display;
}

/* ------------------------------------------------------------------ */
/*  Slider input component                                             */
/* ------------------------------------------------------------------ */
function SliderInput({
  label,
  value,
  onChange,
  min,
  max,
  step,
  suffix,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  suffix?: string;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium text-slate-700">{label}</label>
        <span className="text-sm font-bold text-blue-600">
          {value.toLocaleString("en-IN")}
          {suffix && <span className="text-slate-400 font-normal"> {suffix}</span>}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
      />
      <div className="flex justify-between text-xs text-slate-400">
        <span>{min.toLocaleString("en-IN")}</span>
        <span>{max.toLocaleString("en-IN")}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Domain savings card                                                */
/* ------------------------------------------------------------------ */
function DomainCard({
  data,
  weeklySaving,
}: {
  data: DomainSavings;
  weeklySaving: number;
}) {
  const animatedValue = useAnimatedValue(weeklySaving);

  return (
    <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden hover:shadow-lg transition-all duration-300">
      <div className={`h-1.5 bg-gradient-to-r ${data.gradient}`} />
      <div className="p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span className="text-2xl">{data.emoji}</span>
            <h3 className="font-bold text-slate-900">{data.domain}</h3>
          </div>
          <span className={`text-lg font-bold ${data.color}`}>
            {formatINR(animatedValue)}
            <span className="text-xs font-normal text-slate-400">/week</span>
          </span>
        </div>

        <div className="space-y-2.5">
          {data.metrics.map((m) => (
            <div key={m.label} className="flex items-start gap-2 text-sm">
              <svg
                className="w-4 h-4 text-emerald-500 flex-shrink-0 mt-0.5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <div>
                <span className="font-medium text-slate-700">{m.label}</span>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-red-400 line-through text-xs">{m.before}</span>
                  <svg className="w-3 h-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                  </svg>
                  <span className="text-emerald-600 font-medium text-xs">{m.after}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main ROI Calculator component                                      */
/* ------------------------------------------------------------------ */
export default function ROICalculator() {
  const [employees, setEmployees] = useState(DEFAULT_EMPLOYEES);
  const [invoices, setInvoices] = useState(DEFAULT_INVOICES);
  const [tickets, setTickets] = useState(DEFAULT_TICKETS);
  const [closeDays, setCloseDays] = useState(DEFAULT_CLOSE_DAYS);

  const scales = computeScale(employees, invoices, tickets);

  const domainWeeklySavings = useCallback(
    (domain: string, base: number) => Math.round(base * (scales[domain] || 1)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [employees, invoices, tickets]
  );

  const totalWeekly = DOMAIN_DATA.reduce(
    (sum, d) => sum + domainWeeklySavings(d.domain, d.weeklyBase),
    0
  );
  const totalAnnual = totalWeekly * 52;
  const newCloseDays = Math.max(1, Math.round(closeDays * (2 / 7)));

  const animatedWeekly = useAnimatedValue(totalWeekly);
  const animatedAnnual = useAnimatedValue(totalAnnual);

  return (
    <section id="roi-calculator" className="py-24 bg-white scroll-mt-16">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Header */}
        <div className="text-center mb-16">
          <div className="inline-flex items-center gap-2 bg-blue-50 text-blue-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
            ROI Calculator
          </div>
          <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">
            Calculate Your Savings with AgenticOrg
          </h2>
          <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
            Adjust the sliders to match your organization. See real-time savings estimates across
            every department.
          </p>
        </div>

        <div className="grid lg:grid-cols-12 gap-8">
          {/* Left: Input controls */}
          <div className="lg:col-span-4">
            <div className="bg-slate-50 rounded-2xl border border-slate-200 p-6 space-y-6 sticky top-24">
              <h3 className="font-bold text-slate-900 text-lg">Your Organization</h3>

              <SliderInput
                label="Number of Employees"
                value={employees}
                onChange={setEmployees}
                min={50}
                max={10000}
                step={50}
              />
              <SliderInput
                label="Monthly Invoices Processed"
                value={invoices}
                onChange={setInvoices}
                min={100}
                max={50000}
                step={100}
              />
              <SliderInput
                label="Support Tickets / Month"
                value={tickets}
                onChange={setTickets}
                min={500}
                max={100000}
                step={500}
              />
              <SliderInput
                label="Current Month-end Close"
                value={closeDays}
                onChange={setCloseDays}
                min={3}
                max={15}
                step={1}
                suffix="days"
              />

              {/* Month-end close improvement */}
              <div className="bg-white rounded-xl border border-slate-200 p-4">
                <div className="text-sm font-medium text-slate-600 mb-2">Month-end Close Improvement</div>
                <div className="flex items-center justify-between">
                  <div className="text-center">
                    <div className="text-2xl font-bold text-red-500">D+{closeDays}</div>
                    <div className="text-xs text-slate-400">Current</div>
                  </div>
                  <svg className="w-8 h-8 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                  </svg>
                  <div className="text-center">
                    <div className="text-2xl font-bold text-emerald-600">D+{newCloseDays}</div>
                    <div className="text-xs text-slate-400">With AgenticOrg</div>
                  </div>
                </div>
                <div className="text-center mt-2">
                  <span className="text-sm font-semibold text-blue-600">
                    {closeDays - newCloseDays} days faster
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Right: Results */}
          <div className="lg:col-span-8 space-y-6">
            {/* Summary banner */}
            <div className="bg-gradient-to-r from-blue-600 to-indigo-700 rounded-2xl p-6 sm:p-8 text-white">
              <div className="grid sm:grid-cols-2 gap-6">
                <div>
                  <div className="text-blue-200 text-sm font-medium mb-1">Estimated Weekly Savings</div>
                  <div className="text-3xl sm:text-4xl font-extrabold">
                    {formatINR(Math.round(animatedWeekly))}
                  </div>
                  <div className="text-blue-200 text-sm mt-1">per week across all departments</div>
                </div>
                <div>
                  <div className="text-blue-200 text-sm font-medium mb-1">Estimated Annual Savings</div>
                  <div className="text-3xl sm:text-4xl font-extrabold">
                    {formatINR(Math.round(animatedAnnual))}
                  </div>
                  <div className="text-blue-200 text-sm mt-1">projected over 52 weeks</div>
                </div>
              </div>

              {/* Before / After summary bar */}
              <div className="mt-6 pt-6 border-t border-white/20 grid grid-cols-3 gap-4 text-center">
                <div>
                  <div className="text-2xl font-bold">15x</div>
                  <div className="text-blue-200 text-xs">Faster Invoicing</div>
                </div>
                <div>
                  <div className="text-2xl font-bold">65%</div>
                  <div className="text-blue-200 text-xs">L1 Auto-Containment</div>
                </div>
                <div>
                  <div className="text-2xl font-bold">99.7%</div>
                  <div className="text-blue-200 text-xs">Recon Accuracy</div>
                </div>
              </div>
            </div>

            {/* Domain cards grid */}
            <div className="grid sm:grid-cols-2 gap-4">
              {DOMAIN_DATA.map((d) => (
                <DomainCard
                  key={d.domain}
                  data={d}
                  weeklySaving={domainWeeklySavings(d.domain, d.weeklyBase)}
                />
              ))}
            </div>

            {/* Footnote */}
            <p className="text-xs text-slate-400 text-center mt-4">
              Based on AgenticOrg PRD v4.0 benchmarks. Actual savings may vary based on organization
              size, process complexity, and implementation scope. All INR values are estimates.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
