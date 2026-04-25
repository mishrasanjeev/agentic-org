import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";

export default function Refund() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Cancellation &amp; Refund Policy — AgenticOrg</title>
        <meta
          name="description"
          content="AgenticOrg cancellation and refund policy under Indian consumer law."
        />
      </Helmet>

      <header className="border-b border-slate-200">
        <div className="max-w-3xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link to="/" className="text-xl font-semibold text-slate-900">
            AgenticOrg
          </Link>
          <Link to="/" className="text-sm text-slate-600 hover:text-slate-900">
            ← Back to home
          </Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-12">
        <h1 className="text-4xl font-bold text-slate-900 mb-2">Cancellation &amp; Refund Policy</h1>
        <p className="text-sm text-slate-500 mb-10">Last updated: 25 April 2026</p>

        <p className="text-slate-700 leading-relaxed mb-8">
          This policy describes how cancellations and refunds work for paid subscriptions to
          the AgenticOrg platform, operated by Mu-Zero Technologies Private Limited
          (&ldquo;AgenticOrg&rdquo;). It applies to all paying customers in India and
          internationally, and abides by the Consumer Protection Act 2019 (India) and the
          Consumer Protection (E-Commerce) Rules 2020.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">1. How to cancel</h2>
        <p className="text-slate-700 leading-relaxed mb-3">You can cancel your subscription at any time, using either method:</p>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>From your account dashboard: <strong>Settings → Billing → Cancel subscription</strong>.</li>
          <li>By email to <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">sanjeev@agenticorg.ai</a> from the account&rsquo;s registered admin email, including the workspace name and subscription ID.</li>
        </ul>
        <p className="text-slate-700 leading-relaxed mb-6">
          Cancellation takes effect immediately. Your access continues until the end of the
          paid billing cycle unless you request immediate termination.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">2. Refund rules</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-3 mb-6">
          <li>
            <strong>No refunds for past usage.</strong> Amounts charged for usage already
            consumed in the current billing cycle are not refundable.
          </li>
          <li>
            <strong>Pro-rated refund of unused balance on cancellation.</strong> When you
            cancel mid-cycle, the unused portion of that cycle is refunded:
            <br />
            <em className="text-slate-600">
              Refund = (cycle subscription fee) × (days remaining in cycle ÷ total days in cycle)
            </em>
            <br />
            We process the refund within <strong>7–10 business days</strong> to the original
            payment method (Stripe/Card, UPI, NetBanking, EMI, Wallet).
          </li>
          <li>
            <strong>No refund on add-ons or one-time charges.</strong> Implementation services,
            custom connector development, training, and other one-time fees are non-refundable
            once the work has been delivered.
          </li>
          <li>
            <strong>Service-failure credits.</strong> If our Service is unavailable for a
            continuous period exceeding 24 hours due to our fault (excluding scheduled
            maintenance, force majeure, and third-party outages), you may request a service
            credit equal to the downtime by emailing support.
          </li>
          <li>
            <strong>Erroneous charges.</strong> If you were charged in error (duplicate
            charge, wrong plan, billing-system bug), email support within 30 days of the
            charge. We refund verified errors in full within 7 business days.
          </li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">3. How refunds are processed</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>
            <strong>Cards (Visa, Mastercard, AmEx, RuPay)</strong>: refund posted within 7–10
            business days. Bank settlement may take an additional 3–5 days depending on issuer.
          </li>
          <li>
            <strong>UPI / Net Banking</strong>: refund credited to source account within 7
            business days.
          </li>
          <li>
            <strong>Wallets / EMI</strong>: refund returned to the original wallet/EMI source
            per provider rules.
          </li>
        </ul>
        <p className="text-slate-700 leading-relaxed mb-6">
          You will receive an email confirmation when the refund is initiated, with the
          provider&rsquo;s refund reference ID.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">4. Disputes</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          If you disagree with a refund decision, email{" "}
          <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">
            sanjeev@agenticorg.ai
          </a>{" "}
          with subject &ldquo;Refund Dispute&rdquo;. We will review and respond within 7 business days.
          Unresolved disputes are subject to the governing law and jurisdiction in our{" "}
          <Link to="/terms" className="text-blue-600 hover:underline">Terms of Service</Link>{" "}
          (Gautam Buddha Nagar, Uttar Pradesh, India).
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">5. Contact</h2>
        <p className="text-slate-700 leading-relaxed">
          Mu-Zero Technologies Private Limited
          <br />
          F-1004, Grand Ajnara Heritage, Sector-74, Noida — 201301, Uttar Pradesh, India
          <br />
          Email: <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">sanjeev@agenticorg.ai</a>
          <br />
          Phone: <a className="text-blue-600 hover:underline" href="tel:+917703919243">+91 77039 19243</a>
        </p>
      </main>
    </div>
  );
}
