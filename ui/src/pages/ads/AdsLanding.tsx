import { useState, type FormEvent } from "react";
import { Helmet } from "react-helmet-async";
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
    headline: "Stop Losing ₹69,800/Month on Late Invoice Payments",
    subheadline: "AI processes invoices in 11 seconds — OCR, GSTIN validation, 3-way match, GL posting. No manual work.",
    painPoint: "Your AP team spends 5 days on month-end close. Invoices sit in approval queues. Early-payment discounts expire unclaimed.",
    metric: "11 sec",
    metricLabel: "Per invoice — from receipt to GL posting",
    features: [
      "OCR extracts invoice data from any PDF format",
      "GSTIN validated against government portal in real-time",
      "3-way match: Invoice vs PO vs GRN (2% tolerance, configurable)",
      "Payment auto-scheduled to capture early-pay discounts",
      "Journal entry posted to GL with idempotency (no duplicates)",
      "HITL approval for invoices above your threshold (e.g., ₹5L)",
    ],
    ctaText: "See AP Automation in Action",
    testimonial: {
      quote: "Month-end close went from 5 days to 18 hours. The AP agent catches GST mismatches we used to find weeks later.",
      name: "Rajesh Mehta", role: "CFO, Larsen Manufacturing",
      result: "72% faster close cycle",
    },
    metaTitle: "AI Invoice Processing for India — GSTIN, 3-Way Match, AP Automation | AgenticOrg",
    metaDescription: "Process invoices in 11 seconds with AI. GSTIN validation, 3-way matching, GL posting. Month-end close: 5 days → 1. Start free.",
    slug: "ai-invoice-processing",
  },
  "automated-bank-reconciliation": {
    keyword: "Automated Bank Reconciliation",
    headline: "99.7% Auto-Match Rate on Daily Bank Reconciliation",
    subheadline: "AI reconciles 847 transactions in 3 seconds. Your team arrives to a clean recon report every morning.",
    painPoint: "3 FTEs spending their entire day matching bank statements to GL entries. 99% of matches are straightforward — only 1% needs human judgment.",
    metric: "99.7%",
    metricLabel: "Auto-match accuracy — 847 daily transactions",
    features: [
      "Fetches bank transactions and GL entries automatically via API",
      "Multi-round matching: exact match (96%) → fuzzy match (3.5%)",
      "Break analysis: bank charges, timing differences, partial payments",
      "Escalates breaks above ₹50K to CFO with full context",
      "Reconciliation complete by 6 AM daily — before your team arrives",
      "WORM-compliant audit trail on every match decision",
    ],
    ctaText: "See Reconciliation in Action",
    testimonial: {
      quote: "Bank reconciliation runs at 4 AM every day. We went from 3 FTEs reconciling to zero manual work.",
      name: "Suresh Iyer", role: "VP Finance, GreenEnergy Corp",
      result: "₹69,800/month saved in early-pay discounts",
    },
    metaTitle: "Automated Bank Reconciliation — 99.7% Accuracy, Zero Manual Work | AgenticOrg",
    metaDescription: "AI reconciles 847 bank transactions in 3 seconds with 99.7% accuracy. Breaks auto-escalated. Recon done before your team arrives.",
    slug: "automated-bank-reconciliation",
  },
  "payroll-automation": {
    keyword: "Payroll Automation",
    headline: "Zero Payroll Errors Across 847 Employees — Every Single Month",
    subheadline: "AI computes PF, ESI, TDS, and generates payslips automatically. Integrated with Darwinbox, Tally, and EPFO.",
    painPoint: "One PF/ESI mistake = compliance notice. Manual payroll processing = anxiety every month. Your HR team deserves better.",
    metric: "0",
    metricLabel: "Payroll errors in 6 months — across 847 employees",
    features: [
      "Gross pay computed from attendance data (Darwinbox integration)",
      "PF, ESI, TDS deductions calculated per latest government rules",
      "Automatic validation against attendance and leave records",
      "Payslips generated and emailed to every employee",
      "EPFO challan data prepared for upload",
      "HITL approval before final payroll run — HR Head reviews summary",
    ],
    ctaText: "See Payroll Automation in Action",
    testimonial: {
      quote: "Onboarding that took our HR team 2 weeks now happens in a day. PF and ESI calculations are 100% accurate.",
      name: "Ananya Sharma", role: "CHRO, Nexgen Fintech",
      result: "Zero payroll errors in 6 months",
    },
    metaTitle: "Payroll Automation India — PF, ESI, TDS with Zero Errors | AgenticOrg",
    metaDescription: "AI payroll processing with zero errors. PF, ESI, TDS computed automatically. Darwinbox + EPFO integration. 847 employees, zero mistakes.",
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
      await fetch("/api/v1/demo-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, source: `ads_${slug}` }),
      });
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
        <h3 className="text-xl font-bold text-slate-900 mb-2">You're in!</h3>
        <p className="text-slate-600">We'll reach out within 2 minutes with a personalized demo link.</p>
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
      <p className="text-xs text-slate-400 text-center">No credit card required. Free to start.</p>
    </form>
  );
}

export default function AdsLanding() {
  const slug = window.location.pathname.split("/").pop() || "ai-invoice-processing";
  const page = PAGES[slug] || PAGES["ai-invoice-processing"];

  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>{page.metaTitle}</title>
        <meta name="description" content={page.metaDescription} />
        <meta name="robots" content="noindex" />
        <link rel="canonical" href={`https://agenticorg.ai/solutions/${page.slug}`} />
      </Helmet>

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
            <h2 className="text-lg font-bold text-slate-900 mb-1">See it in action</h2>
            <p className="text-sm text-slate-500 mb-4">Get a personalized demo in under 2 minutes.</p>
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

      {/* Testimonial */}
      <section className="py-16">
        <div className="max-w-3xl mx-auto px-4 text-center">
          <div className="flex justify-center gap-1 mb-4">
            {[1,2,3,4,5].map((i) => (
              <svg key={i} className="w-5 h-5 text-amber-400" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
              </svg>
            ))}
          </div>
          <blockquote className="text-xl text-slate-800 font-medium italic leading-relaxed">
            "{page.testimonial.quote}"
          </blockquote>
          <div className="mt-4">
            <p className="font-semibold text-slate-900">{page.testimonial.name}</p>
            <p className="text-sm text-slate-500">{page.testimonial.role}</p>
          </div>
          <div className="mt-3 inline-block bg-emerald-50 text-emerald-700 text-sm font-semibold px-4 py-1.5 rounded-full">
            Result: {page.testimonial.result}
          </div>
        </div>
      </section>

      {/* Bottom CTA */}
      <section className="py-16 bg-slate-900">
        <div className="max-w-xl mx-auto px-4 text-center">
          <h2 className="text-2xl font-bold text-white mb-4">Ready to see it work?</h2>
          <p className="text-slate-400 mb-6">Free to start. No credit card. Deploy in 5 minutes.</p>
          <div className="flex justify-center gap-4">
            <Link to="/signup" className="bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-8 py-3 rounded-xl text-sm font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg">Start Free</Link>
            <Link to="/playground" className="border border-slate-600 text-slate-300 px-8 py-3 rounded-xl text-sm font-semibold hover:bg-slate-800 transition-all">Try Playground</Link>
          </div>
        </div>
      </section>
    </div>
  );
}
