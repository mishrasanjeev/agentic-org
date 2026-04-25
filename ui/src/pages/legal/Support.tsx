import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";

export default function Support() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Support &amp; Contact — AgenticOrg</title>
        <meta
          name="description"
          content="Contact AgenticOrg support: registered company address, India phone, email."
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
        <h1 className="text-4xl font-bold text-slate-900 mb-2">Support &amp; Contact</h1>
        <p className="text-slate-600 mb-10">
          We respond to support requests within one business day (Mon–Fri, 10:00–19:00 IST).
        </p>

        <section className="mb-10">
          <h2 className="text-2xl font-semibold text-slate-900 mb-4">Get in touch</h2>
          <dl className="grid sm:grid-cols-3 gap-y-3 text-slate-700">
            <dt className="font-medium text-slate-900">Email</dt>
            <dd className="sm:col-span-2">
              <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">
                sanjeev@agenticorg.ai
              </a>
            </dd>
            <dt className="font-medium text-slate-900">Phone (India)</dt>
            <dd className="sm:col-span-2">
              <a className="text-blue-600 hover:underline" href="tel:+917703919243">
                +91 77039 19243
              </a>
            </dd>
            <dt className="font-medium text-slate-900">Hours</dt>
            <dd className="sm:col-span-2">Mon–Fri, 10:00–19:00 IST (excluding Indian public holidays)</dd>
          </dl>
        </section>

        <section className="mb-10">
          <h2 className="text-2xl font-semibold text-slate-900 mb-4">Registered company address</h2>
          <address className="not-italic text-slate-700 leading-relaxed">
            <strong className="text-slate-900">Mu-Zero Technologies Private Limited</strong>
            <br />
            F-1004, Grand Ajnara Heritage
            <br />
            Sector-74, Noida — 201301
            <br />
            Uttar Pradesh, India
          </address>
        </section>

        <section className="mb-10">
          <h2 className="text-2xl font-semibold text-slate-900 mb-4">Billing &amp; account questions</h2>
          <p className="text-slate-700 leading-relaxed">
            For invoices, subscription changes, and refund/cancellation requests please email{" "}
            <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">
              sanjeev@agenticorg.ai
            </a>{" "}
            with your account email and the order/subscription ID. See our{" "}
            <Link to="/refund" className="text-blue-600 hover:underline">
              Cancellation &amp; Refund Policy
            </Link>{" "}
            for details.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-semibold text-slate-900 mb-4">Security &amp; privacy concerns</h2>
          <p className="text-slate-700 leading-relaxed">
            Report suspected security issues to{" "}
            <a className="text-blue-600 hover:underline" href="mailto:sanjeev@agenticorg.ai">
              sanjeev@agenticorg.ai
            </a>
            . For data-handling questions, see our{" "}
            <Link to="/privacy" className="text-blue-600 hover:underline">
              Privacy Policy
            </Link>
            .
          </p>
        </section>
      </main>
    </div>
  );
}
