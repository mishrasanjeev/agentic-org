import { Link } from "react-router-dom";

export default function Privacy() {
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

      <main className="max-w-3xl mx-auto px-6 py-12 prose prose-slate">
        <h1 className="text-4xl font-bold text-slate-900 mb-2">Privacy Policy</h1>
        <p className="text-sm text-slate-500 mb-10">Last updated: 11 July 2026</p>

        <p className="text-slate-700 leading-relaxed mb-6">
          This Privacy Policy describes how Mu-Zero Technologies Private Limited
          (&ldquo;<strong>AgenticOrg</strong>&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;) collects, uses, discloses and
          secures information when you use the AgenticOrg platform (the
          &ldquo;<strong>Service</strong>&rdquo;) accessed at <code>agenticorg.ai</code>.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">1. Information we collect</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>
            <strong>Account data</strong> — name, work email, organization, role.
          </li>
          <li>
            <strong>Usage data</strong> — agent runs, workflow executions, connector invocations,
            request/response metadata, IP address, browser/OS for security and product analytics.
          </li>
          <li>
            <strong>Connector content</strong> - when an administrator authorizes a
            third-party system, the Service processes the selected content and metadata
            needed for the configured task. Inputs, outputs, and audit references may be
            retained according to workspace settings and the retention section below.
            We do not intentionally use customer connector content to train our own
            general-purpose models. Any configured model provider processes data under
            the applicable account settings and provider terms.
          </li>
          <li>
            <strong>Payment and billing data</strong> - where checkout is available,
            the configured processor handles payment-instrument details. We may retain
            customer, subscription, invoice, amount, currency, status, and processor
            reference data needed for billing and support. The Service is not intended
            to store full card numbers or CVVs.
          </li>
          <li>
            <strong>Support communications</strong> — when you email or call us.
          </li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">2. How we use your information</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>To operate the Service: provision agents, execute workflows, return results.</li>
          <li>To bill you: invoice generation, subscription management, payment reconciliation.</li>
          <li>To secure the Service: detect abuse, throttle attacks, audit administrative actions.</li>
          <li>To improve the Service: aggregated, de-identified usage patterns.</li>
          <li>To respond to support requests and legal/regulatory obligations.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">3. Who we share data with</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>
            <strong>Cloud infrastructure</strong> - the current managed deployment uses
            Google Cloud services for compute, database, storage, and secret management.
            Regions and subprocessors can vary by deployment, customer agreement, and
            self-hosted configuration. Providers receive data needed to deliver those services.
          </li>
          <li>
            <strong>Payment processors</strong> — Stripe Inc. (USD) and Pine Labs Pvt Ltd
            (INR/Plural) receive only the fields required to process the transaction.
          </li>
          <li>
            <strong>Configured model providers</strong> - depending on workspace settings,
            content may be sent to providers such as Google, Anthropic, OpenAI, or a
            customer-operated model endpoint. Provider processing, retention, and training
            terms depend on the selected service and account configuration; administrators
            should review those terms before enabling a provider.
          </li>
          <li>
            <strong>Connector destinations</strong> — only the third-party tools you explicitly
            authorize, only the data the agent task requires.
          </li>
          <li>
            <strong>Legal authorities</strong> — when required by valid legal process or to
            protect rights, safety, or property.
          </li>
        </ul>
        <p className="text-slate-700 leading-relaxed mb-6">
          We do not sell your personal data. We do not share data with advertising networks.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">4. How disclosure happens</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          The managed deployment is designed to use encrypted transport and provider
          encryption-at-rest controls, role-restricted production access, audit events,
          and secret-management services. Exact controls can differ for self-hosted or
          customer-configured deployments. No security measure eliminates all risk, and
          this description is not a certification or independent assurance report.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">5. Security practices</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-3">
          <li>Authentication, tenant, role, and scope checks for protected operations.</li>
          <li>Managed secret references for configured cloud and connector credentials.</li>
          <li>Transport and storage protections supplied by the deployed infrastructure.</li>
          <li>Configurable PII masking, rate limiting, audit events, and security headers.</li>
          <li>Dependency, static-analysis, container, and regression security checks.</li>
        </ul>
        <p className="text-sm text-slate-600 mb-6">
          Availability and effectiveness depend on deployment configuration, key custody,
          provider settings, monitoring, and customer use. These practices do not assert
          compliance with or certification to a particular standard.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">6. Data retention</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          Retention depends on record type, workspace configuration, plan or contract,
          security needs, backup cycles, and applicable law. Some billing, fraud-prevention,
          audit, or legal records may be kept after account closure where permitted or
          required. Deletion requests are subject to identity verification, legal holds,
          technical backup cycles, and non-waivable obligations. Contact us for the
          schedule applicable to your deployment.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">7. Your rights</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          Subject to applicable law and verification, you may have rights to access,
          correct, export, object to, restrict, or delete personal data. Rights and
          response periods vary by jurisdiction and may be limited by legal exceptions.
          Submit a request using the contact details below.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">8. Children</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          The Service is intended for organizational use. We do not knowingly collect data from
          individuals under 18.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">9. Changes to this policy</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          We will post material changes here with a new &ldquo;Last updated&rdquo; date and, where
          feasible, notify account admins by email at least 14 days before the change takes effect.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">10. Contact</h2>
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
