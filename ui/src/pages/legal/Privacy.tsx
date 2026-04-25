import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";

export default function Privacy() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Privacy Policy — AgenticOrg</title>
        <meta
          name="description"
          content="How AgenticOrg collects, uses, discloses and secures your data."
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

      <main className="max-w-3xl mx-auto px-6 py-12 prose prose-slate">
        <h1 className="text-4xl font-bold text-slate-900 mb-2">Privacy Policy</h1>
        <p className="text-sm text-slate-500 mb-10">Last updated: 25 April 2026</p>

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
            <strong>Connector content</strong> — when you connect a third-party tool (Gmail, Slack,
            HubSpot, Tally, etc.) we process the data the agent reads/writes on your behalf.
            This data is held only as long as needed to fulfil the agent task and is not used
            for model training.
          </li>
          <li>
            <strong>Payment data</strong> — handled directly by Stripe (USD) or Pine Labs Plural
            (INR). We never see or store full card numbers, CVVs, or bank credentials.
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
            <strong>Cloud infrastructure (Google Cloud Platform)</strong> — hosts the Service,
            stores your data in Singapore (compute) and Singapore (database). Encrypted at rest
            (AES-256) and in transit (TLS 1.2+).
          </li>
          <li>
            <strong>Payment processors</strong> — Stripe Inc. (USD) and Pine Labs Pvt Ltd
            (INR/Plural) receive only the fields required to process the transaction.
          </li>
          <li>
            <strong>LLM providers</strong> — Google (Gemini) and Anthropic (Claude) when used.
            Content sent to providers follows their respective data-handling commitments;
            we do not allow your data to be used for their model training.
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
          All sharing is over TLS 1.2+. Database backups, log archives, and document storage
          are encrypted at rest using Google-managed keys. Access to production systems is
          role-based, MFA-enforced, and audit-logged. Secrets (API keys, OAuth tokens, database
          credentials) are stored in Google Secret Manager, never in source code or plaintext logs.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">5. Security practices</h2>
        <ul className="list-disc pl-6 text-slate-700 space-y-2 mb-6">
          <li>TLS 1.2+ for all network traffic; HSTS enforced.</li>
          <li>Database isolated from public network; access via Cloud SQL connector only.</li>
          <li>Application secrets in Google Secret Manager, rotated on schedule.</li>
          <li>Tenant isolation enforced at the database row-level (RLS).</li>
          <li>JWT tokens with short expiry, blacklist on logout, refresh-token rotation.</li>
          <li>PII masking in logs and traces.</li>
          <li>Vulnerability scanning (bandit, pip-audit) in CI; dependencies tracked via Dependabot.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">6. Data retention</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          Account data is retained for the lifetime of your account plus 90 days after closure.
          Audit logs are retained for 400 days. Transient connector content is retained only as
          long as needed to complete the agent task. You can request deletion of your data by
          emailing <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">sanjeev@agenticorg.ai</a>.
        </p>

        <h2 className="text-2xl font-semibold text-slate-900 mt-8 mb-3">7. Your rights</h2>
        <p className="text-slate-700 leading-relaxed mb-6">
          Subject to applicable law (including India&rsquo;s Digital Personal Data Protection
          Act 2023, the EU GDPR where it applies, and California CCPA), you may request access,
          correction, export, or deletion of your personal data. We will respond within 30 days.
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
