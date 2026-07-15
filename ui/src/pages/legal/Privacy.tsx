import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";

export default function Privacy() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Privacy Notice - AgenticOrg</title>
        <meta name="description" content="Draft AgenticOrg privacy notice requiring legal review." />
      </Helmet>

      <header className="border-b border-slate-200">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link to="/" className="text-xl font-semibold text-slate-900">AgenticOrg</Link>
          <Link to="/" className="text-sm text-slate-600 hover:text-slate-900">Back to home</Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-12">
        <h1 className="mb-3 text-4xl font-bold text-slate-900">Privacy notice</h1>
        <p className="mb-10 rounded-lg border border-amber-300 bg-amber-50 p-4 font-medium text-amber-950">
          Draft policy notice - legal and privacy review required before publication or reliance.
        </p>

        <p className="mb-6 leading-relaxed text-slate-700">
          This page describes repository-level processing categories. Actual processing depends
          on the deployed configuration, enabled providers and connectors, customer instructions,
          and the applicable signed data-processing terms.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Potential data categories</h2>
        <ul className="mb-6 list-disc space-y-2 pl-6 text-slate-700">
          <li>Account and organization details supplied by authorized users.</li>
          <li>Workflow inputs, outputs, metadata, and approval records.</li>
          <li>Connector content selected by the customer and permitted by its grants.</li>
          <li>Operational, security, billing, and support records created by configured services.</li>
        </ul>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Purpose and disclosure</h2>
        <p className="mb-6 leading-relaxed text-slate-700">
          Data may be processed to provide configured workflows, administer accounts, investigate
          support or security events, meet legal obligations, and operate enabled providers. A
          current subprocessor list and provider terms must be verified for the target deployment;
          names appearing in source code are not evidence that a provider is active.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Retention, location, and security</h2>
        <p className="mb-6 leading-relaxed text-slate-700">
          Retention periods, deletion behavior, backup handling, data location, cross-border
          transfers, and security measures depend on verified deployment settings, provider
          records, applicable law, and the signed agreement. This repository notice sets no
          fixed period, region, response target, or certification status.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Requests</h2>
        <p className="mb-6 leading-relaxed text-slate-700">
          Requests concerning access, correction, export, deletion, objection, or restriction
          are handled under applicable law and the controlling customer agreement. Identity and
          authority may need to be verified before a request is acted on.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Contact</h2>
        <p className="leading-relaxed text-slate-700">
          Send privacy questions without credentials or unnecessary personal data to{" "}
          <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">
            sanjeev@agenticorg.ai
          </a>.
        </p>
      </main>
    </div>
  );
}
