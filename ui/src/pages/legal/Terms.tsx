import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";

export default function Terms() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Terms of Service — AgenticOrg</title>
        <meta
          name="description"
          content="AgenticOrg Terms of Service: subscriptions, refunds, cancellations, governing law."
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
        <h1 className="text-4xl font-bold text-slate-900 mb-2">Terms of Service</h1>
        <p className="text-sm text-slate-500 mb-10">Last updated: 25 April 2026</p>

        <p className="text-slate-700 leading-relaxed mb-6">
          These Terms of Service (&ldquo;<strong>Terms</strong>&rdquo;) govern your access to and use of the
          AgenticOrg platform (the &ldquo;<strong>Service</strong>&rdquo;), operated by Mu-Zero Technologies
          Private Limited (&ldquo;<strong>AgenticOrg</strong>&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;). By creating an
          account or using the Service you agree to these Terms.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">1. The Service</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          AgenticOrg provides a B2B SaaS platform that lets organizations build, deploy, and
          govern AI agents that perform real work — automating finance, sales, HR, and IT
          operations through pre-built connectors. The Service is provided on a subscription
          basis. Free, Pro, and Enterprise tiers are described at{" "}
          <Link to="/pricing" className="text-blue-600 hover:underline">/pricing</Link>.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">2. Account &amp; eligibility</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>You must be at least 18 years old and authorized to bind your organization.</li>
          <li>You are responsible for keeping account credentials confidential and for all activity under your account.</li>
          <li>Notify us immediately at <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">sanjeev@agenticorg.ai</a> on any suspected unauthorized access.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">3. Subscriptions &amp; billing</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>Paid plans are billed in advance for each billing cycle (monthly or annual).</li>
          <li>USD payments are processed by Stripe; INR payments by Pine Labs Plural.</li>
          <li>Prices may change on 30 days&rsquo; written notice; the change takes effect on your next renewal.</li>
          <li>Indian customers are charged inclusive of GST as applicable; tax invoices are emailed at the start of each cycle.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">4. Cancellation &amp; refund policy</h2>
        <p className="text-slate-700 leading-relaxed mb-3">
          You can cancel your subscription at any time from your account dashboard or by emailing
          <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai"> sanjeev@agenticorg.ai</a>.
        </p>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>
            <strong>No refunds for past usage.</strong> Charges already incurred for the
            current billing cycle&rsquo;s usage are non-refundable.
          </li>
          <li>
            <strong>Pro-rated refund of unused balance.</strong> When you cancel mid-cycle,
            we calculate the unused portion of the cycle (days remaining ÷ days in cycle) and
            refund the corresponding amount within 7–10 business days to the original payment
            method.
          </li>
          <li>
            <strong>No refund on add-ons or one-time purchases.</strong> One-time charges
            (e.g., implementation services, custom connectors) are non-refundable once delivered.
          </li>
          <li>
            <strong>Service failure refunds.</strong> If we fail to deliver agreed service for
            a continuous period exceeding 24 hours due to our fault, you may request a credit
            equal to that downtime by emailing support.
          </li>
        </ul>
        <p className="text-slate-700 leading-relaxed mb-6">
          See our standalone <Link to="/refund" className="text-blue-600 hover:underline">
          Cancellation &amp; Refund Policy</Link> for the full text.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">5. Acceptable use</h2>
        <p className="text-slate-700 leading-relaxed mb-3">You agree not to:</p>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>Use the Service to violate any law or third-party right.</li>
          <li>Attempt to reverse-engineer, decompile, or extract source code from the Service.</li>
          <li>Use the Service to send spam, phishing, malware, or abusive content.</li>
          <li>Probe, scan, or attempt to compromise the security of the Service.</li>
          <li>Resell or sublicense the Service without our written permission.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">6. Your content &amp; data</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          You retain ownership of all data you submit to the Service. You grant us a limited
          license to process, store, and transmit that data only to provide and improve the
          Service. We do not allow your data to be used for third-party model training. See
          our <Link to="/privacy" className="text-blue-600 hover:underline">Privacy Policy</Link>.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">7. Intellectual property</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          The Service, including all software, designs, agent definitions, prompts, and
          documentation, remains the exclusive property of AgenticOrg. Nothing in these Terms
          transfers any IP rights to you except the limited license to use the Service.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">8. Service availability</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          We aim for 99.5% monthly uptime on Pro and Enterprise tiers (excluding scheduled
          maintenance, force majeure, and third-party LLM/connector outages). Free tier is
          provided as-is with no uptime commitment.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">9. Warranty disclaimer</h2>
        <p className="text-slate-700 leading-relaxed mb-6 uppercase text-sm">
          The Service is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo; without warranty of
          any kind, express or implied, including merchantability, fitness for a particular
          purpose, and non-infringement. AI-generated outputs may contain errors; you remain
          responsible for reviewing and validating outputs before acting on them.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">10. Limitation of liability</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          To the maximum extent permitted by law, AgenticOrg&rsquo;s aggregate liability for any
          claim arising out of or relating to the Service shall not exceed the amount you paid
          us in the 12 months preceding the claim. We are not liable for indirect, incidental,
          special, consequential, or punitive damages.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">11. Termination</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          We may suspend or terminate your account for material breach of these Terms, illegal
          use, or non-payment, after reasonable notice where feasible. On termination, your
          access to the Service ends and we will delete your data per our retention schedule.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">12. Governing law &amp; jurisdiction</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          These Terms are governed by the laws of India. Disputes shall be subject to the
          exclusive jurisdiction of the courts of Gautam Buddha Nagar (Noida), Uttar Pradesh.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">13. Changes</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          We may update these Terms from time to time. Material changes will be posted here and
          notified to account admins by email at least 14 days before they take effect.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">14. Contact</h2>
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
