import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";

export default function Refund() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Cancellation and Refund Notice - AgenticOrg</title>
        <meta name="description" content="Draft AgenticOrg cancellation and refund notice requiring legal review." />
      </Helmet>

      <header className="border-b border-slate-200">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link to="/" className="text-xl font-semibold text-slate-900">AgenticOrg</Link>
          <Link to="/" className="text-sm text-slate-600 hover:text-slate-900">Back to home</Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-12">
        <h1 className="mb-3 text-4xl font-bold text-slate-900">Cancellation and refund notice</h1>
        <p className="mb-10 rounded-lg border border-amber-300 bg-amber-50 p-4 font-medium text-amber-950">
          Draft policy notice - legal review required before publication or reliance.
        </p>

        <p className="mb-6 leading-relaxed text-slate-700">
          This repository page does not grant or deny a refund, credit, cancellation right, or
          service remedy. Eligibility, calculations, effective dates, access after cancellation,
          payment method, processing timing, taxes, disputes, and exceptions are governed by the
          signed order form, the legally reviewed policy presented for the purchase, and
          applicable law.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Submit a request</h2>
        <p className="mb-6 leading-relaxed text-slate-700">
          An authorized workspace administrator may email{" "}
          <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">
            sanjeev@agenticorg.ai
          </a>{" "}
          with the workspace and order identifiers and the requested action. Do not include full
          payment credentials. The team will identify the controlling terms before confirming any
          action or remedy.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Related terms</h2>
        <p className="leading-relaxed text-slate-700">
          Review this notice together with the applicable order form and the current{" "}
          <Link to="/terms" className="text-blue-600 hover:underline">terms notice</Link>.
          Repository examples, plan labels, and UI text are not authoritative commercial terms.
        </p>
      </main>
    </div>
  );
}
