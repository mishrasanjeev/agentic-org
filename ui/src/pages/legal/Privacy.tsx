import { Helmet } from "react-helmet-async";
import { Link } from "react-router";
import ProductOwnership, { PRIMARY_CONTACT_EMAIL } from "../../components/ProductOwnership";

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

        <section aria-labelledby="shopping-network-buyers">
          <h2 id="shopping-network-buyers" className="mb-3 mt-8 text-2xl font-semibold text-slate-900">
            Shopping network buyers
          </h2>
          <p className="mb-4 leading-relaxed text-slate-700">
            Buyers can shop the shops on the AgenticOrg shopping network through the AgenticOrg MCP server
            (https://mcp.agenticorg.ai/mcp, used from ChatGPT, Claude and other AI apps) and through the
            AgenticOrg shopping bot on Telegram. Neither needs an account. Each shop is the seller and takes
            payment on its own payment page; AgenticOrg never takes payment and never receives card details.
            The network terms are at <Link className="text-blue-700 underline underline-offset-2 hover:text-blue-900" to="/network-terms">network terms</Link>.
          </p>
          <h3 className="mb-2 mt-6 text-xl font-semibold text-slate-900">What the MCP server handles</h3>
          <ul className="mb-6 list-disc space-y-2 pl-6 text-slate-700">
            <li>
              Each request carries what the AI app sends: search words, the shop chosen and, to buy, the items,
              the delivery address and any contact details the shop's checkout asks for.
            </li>
            <li>
              Searches go to the shops' public catalogs. Checkout details go to the chosen shop's checkout, which
              AgenticOrg runs for that shop and keeps in the shop's AgenticOrg account as the shop's order record.
            </li>
            <li>
              The server keeps no conversation and no buyer profile. It logs each request's method, result
              status and duration, not its content. It counts requests per IP address for one minute in memory
              to limit abuse. The hosting platform's own request logs also record the time, path, status, IP
              address and user agent of each request.
            </li>
            <li>The AI app's own provider handles the conversation under that provider's privacy terms.</li>
          </ul>
          <h3 className="mb-2 mt-6 text-xl font-semibold text-slate-900">What the Telegram shopping bot handles</h3>
          <ul className="mb-6 list-disc space-y-2 pl-6 text-slate-700">
            <li>
              The bot answers private chats only. It stores the chat under a one-way hash (SHA-256) of the
              Telegram chat id, never the id itself.
            </li>
            <li>
              With that hash it keeps the shop the buyer chose and the last six exchanges (a message and its
              reply), so a
              conversation can continue. The stored chat expires seven days after the last message, earlier
              messages are no longer used after 30 minutes without a message, and /stop deletes the stored chat
              at once.
            </li>
            <li>
              To write a reply, the buyer's message, the recent turns and the catalog results are sent to the
              language model service the platform is configured with.
            </li>
            <li>
              Telegram update numbers are kept for 24 hours so that an update Telegram sends twice is answered
              once. Message counts per hashed chat are kept for an hour to limit abuse.
            </li>
            <li>
              Checkout details the buyer gives in the chat go to the chosen shop's checkout, as for the MCP server.
              Telegram handles the chat itself under Telegram's privacy terms.
            </li>
          </ul>
        </section>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Requests</h2>
        <p className="mb-6 leading-relaxed text-slate-700">
          Requests concerning access, correction, export, deletion, objection, or restriction
          are handled under applicable law and the controlling customer agreement. Identity and
          authority may need to be verified before a request is acted on.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Contact</h2>
        <p className="leading-relaxed text-slate-700">
          Send privacy questions without credentials or unnecessary personal data to{" "}
          <a className="text-blue-700 underline underline-offset-2 hover:text-blue-900" href={`mailto:${PRIMARY_CONTACT_EMAIL}`}>
            {PRIMARY_CONTACT_EMAIL}
          </a>.
        </p>
        <ProductOwnership tone="light" className="mt-10 border-t border-slate-200 pt-6" />
      </main>
    </div>
  );
}
