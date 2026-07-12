import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

interface AdsPageProps {
  keyword: string;
  headline: string;
  subheadline: string;
  painPoint: string;
  metric: string;
  metricLabel: string;
  features: string[];
  ctaText: string;
  testimonial: { quote: string; name: string; role: string; result: string };
  metaTitle: string;
  metaDescription: string;
  slug: string;
}

const PAGES: Record<string, AdsPageProps> = {
  "ai-invoice-processing": {
    keyword: "AI Invoice Processing",
    headline: "Evaluate AI-Assisted Invoice Processing with Your Controls",
    subheadline: "Pilot extraction, configured validation, proposed matching, approval, and accounting handoff on representative invoices. Results depend on documents, providers, scopes, and policy.",
    painPoint: "Invoice delays often come from data quality, missing purchase records, approval queues, and provider access. Establish your own baseline before estimating value.",
    metric: "Measured",
    metricLabel: "Report field quality, exceptions, reviewer effort, and cycle time from your pilot",
    features: [
      "Extract candidate fields while preserving the source document",
      "Request GST validation only through configured, authoritative provider access",
      "Propose invoice, PO, and receipt matches using tenant-defined tolerances",
      "Route ambiguous, material, or policy-sensitive cases to review",
      "Keep journal posting and payment behind separate scopes and approvals",
      "Record corrections, provider failures, and source-system confirmation",
    ],
    ctaText: "Request an AP Evaluation",
    testimonial: {
      quote: "Which document classes can meet our field-quality threshold, and which exceptions must remain with reviewers?",
      name: "Illustrative pilot question", role: "Not a customer testimonial",
      result: "Publish sample, denominator, configuration, errors, and limitations",
    },
    metaTitle: "AI-Assisted Invoice Processing Evaluation | AgenticOrg",
    metaDescription: "Evaluate invoice field extraction, configured validation, proposed matching, exception review, approval, and accounting handoffs with representative data.",
    slug: "ai-invoice-processing",
  },
  "automated-bank-reconciliation": {
    keyword: "Automated Bank Reconciliation",
    headline: "Evaluate Evidence-Backed Bank Reconciliation",
    subheadline: "Compare exact and fuzzy candidate matches, break explanations, and reviewer decisions on representative bank and ledger data.",
    painPoint: "References, timing, currencies, partial payments, fees, reversals, and entity boundaries make reconciliation quality specific to each organization.",
    metric: "Validated",
    metricLabel: "Measure false matches, missed matches, reviewer agreement, and exception severity",
    features: [
      "Read authorized bank and ledger records from configured sources",
      "Attach source evidence and rationale to each candidate match",
      "Keep fees, timing differences, and partial payments as reviewable hypotheses",
      "Route material, stale, intercompany, or ambiguous items by policy",
      "Treat the bank and ledger as authoritative sources of record",
      "Re-test after model, prompt, rule, format, or connector changes",
    ],
    ctaText: "Request a Reconciliation Evaluation",
    testimonial: {
      quote: "Which match classes are reliable on our statements, and what is the impact of the false matches?",
      name: "Illustrative pilot question", role: "Not a customer testimonial",
      result: "Measure on representative periods and risk classes",
    },
    metaTitle: "Evidence-Backed Bank Reconciliation Evaluation | AgenticOrg",
    metaDescription: "Evaluate candidate bank-to-ledger matches, source evidence, confidence, false-match risk, exception handling, and reviewer decisions on representative periods.",
    slug: "automated-bank-reconciliation",
  },
  "payroll-automation": {
    keyword: "Payroll Automation",
    headline: "Evaluate AI-Assisted Payroll Preparation for India",
    subheadline: "Test candidate calculations, statutory-rule configuration, anomaly checks, and HR review without treating a draft as a completed payroll run.",
    painPoint: "Payroll depends on current rules, accurate attendance and employee data, HR approval, provider availability, and reconciliation to systems of record.",
    metric: "Reviewed",
    metricLabel: "Compare calculations, exceptions, reviewer corrections, and source-system results",
    features: [
      "Prepare candidate gross-pay and deduction calculations from approved inputs",
      "Verify current PF, ESI, TDS, and state rules with qualified owners",
      "Flag missing attendance, leave, identity, or salary data for review",
      "Keep payslip, EPFO, HRMS, and payment actions separately authorized",
      "Require accountable HR approval under the configured policy",
      "Reconcile provider and payroll-system results before reporting completion",
    ],
    ctaText: "Request a Payroll Evaluation",
    testimonial: {
      quote: "Which employee and payroll cases match accepted calculations, and where do current rules or source data require correction?",
      name: "Illustrative pilot question", role: "Not a customer testimonial",
      result: "No zero-error or regulator-outcome guarantee",
    },
    metaTitle: "AI-Assisted Payroll Preparation Evaluation | AgenticOrg",
    metaDescription: "Evaluate candidate payroll calculations, current statutory-rule configuration, anomaly checks, provider boundaries, reconciliation, and accountable HR review.",
    slug: "payroll-automation",
  },
};

function DemoForm({ ctaText, slug }: { ctaText: string; slug: string }) {
  const [form, setForm] = useState({ name: "", email: "", company: "", role: "", phone: "" });
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!form.name.trim() || !form.email.trim()) { setError("Name and email are required"); return; }
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch("/api/v1/demo-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, source: `ads_${slug}` }),
      });
      if (!response.ok) throw new Error("Demo request was not accepted");
      setDone(true);
    } catch { setError("Something went wrong. Try again."); }
    finally { setSubmitting(false); }
  };

  if (done) {
    return (
      <div className="text-center py-8">
        <div className="w-16 h-16 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-4">
          <svg className="w-8 h-8 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <h3 className="text-xl font-bold text-slate-900 mb-2">Request received</h3>
        <p className="text-slate-600">We will review the request and contact you using the details provided. No response time is guaranteed unless an executed support agreement says otherwise.</p>
        <Link to="/playground" className="inline-block mt-4 text-blue-600 font-semibold hover:underline">Try the Playground while you wait</Link>
      </div>
    );
  }

  const inputClass = "w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 outline-none transition-all";

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <input required type="text" placeholder="Your name *" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputClass} />
      <input required type="email" placeholder="Work email *" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={inputClass} />
      <div className="grid grid-cols-2 gap-3">
        <input type="text" placeholder="Company" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className={inputClass} />
        <input type="text" placeholder="Role (e.g., CFO)" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className={inputClass} />
      </div>
      <input type="tel" placeholder="Phone (optional)" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} className={inputClass} />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button type="submit" disabled={submitting} className="w-full bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-6 py-3 rounded-lg text-sm font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25 disabled:opacity-60">
        {submitting ? "Submitting..." : ctaText}
      </button>
      <p className="text-xs text-slate-400 text-center">A demo request does not require a credit card and does not activate integrations.</p>
    </form>
  );
}

export default function AdsLanding() {
  const slug = window.location.pathname.split("/").pop() || "ai-invoice-processing";
  const page = PAGES[slug] || PAGES["ai-invoice-processing"];

  return (
    <div className="min-h-screen bg-white">

      {/* Minimal nav */}
      <nav className="bg-white border-b px-4 py-3">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-xs">AO</div>
            <span className="font-semibold text-slate-900">AgenticOrg</span>
          </Link>
          <Link to="/playground" className="text-sm text-blue-600 font-medium hover:underline">Try Playground</Link>
        </div>
      </nav>

      {/* Hero + Form */}
      <section className="py-16 lg:py-24">
        <div className="max-w-5xl mx-auto px-4 grid lg:grid-cols-2 gap-12 items-start">
          {/* Left — Copy */}
          <div>
            <span className="inline-block bg-blue-50 text-blue-700 text-xs font-semibold px-3 py-1 rounded-full mb-4">{page.keyword}</span>
            <h1 className="text-3xl sm:text-4xl font-extrabold text-slate-900 leading-tight">{page.headline}</h1>
            <p className="mt-4 text-lg text-slate-600">{page.subheadline}</p>

            {/* Big metric */}
            <div className="mt-8 flex items-center gap-4 bg-slate-50 rounded-xl p-5 border">
              <div className="text-4xl font-extrabold text-blue-600">{page.metric}</div>
              <div className="text-sm text-slate-600">{page.metricLabel}</div>
            </div>

            {/* Pain point */}
            <p className="mt-6 text-slate-500 text-sm italic">"{page.painPoint}"</p>
          </div>

          {/* Right — Form */}
          <div className="bg-white rounded-2xl border-2 border-slate-200 p-6 shadow-lg sticky top-24">
            <h2 className="text-lg font-bold text-slate-900 mb-1">Request a scoped evaluation</h2>
            <p className="text-sm text-slate-500 mb-4">Share your workflow and evaluation goals. Response times vary by request and plan.</p>
            <DemoForm ctaText={page.ctaText} slug={page.slug} />
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-16 bg-slate-50">
        <div className="max-w-3xl mx-auto px-4">
          <h2 className="text-2xl font-bold text-slate-900 text-center mb-8">How it works</h2>
          <div className="space-y-4">
            {page.features.map((f, i) => (
              <div key={i} className="flex items-start gap-3">
                <div className="w-6 h-6 rounded-full bg-emerald-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <svg className="w-3.5 h-3.5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <p className="text-slate-700 text-sm">{f}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Evidence prompt */}
      <section className="py-16">
        <div className="max-w-3xl mx-auto px-4 text-center">
          <p className="text-xs font-semibold uppercase tracking-wider text-blue-600">Illustrative evaluation prompt</p>
          <blockquote className="mt-4 text-xl text-slate-800 font-medium leading-relaxed">
            "{page.testimonial.quote}"
          </blockquote>
          <div className="mt-4">
            <p className="font-semibold text-slate-900">{page.testimonial.name}</p>
            <p className="text-sm text-slate-500">{page.testimonial.role}</p>
          </div>
          <div className="mt-3 inline-block bg-blue-50 text-blue-700 text-sm font-semibold px-4 py-1.5 rounded-full">
            Evaluation note: {page.testimonial.result}
          </div>
        </div>
      </section>

      {/* Bottom CTA */}
      <section className="py-16 bg-slate-900">
        <div className="max-w-xl mx-auto px-4 text-center">
          <h2 className="text-2xl font-bold text-white mb-4">Ready to evaluate the workflow?</h2>
          <p className="text-slate-400 mb-6">Use synthetic samples first, then validate integrations, scopes, approvals, and outcomes in your environment.</p>
          <div className="flex justify-center gap-4">
            <Link to="/signup" className="bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-8 py-3 rounded-xl text-sm font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg">Create an Account</Link>
            <Link to="/playground" className="border border-slate-600 text-slate-300 px-8 py-3 rounded-xl text-sm font-semibold hover:bg-slate-800 transition-all">Try Playground</Link>
          </div>
        </div>
      </section>
    </div>
  );
}
