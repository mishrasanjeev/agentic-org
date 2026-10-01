// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  CreditCard,
  Database,
  FileCheck2,
  MessageSquare,
  ShieldCheck,
  Store,
} from "lucide-react";
import "./commerce-transaction.css";

const parties = [
  { id: "buyer", label: "Outside buyer", note: "Consumer app", icon: Bot },
  { id: "seller", label: "Seller agent", note: "AgenticOrg", icon: MessageSquare },
  { id: "source", label: "Source", note: "Shopify", icon: Database },
  { id: "authority", label: "Authority", note: "Grantex", icon: ShieldCheck },
  { id: "payment", label: "Payment", note: "Plural/P3P", icon: CreditCard },
  { id: "merchant", label: "Merchant", note: "Order system", icon: Store },
] as const;

const exchanges = [
  {
    title: "Connect the buyer",
    kind: "Available with merchant access",
    from: "buyer",
    to: "seller",
    supporting: [] as string[],
    signal: "A2A v1 card + scoped credential",
    buyerView: "I can ask this merchant's seller about products.",
    operation: "The consumer app configures the seller endpoint. A tenant admin issues a separate merchant/seller/buyer-scoped credential; the public card alone grants no catalog access.",
    wire: "GET /.well-known/agent-card.json\nGET /api/v1/a2a/extendedAgentCard",
  },
  {
    title: "Ask about a product",
    kind: "Available: non-binding",
    from: "seller",
    to: "buyer",
    supporting: ["source", "authority"],
    signal: "Sourced price + inventory snapshot",
    buyerView: "Source: Shopify. Updated at the shown time. Final price and stock need confirmation.",
    operation: "AgenticOrg answers from valid cached OACP artifacts. Shopify is the operational source and Grantex issued authority; neither is called for every buyer question.",
    wire: "POST /api/v1/a2a/message:send\nmetadata: sourceLabel, freshnessLabel",
  },
  {
    title: "Buyer asks to purchase",
    kind: "Available: refusal boundary",
    from: "buyer",
    to: "seller",
    supporting: [],
    signal: "A2A intent is not an order",
    buyerView: "I cannot complete a purchase in this conversation. Final terms need the merchant and payment provider.",
    operation: "The synchronous seller A2A route refuses execution. A separate purchase-preparation path can assemble a non-executing handoff; it does not reserve stock or charge the buyer.",
    wire: "message:send -> refused / prepare separately\nallowedToExecute: false",
  },
  {
    title: "Check the payment rail",
    kind: "Available: capability only",
    from: "seller",
    to: "payment",
    supporting: [],
    signal: "Plural/P3P capability evidence",
    buyerView: "Payment eligibility is being checked, not charged.",
    operation: "The separate AgenticOrg verifier checks provider-owned capability and records a non-sensitive evidence reference. A capability response is not a mandate, checkout or paid state.",
    wire: "POST /api/v1/commerce/runtime/providers/plural-pine/mandate-capability/verify\nno payment execution",
  },
  {
    title: "Authorize with the provider",
    kind: "Requires approved integration",
    from: "buyer",
    to: "payment",
    supporting: [],
    signal: "Human + provider-owned authorization",
    buyerView: "Review the provider's terms and authorize there, if this merchant has enabled that journey.",
    operation: "The target paid flow needs merchant/provider onboarding, buyer consent where required, a provider-hosted authorization path and verified callbacks. This is not wired into the current external seller A2A route.",
    wire: "Provider-approved flow required\nnot implemented by A2A message:send",
  },
  {
    title: "Confirm order and receipt",
    kind: "Requires approved integration",
    from: "payment",
    to: "merchant",
    supporting: ["seller"],
    signal: "Provider result + merchant order truth",
    buyerView: "Show confirmed only after provider and merchant records agree.",
    operation: "A production purchase needs verified provider outcome, merchant order creation, inventory reconciliation and a buyer-safe receipt. Without both authorities, the result stays pending or blocked.",
    wire: "Verified provider callback + merchant confirmation\nnever infer paid from a prepared packet",
  },
] as const;

const buyerNames = [
  "Independent A2A client",
  "Muse-style buyer",
  "Instinct-style buyer",
  "Dots-style buyer",
];

export default function CommerceA2ATransaction() {
  const [step, setStep] = useState(0);
  const [buyer, setBuyer] = useState(buyerNames[0]);
  const exchange = exchanges[step];

  return (
    <section className="docs-transaction" aria-label="Third-party buyer transaction map">
      <div className="docs-transaction-heading">
        <div>
          <h2>From outside buyer to seller, provider and order</h2>
          <p>Follow what moves between parties when a shopper finds a product and asks to buy it.</p>
        </div>
        <label>
          Buyer app
          <select value={buyer} onChange={(event) => setBuyer(event.target.value)} aria-label="Illustrative buyer app">
            {buyerNames.map((name) => <option key={name}>{name}</option>)}
          </select>
        </label>
      </div>
      <div className="docs-transaction-map" aria-label="Commerce participants">
        {parties.map((party) => {
          const Icon = party.icon;
          const supporting: readonly string[] = exchange.supporting;
          const role = exchange.from === party.id
            ? "sending"
            : exchange.to === party.id
              ? "receiving"
              : supporting.includes(party.id)
                ? "supporting"
                : "idle";
          return (
            <div className={`docs-transaction-party docs-transaction-party-${party.id} is-${role}`} key={party.id}>
              <span className="docs-transaction-party-icon"><Icon size={22} aria-hidden="true" /></span>
              <strong>{party.id === "buyer" ? buyer : party.label}</strong>
              <small>{party.note}</small>
            </div>
          );
        })}
      </div>
      <div className="docs-transaction-message" role="status" aria-live="polite">
        <span>{parties.find((party) => party.id === exchange.from)?.label}</span>
        <ArrowRight size={18} aria-hidden="true" />
        <strong>{exchange.signal}</strong>
        <ArrowRight size={18} aria-hidden="true" />
        <span>{parties.find((party) => party.id === exchange.to)?.label}</span>
      </div>
      <div className="docs-transaction-body">
        <nav className="docs-transaction-steps" aria-label="Transaction steps">
          {exchanges.map((item, index) => (
            <button
              key={item.title}
              type="button"
              aria-current={step === index ? "step" : undefined}
              aria-label={`Step ${index + 1}: ${item.title}`}
              onClick={() => setStep(index)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{item.title}</strong>
              <small>{item.kind}</small>
            </button>
          ))}
        </nav>
        <div className="docs-transaction-detail" key={step}>
          <div className="docs-transaction-detail-top">
            <span className={step >= 4 ? "needs-integration" : "available"}>
              {step >= 4 ? <FileCheck2 size={15} aria-hidden="true" /> : <ShieldCheck size={15} aria-hidden="true" />}
              {exchange.kind}
            </span>
            <small>{step + 1} of {exchanges.length}</small>
          </div>
          <h3>{exchange.title}</h3>
          <div className="docs-transaction-detail-line"><b>Buyer sees</b><p>{exchange.buyerView}</p></div>
          <div className="docs-transaction-detail-line"><b>Behind the scene</b><p>{exchange.operation}</p></div>
          <pre aria-label="Illustrative message or handoff"><code>{exchange.wire}</code></pre>
          <div className="docs-transaction-controls">
            <button type="button" title="Previous transaction step" aria-label="Previous transaction step" disabled={step === 0} onClick={() => setStep(step - 1)}><ArrowLeft size={17} /></button>
            <span>{String(step + 1).padStart(2, "0")} / {String(exchanges.length).padStart(2, "0")}</span>
            <button type="button" title="Next transaction step" aria-label="Next transaction step" disabled={step === exchanges.length - 1} onClick={() => setStep(step + 1)}><ArrowRight size={17} /></button>
          </div>
        </div>
      </div>
      <p className="docs-transaction-disclaimer">
        This is an explanatory map, not a live transaction simulator. Muse, Instinct and Dots are illustrative buyer-app choices, not verified integrations. The current external A2A route is non-binding; a paid purchase still needs approved provider and merchant execution.
      </p>
    </section>
  );
}
