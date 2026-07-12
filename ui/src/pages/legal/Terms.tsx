import { Link } from "react-router-dom";

export default function Terms() {
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
        <h1 className="text-4xl font-bold text-slate-900 mb-2">Terms of Service</h1>
        <p className="text-sm text-slate-500 mb-10">Last updated: 11 July 2026</p>

        <p className="text-slate-700 leading-relaxed mb-6">
          These Terms of Service (&ldquo;<strong>Terms</strong>&rdquo;) govern your access to and use of the
          AgenticOrg platform (the &ldquo;<strong>Service</strong>&rdquo;), operated by Mu-Zero Technologies
          Private Limited (&ldquo;<strong>AgenticOrg</strong>&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;). By creating an
          account or using the Service you agree to these Terms.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">1. The Service</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          AgenticOrg provides software and hosted-service features for configuring and
          running scoped AI-agent and workflow operations. Available models, connectors,
          tools, write actions, support, and limits depend on the plan, deployment,
          credentials, provider access, and workspace policy. A listed capability is not
          a guarantee that it is enabled or suitable for your use.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">2. Account &amp; eligibility</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>You must be at least 18 years old and authorized to bind your organization.</li>
          <li>You are responsible for keeping account credentials confidential and for all activity under your account.</li>
          <li>Notify us immediately at <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">sanjeev@agenticorg.ai</a> on any suspected unauthorized access.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">3. Subscriptions &amp; billing</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>Plan, billing period, currency, price, taxes, and renewal terms are those shown at checkout or in the applicable order.</li>
          <li>Payments may be processed by the provider offered for your currency and region.</li>
          <li>Price changes and renewal notices will be handled as required by the applicable order, contract, and law.</li>
          <li>You are responsible for accurate billing details and taxes not collected by us where applicable.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">4. Cancellation &amp; refund policy</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          You may submit a cancellation through available billing controls or the
          contact address below. Confirmation, renewal stop date, access period, credits,
          and refund eligibility are governed by the applicable order and our
          <Link to="/refund" className="text-blue-600 hover:underline"> Cancellation &amp; Refund Policy</Link>.
          Nothing in these Terms limits a non-waivable statutory right.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">5. Acceptable use</h2>
        <p className="text-slate-700 leading-relaxed mb-3">You agree not to:</p>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>Use the Service to violate any law or third-party right.</li>
          <li>Attempt to reverse-engineer non-public portions of the hosted Service except where an open-source license or applicable law permits it.</li>
          <li>Use the Service to send spam, phishing, malware, or abusive content.</li>
          <li>Probe, scan, or attempt to compromise the security of the Service.</li>
          <li>Resell or sublicense the Service without our written permission.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">6. Your content &amp; data</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          As between you and AgenticOrg, you retain your rights in content you submit.
          You grant us the limited rights needed to host, process, secure, transmit, and
          support that content for the Service and as otherwise instructed or permitted
          by the Privacy Policy. Configured third-party model and connector providers
          process data under their own terms and the workspace settings you select.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">7. Intellectual property</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          Open-source portions of AgenticOrg are licensed under their applicable
          repository licenses, including Apache-2.0 where stated. The hosted Service,
          trademarks, branding, and non-public materials remain owned by AgenticOrg or
          its licensors. These Terms do not override open-source licenses or transfer
          either party's pre-existing intellectual-property rights.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">8. Service availability</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          The Service may be unavailable because of maintenance, incidents, provider
          failures, force majeure, or customer configuration. No uptime target, service
          credit, response time, or remedy applies unless it appears in an executed SLA
          or order. The public status page is informational and does not amend a contract.
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
          access to the Service ends, and data is handled under the Privacy Policy, applicable order, legal obligations, and technical backup cycles.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">12. Governing law &amp; jurisdiction</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          These Terms are governed by the laws of India. Disputes shall be subject to the
          exclusive jurisdiction of the courts of Gautam Buddha Nagar (Noida), Uttar Pradesh.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">13. Changes</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          We may update these Terms by posting a revised version and effective date.
          We will provide additional notice when required by the applicable contract or law.
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
