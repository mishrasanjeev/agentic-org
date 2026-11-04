// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { Helmet } from "react-helmet-async";
import { Link } from "react-router";
import ProductOwnership, { PRIMARY_CONTACT_EMAIL } from "../../components/ProductOwnership";

// The version a shop accepts on the Sell everywhere page (AGENTICORG_NETWORK_TERMS_VERSION, core/config.py).
export const NETWORK_TERMS_VERSION = "2026-10-25";

const link = "text-blue-700 underline underline-offset-2 hover:text-blue-900";

export default function NetworkTerms() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Shopping Network Terms - AgenticOrg</title>
        <meta
          name="description"
          content="Draft terms for buyers and shops on the AgenticOrg shopping network: shops are the sellers and take payment themselves; AgenticOrg lists shops and opens checkouts."
        />
      </Helmet>

      <header className="border-b border-slate-200">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link to="/" className="text-xl font-semibold text-slate-900">AgenticOrg</Link>
          <Link to="/" className="text-sm text-slate-600 hover:text-slate-900">Back to home</Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-12">
        <h1 className="mb-3 text-4xl font-bold text-slate-900">Shopping network terms</h1>
        <p className="mb-10 rounded-lg border border-amber-300 bg-amber-50 p-4 font-medium text-amber-950">
          Draft terms - legal review required before publication or reliance.
        </p>

        <p className="mb-6 leading-relaxed text-slate-700">
          The AgenticOrg shopping network lists independent shops so that buyers can find their products from
          AI apps (through the AgenticOrg MCP server) and from the AgenticOrg shopping bot on Telegram, and
          open a checkout at a shop. Version {NETWORK_TERMS_VERSION}.
        </p>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">For buyers</h2>
        <ul className="mb-6 list-disc space-y-2 pl-6 text-slate-700">
          <li>Each shop is the seller of its products. Your purchase contract is with the shop.</li>
          <li>
            AgenticOrg shows products, prices and stock as each shop publishes them, opens a checkout at the shop
            you choose, and gives you the link to the shop's own payment page. You pay the shop there.
            AgenticOrg never takes payment and never receives card details.
          </li>
          <li>
            Delivery, returns, refunds, warranties and support are the shop's. Each shop's page shows its
            shipping and returns summary; ask the shop before you buy when something is unclear.
          </li>
          <li>
            Search results are asked of the shops in a random order. AgenticOrg does not rank shops or take
            payment to show a shop first.
          </li>
          <li>
            What the MCP server and the Telegram bot handle is described in the{" "}
            <Link className={link} to="/privacy#shopping-network-buyers">privacy notice</Link>.
          </li>
        </ul>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">For shops</h2>
        <p className="mb-4 leading-relaxed text-slate-700">
          A shop accepts these terms, version {NETWORK_TERMS_VERSION}, when it turns on Sell everywhere:
        </p>
        <blockquote className="mb-6 border-l-4 border-slate-300 pl-4 text-slate-700">
          AgenticOrg lists my shop and opens checkouts for buyers who find it. I remain the seller and take
          payment myself.
        </blockquote>
        <ul className="mb-6 list-disc space-y-2 pl-6 text-slate-700">
          <li>A shop can pause its listing at any time on the Sell everywhere page.</li>
          <li>
            AgenticOrg lists a shop only while its shop profile is published and its catalog is verified, and
            may register the network, with each listed shop, with outside programs such as app directories and
            merchant centers.
          </li>
          <li>
            Using the AgenticOrg platform is governed by the <Link className={link} to="/terms">terms notice</Link>{" "}
            and the shop's signed agreement.
          </li>
        </ul>

        <h2 className="mb-3 mt-8 text-2xl font-semibold text-slate-900">Contact</h2>
        <p className="leading-relaxed text-slate-700">
          Questions about these terms:{" "}
          <a className={link} href={`mailto:${PRIMARY_CONTACT_EMAIL}`}>{PRIMARY_CONTACT_EMAIL}</a>. Questions about
          an order go to the shop.
        </p>
        <ProductOwnership tone="light" className="mt-10 border-t border-slate-200 pt-6" />
      </main>
    </div>
  );
}
