import { Link } from "react-router-dom";

export default function Refund() {
  return (
    <div className="min-h-screen bg-white">

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
        <p className="text-sm text-slate-500 mb-10">Last updated: 11 July 2026</p>

        <p className="text-slate-700 leading-relaxed mb-8">
          This policy describes cancellation and refund requests for paid AgenticOrg
          subscriptions. The applicable checkout terms, executed order, mandatory
          consumer rights, and payment-provider rules also apply. This policy does not
          limit rights that cannot lawfully be waived.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">1. How to cancel</h2>
        <p className="text-slate-700 leading-relaxed mb-3">
          Submit a cancellation through the billing control shown in your account, when
          available, or email the contact below from an authorized admin address with the
          workspace and subscription identifiers.
        </p>
        <p className="text-slate-700 leading-relaxed mb-6">
          Cancellation is effective when confirmed. The confirmation will state whether
          renewal stops immediately, when access ends, and whether an immediate termination
          was requested. Do not assume a submitted request changed provider billing until
          you receive confirmation.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">2. Refund rules</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-3 mb-6">
          <li><strong>Past usage.</strong> Charges attributable to service already consumed are generally not refundable, except where the applicable order or law requires otherwise.</li>
          <li><strong>Unused prepaid time.</strong> A pro-rated refund applies only when stated in the checkout terms or order, approved by support, or required by law. Any estimate is calculated from the eligible prepaid amount and unused period.</li>
          <li><strong>One-time work.</strong> Delivered implementation, training, or custom-development work is generally non-refundable, subject to the order and non-waivable rights.</li>
          <li><strong>Service credits.</strong> Availability credits and remedies apply only under an executed SLA or order and its exclusions and claim procedure.</li>
          <li><strong>Billing errors.</strong> Contact us promptly about a suspected duplicate, wrong-plan, or other erroneous charge. Verified errors will be corrected or refunded as required by the provider, order, and applicable law.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">3. How refunds are processed</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          Approved refunds are normally initiated to the original payment method where
          supported. Banks, card issuers, UPI services, wallets, and other processors
          control settlement time after initiation, so displayed estimates are not
          guaranteed. We will provide a processor reference when one is available.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">4. Disputes</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          If you disagree with a refund decision, email{" "}
          <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">
            sanjeev@agenticorg.ai
          </a>{" "}
          with subject &ldquo;Refund Dispute&rdquo;. We will acknowledge and review the request as reasonably practicable; resolution time depends on the evidence, provider, and applicable process.
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
