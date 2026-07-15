import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";

export default function Support() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Support Notice - AgenticOrg</title>
        <meta name="description" content="AgenticOrg support contact and agreement boundaries." />
      </Helmet>

      <header className="border-b border-slate-200">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link to="/" className="text-xl font-semibold text-slate-900">AgenticOrg</Link>
          <Link to="/" className="text-sm text-slate-600 hover:text-slate-900">Back to home</Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-12">
        <h1 className="mb-3 text-4xl font-bold text-slate-900">Support contact</h1>
        <p className="mb-10 rounded-lg border border-amber-300 bg-amber-50 p-4 font-medium text-amber-950">
          Draft support notice - legal and support-operations review required.
        </p>

        <section className="mb-10">
          <h2 className="mb-3 text-2xl font-semibold text-slate-900">Request support</h2>
          <p className="leading-relaxed text-slate-700">
            Email{" "}
            <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">
              sanjeev@agenticorg.ai
            </a>{" "}
            with the workspace identifier, a concise description, observed timestamps, and safe
            reproduction details. Do not send passwords, private keys, access tokens, or unrelated
            personal data.
          </p>
        </section>

        <section className="mb-10">
          <h2 className="mb-3 text-2xl font-semibold text-slate-900">Coverage and response</h2>
          <p className="leading-relaxed text-slate-700">
            Support channels, operating windows, severity definitions, response objectives,
            escalation paths, resolution objectives, and service credits are governed by the
            applicable signed order form or support schedule. This page establishes no fixed
            response or resolution commitment.
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-2xl font-semibold text-slate-900">Security and privacy</h2>
          <p className="leading-relaxed text-slate-700">
            Suspected vulnerabilities should follow the private reporting process in the current
            security policy. Data-handling questions should reference the{" "}
            <Link to="/privacy" className="text-blue-600 hover:underline">privacy notice</Link>{" "}
            and the signed data-processing terms.
          </p>
        </section>
      </main>
    </div>
  );
}
