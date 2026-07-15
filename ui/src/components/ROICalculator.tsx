import { useState } from "react";

/* ------------------------------------------------------------------ */
/*  ROI Calculator — Interactive savings estimator for agenticorg.ai   */
/* ------------------------------------------------------------------ */

interface DomainSavings {
  domain: string;
  emoji: string;
  color: string;
  gradient: string;
  metrics: { label: string; before: string; after: string }[];
}

const DOMAIN_DATA: DomainSavings[] = [
  {
    domain: "Finance",
    emoji: "\u{1F4B0}",
    color: "text-emerald-600",
    gradient: "from-emerald-500 to-teal-600",
    metrics: [
      { label: "Invoice Processing", before: "Manual baseline", after: "Assisted review" },
      { label: "Bank Reconciliation", before: "Manual matching", after: "Proposed matches" },
      { label: "Month-end Close", before: "Fragmented checklist", after: "Coordinated workflow" },
    ],
  },
  {
    domain: "HR",
    emoji: "\u{1F465}",
    color: "text-blue-600",
    gradient: "from-blue-500 to-indigo-600",
    metrics: [
      { label: "Employee Onboarding", before: "Manual handoffs", after: "Routed tasks" },
      { label: "Payroll Processing", before: "Manual review", after: "Exception queue" },
      { label: "Leave Management", before: "Manual approval chains", after: "Auto-routed" },
    ],
  },
  {
    domain: "Operations",
    emoji: "\u2699\uFE0F",
    color: "text-orange-600",
    gradient: "from-orange-500 to-red-600",
    metrics: [
      { label: "L1 Support", before: "Manual triage", after: "Routing suggestions" },
      { label: "Vendor Onboarding", before: "Manual checklist", after: "Guided workflow" },
      { label: "IT Incident Response", before: "Scattered context", after: "Prepared context" },
    ],
  },
  {
    domain: "Marketing",
    emoji: "\u{1F4E3}",
    color: "text-purple-600",
    gradient: "from-purple-500 to-pink-600",
    metrics: [
      { label: "Campaign Optimization", before: "Manual review", after: "Reviewable proposals" },
      { label: "Content Production", before: "Blank-page start", after: "Draft workflow" },
      { label: "SEO & Analytics", before: "Scattered reports", after: "Shared workspace" },
    ],
  },
];

const DEFAULT_EMPLOYEES = 500;
const DEFAULT_INVOICES = 2000;
const DEFAULT_TICKETS = 5000;
const DEFAULT_CLOSE_DAYS = 7;

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
function DomainCard({ data }: { data: DomainSavings }) {
  return (
    <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden hover:shadow-lg transition-all duration-300">
      <div className={`h-1.5 bg-gradient-to-r ${data.gradient}`} />
      <div className="p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span className="text-2xl">{data.emoji}</span>
            <h3 className="font-bold text-slate-900">{data.domain}</h3>
          </div>
          <span className={`text-sm font-semibold ${data.color}`}>Pilot worksheet</span>
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

  return (
    <section id="roi-calculator" className="py-24 bg-white scroll-mt-16">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Header */}
        <div className="text-center mb-16">
          <div className="inline-flex items-center gap-2 bg-blue-50 text-blue-700 rounded-full px-4 py-1.5 text-sm font-medium mb-4">
            Planning Scenario
          </div>
          <h2 className="text-3xl sm:text-4xl font-bold text-slate-900">
            Explore an Illustrative Capacity Scenario
          </h2>
          <p className="mt-4 text-lg text-slate-500 max-w-2xl mx-auto">
            Adjust the sliders to explore a pilot workload model. Outputs are illustrative,
            not measured customer results or guaranteed savings.
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
                <div className="text-sm font-medium text-slate-600 mb-2">Month-end Close Baseline</div>
                <div className="flex items-center justify-between">
                  <div className="text-center">
                    <div className="text-2xl font-bold text-red-500">D+{closeDays}</div>
                    <div className="text-xs text-slate-400">Pilot input</div>
                  </div>
                  <svg className="w-8 h-8 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                  </svg>
                  <div className="text-center">
                    <div className="text-sm font-bold text-emerald-600">Set after evidence</div>
                    <div className="text-xs text-slate-400">Validated target</div>
                  </div>
                </div>
                <div className="text-center mt-2">
                  <span className="text-sm font-semibold text-blue-600">No improvement is assumed</span>
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
                  <div className="text-blue-200 text-sm font-medium mb-1">Monthly Invoice Baseline</div>
                  <div className="text-3xl sm:text-4xl font-extrabold">{invoices.toLocaleString("en-IN")}</div>
                  <div className="text-blue-200 text-sm mt-1">user-entered pilot input</div>
                </div>
                <div>
                  <div className="text-blue-200 text-sm font-medium mb-1">Monthly Ticket Baseline</div>
                  <div className="text-3xl sm:text-4xl font-extrabold">{tickets.toLocaleString("en-IN")}</div>
                  <div className="text-blue-200 text-sm mt-1">user-entered pilot input</div>
                </div>
              </div>

              {/* Before / After summary bar */}
              <div className="mt-6 pt-6 border-t border-white/20 grid grid-cols-3 gap-4 text-center">
                <div>
                  <div className="text-2xl font-bold">Review</div>
                  <div className="text-blue-200 text-xs">Human validation</div>
                </div>
                <div>
                  <div className="text-2xl font-bold">HITL</div>
                  <div className="text-blue-200 text-xs">Critical actions</div>
                </div>
                <div>
                  <div className="text-2xl font-bold">Policy</div>
                  <div className="text-blue-200 text-xs">Governed promotion</div>
                </div>
              </div>
            </div>

            {/* Domain cards grid */}
            <div className="grid sm:grid-cols-2 gap-4">
              {DOMAIN_DATA.map((d) => (
                <DomainCard key={d.domain} data={d} />
              ))}
            </div>

            {/* Footnote */}
            <p className="text-xs text-slate-400 text-center mt-4">
              Pilot worksheet only — not a quote, guarantee, benchmark, savings projection, or
              measured customer outcome. Set targets only after validating them with your own data.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
