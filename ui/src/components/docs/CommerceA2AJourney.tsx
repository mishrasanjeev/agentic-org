// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import {
  ArrowRight,
  BookCheck,
  Check,
  CreditCard,
  Database,
  KeyRound,
  MessageSquare,
  RotateCcw,
  ShieldCheck,
  Store,
} from "lucide-react";

const stages = [
  {
    title: "Create the seller",
    owner: "Merchant + AgenticOrg",
    icon: Store,
    action: "Save the merchant-scoped Seller Commerce Agent configuration in Commerce Runtime.",
    proof: "Merchant, seller, source, channel and provider references belong to the right tenant.",
    boundary: "An onboarding packet does not publish a public merchant or approve a purchase.",
    question: "The seller packet is saved. What comes next?",
    choices: ["Turn on public catalog immediately", "Confirm scope and approvals before publishing"],
    correct: 1,
    anchor: "checkpoint-1-create-the-seller",
  },
  {
    title: "Bring source facts",
    owner: "Shopify + AgenticOrg",
    icon: Database,
    action: "Connect approved read-only Shopify access and sync products, variants, images, prices and inventory snapshots.",
    proof: "Compare SKU, currency, price and inventory timestamp with the merchant system.",
    boundary: "Shopify remains the operational source; no catalog scraping or guessed stock.",
    question: "A webhook says a product changed. Is the buyer-facing price now fresh?",
    choices: ["Yes, the webhook is sufficient", "No, sync and check the resulting source snapshot"],
    correct: 1,
    anchor: "checkpoint-2-bring-source-facts",
  },
  {
    title: "Establish authority",
    owner: "Grantex + AgenticOrg",
    icon: ShieldCheck,
    action: "Request scoped OACP artifacts, verify them and retain valid source/freshness metadata in the cache.",
    proof: "Artifact scope, source refs, expiry and revocation posture match the merchant and seller.",
    boundary: "Grantex is not called for every non-binding buyer question.",
    question: "Grantex is temporarily unavailable but a cached artifact is still valid. Can product Q&A continue?",
    choices: ["Yes, for non-binding answers with freshness labels", "No, route every question through Grantex"],
    correct: 0,
    anchor: "checkpoint-3-establish-authority",
  },
  {
    title: "Admit an outside buyer",
    owner: "Merchant admin + buyer app",
    icon: KeyRound,
    action: "Issue one seller/merchant/buyer-scoped A2A credential and configure the external agent's endpoint.",
    proof: "The generic card is public; the extended card works only with the approved credential.",
    boundary: "A platform name such as Muse or Dots is not verified identity or automatic distribution.",
    question: "The buyer app found the public Agent Card. Can it see this merchant's catalog?",
    choices: ["Yes, the card grants access", "No, it needs merchant-scoped buyer access"],
    correct: 1,
    anchor: "checkpoint-4-admit-an-outside-buyer",
  },
  {
    title: "Prove the conversation",
    owner: "External buyer + seller agent",
    icon: MessageSquare,
    action: "Send an A2A v1 text question and inspect source, freshness, refusal and scope metadata.",
    proof: "Product Q&A succeeds from valid evidence; purchase intent, cross-merchant access and revoked tokens fail.",
    boundary: "The A2A message route is non-binding and cannot create an order or reserve inventory.",
    question: "The buyer sends 'buy two now' through message:send. What is the correct result?",
    choices: ["A paid order", "A refusal or non-executing handoff guidance"],
    correct: 1,
    anchor: "checkpoint-5-prove-the-conversation",
  },
  {
    title: "Hand off payment",
    owner: "Merchant + Pine Labs Plural/P3P",
    icon: CreditCard,
    action: "Prepare the purchase context and verify provider capability; complete authorization and execution with the provider and merchant systems.",
    proof: "A paid state needs provider confirmation, merchant order/inventory reconciliation and a buyer-safe receipt.",
    boundary: "Current A2A seller Q&A does not create a Plural mandate, checkout or payment.",
    question: "Plural capability verification succeeded. Is the buyer's payment complete?",
    choices: ["Yes, capability means paid", "No, provider and merchant confirmation are still required"],
    correct: 1,
    anchor: "checkpoint-6-hand-off-payment",
  },
] as const;

export default function CommerceA2AJourney() {
  const [active, setActive] = useState(0);
  const [reviewed, setReviewed] = useState<number[]>([]);
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const stage = stages[active];
  const StageIcon = stage.icon;
  const completed = reviewed.includes(active);

  function markReviewed() {
    if (!completed && answers[active] === stage.correct) setReviewed([...reviewed, active]);
    if (active < stages.length - 1) setActive(active + 1);
  }

  return (
    <section className="docs-commerce-journey" aria-label="Seller A2A commerce learning journey">
      <div className="docs-commerce-journey-head">
        <div>
          <p className="docs-eyebrow">Guided integration map</p>
          <h2>Six checkpoints from store to buyer agent</h2>
          <p>Select a checkpoint to see the action, evidence and safety boundary.</p>
        </div>
        <span className="docs-commerce-journey-count" role="status">
          <BookCheck size={17} aria-hidden="true" /> {reviewed.length} / {stages.length} reviewed
        </span>
      </div>
      <div
        className="docs-commerce-journey-progress"
        role="progressbar"
        aria-label="Learning checkpoints reviewed"
        aria-valuenow={reviewed.length}
        aria-valuemin={0}
        aria-valuemax={stages.length}
      >
        <span style={{ width: `${(reviewed.length / stages.length) * 100}%` }} />
      </div>
      <div className="docs-commerce-journey-stages" aria-label="Choose a checkpoint">
        {stages.map((item, index) => {
          const Icon = item.icon;
          return (
            <button
              key={item.title}
              type="button"
              className={`docs-commerce-journey-stage ${index === active ? "is-active" : ""}`}
              aria-pressed={index === active}
              aria-label={`View checkpoint ${index + 1}: ${item.title}`}
              onClick={() => setActive(index)}
              title={`View checkpoint ${index + 1}: ${item.title}`}
            >
              <span className="docs-commerce-journey-stage-icon">
                {reviewed.includes(index) ? <Check size={18} /> : <Icon size={18} />}
              </span>
              <span className="docs-commerce-journey-stage-label">
                <small>{String(index + 1).padStart(2, "0")}</small>
                <strong>{item.title}</strong>
              </span>
            </button>
          );
        })}
      </div>
      <div className="docs-commerce-journey-detail" key={active}>
        <div className="docs-commerce-journey-detail-title">
          <StageIcon size={24} aria-hidden="true" />
          <div>
            <span>{stage.owner}</span>
            <h3>{stage.title}</h3>
          </div>
        </div>
        <dl>
          <div><dt>Do</dt><dd>{stage.action}</dd></div>
          <div><dt>Check</dt><dd>{stage.proof}</dd></div>
          <div><dt>Boundary</dt><dd>{stage.boundary}</dd></div>
        </dl>
        <fieldset className="docs-commerce-journey-question">
          <legend>Decision check: {stage.question}</legend>
          <div>
            {stage.choices.map((choice, index) => (
              <label key={choice}>
                <input
                  type="radio"
                  name={`commerce-checkpoint-${active}`}
                  checked={answers[active] === index}
                  onChange={() => setAnswers({ ...answers, [active]: index })}
                />
                <span>{choice}</span>
              </label>
            ))}
          </div>
          {answers[active] !== undefined && (
            <p role="status">
              {answers[active] === stage.correct
                ? "Correct. This checkpoint is ready to review."
                : `Not yet. Recheck the boundary: ${stage.boundary}`}
            </p>
          )}
        </fieldset>
        <div className="docs-commerce-journey-detail-actions">
          <a href={`#${stage.anchor}`}>Read the full step <ArrowRight size={15} aria-hidden="true" /></a>
          <button type="button" onClick={markReviewed} disabled={(completed && active === stages.length - 1) || (!completed && answers[active] !== stage.correct)}>
            {completed && active === stages.length - 1 ? "Reviewed" : completed ? "Next checkpoint" : "Mark reviewed"}
            <ArrowRight size={15} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className="docs-commerce-journey-foot">
        <p>This is learning progress on this page, not a connection test or launch approval.</p>
        {reviewed.length > 0 && (
          <button type="button" onClick={() => { setReviewed([]); setAnswers({}); setActive(0); }} title="Reset learning progress">
            <RotateCcw size={14} aria-hidden="true" /> Reset
          </button>
        )}
      </div>
    </section>
  );
}
