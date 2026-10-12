import { Helmet } from "react-helmet-async";
import { Link } from "react-router";
import ProductOwnership, { PRIMARY_CONTACT_EMAIL } from "../../components/ProductOwnership";

export default function Terms() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Terms Notice - AgenticOrg</title>
        <meta
          name="description"
          content="AgenticOrg terms notice and approved prospective licensing policy; signed agreements and release-specific software licenses control."
        />
      </Helmet>

      <header className="border-b border-slate-200">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link to="/" className="text-xl font-semibold text-slate-900">AgenticOrg</Link>
          <Link to="/" className="text-sm text-slate-600 hover:text-slate-900">Back to home</Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-12">
        <h1 className="mb-3 text-4xl font-bold text-slate-900">Terms notice</h1>
        <p className="mb-10 rounded-lg border border-amber-300 bg-amber-50 p-4 font-medium text-amber-950">
          Informational notice - signed agreements and release-specific software licenses control.
        </p>

        <p className="mb-6 leading-relaxed text-slate-700">
          This repository copy is not a substitute for an executed customer agreement and does
          not create commercial, service-level, privacy, or refund commitments. The documents
          signed by the customer and AgenticOrg control where they conflict with this page.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Controlling documents</h2>
        <p className="mb-6 leading-relaxed text-slate-700">
          Applicable terms may include a signed order form, master agreement, support schedule,
          data-processing agreement, and the legally reviewed policy posted for the relevant
          purchase. Plan names or product copy in the repository are not authoritative billing
          or entitlement records.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Service use</h2>
        <ul className="mb-6 list-disc space-y-2 pl-6 text-slate-700">
          <li>Authorized users are responsible for protecting their credentials and access.</li>
          <li>AI-assisted output requires appropriate human review before it is relied on.</li>
          <li>Use must follow applicable law, granted permissions, and third-party terms.</li>
          <li>Suspected unauthorized access should be reported through the support contact.</li>
        </ul>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Prospective licensing policy</h2>
        <p className="mb-6 leading-relaxed text-slate-700">
          Publication approved; software adoption pending: personal/internal use would be free; commercializing
          the platform would require an agreement with Orchestrum Technologies LLP.
          Agreements: <a className="text-blue-600 hover:underline" href={`mailto:${PRIMARY_CONTACT_EMAIL}`}>{PRIMARY_CONTACT_EMAIL}</a>.
          {" "}Read the <Link to="/docs/licensing" className="text-blue-600 hover:underline">licensing policy</Link> for
          definitions, recorded approval and the release-scope boundary. Current Apache-2.0 rights remain unchanged;
          hosted plans, infrastructure and provider costs are separate.
        </p>
        <p className="mb-6 leading-relaxed text-slate-700">
          The concrete transition uses a separate source-available license for
          explicitly covered, eligible future platform material. Legacy code and
          published SDK/protocol grants keep their licenses. Hosted resale, OEM
          access and brand permissions need their own applicable agreement; that
          does not restrict independent use of Apache-licensed code.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Terms requiring agreement</h2>
        <p className="mb-6 leading-relaxed text-slate-700">
          Availability, support coverage, service credits, prices, taxes, billing cadence,
          renewal, cancellation, refunds, data retention, data location, warranties, liability,
          suspension, termination, governing law, and notice procedures are only as stated in
          the applicable controlling documents. This page sets no fixed target, timeline,
          location, or remedy for those subjects.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Related notices</h2>
        <p className="mb-6 leading-relaxed text-slate-700">
          Review the current <Link to="/privacy" className="text-blue-600 hover:underline">privacy notice</Link>,{" "}
          <Link to="/support" className="text-blue-600 hover:underline">support notice</Link>, and{" "}
          <Link to="/refund" className="text-blue-600 hover:underline">refund notice</Link> with the
          signed agreement. Each repository copy requires legal review.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Contact</h2>
        <p className="leading-relaxed text-slate-700">
          Questions about the applicable agreement may be sent to{" "}
          <a className="text-blue-600 hover:underline" href={`mailto:${PRIMARY_CONTACT_EMAIL}`}>
            {PRIMARY_CONTACT_EMAIL}
          </a>.
        </p>
        <ProductOwnership tone="light" className="mt-10 border-t border-slate-200 pt-6" />
      </main>
    </div>
  );
}
