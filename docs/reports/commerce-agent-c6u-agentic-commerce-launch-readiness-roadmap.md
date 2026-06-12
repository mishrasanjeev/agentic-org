# Commerce Agent C6U Agentic Commerce Launch Readiness Roadmap

Status: internal planning roadmap only.

Data cutoff: 2026-06-09.

Source baseline:

- Grantex PR #544 merged at `710b0cb327e1c2992c3750a638c9b8ff5ab55e1c`.
- Grantex baseline gap file: `docs/internal/commerce-v1/commerce-v1-current-product-gap-brutal-analysis.md`.
- AgenticOrg baseline: `origin/main` at `d02657be67c4be256cd1f0b3d52d46e20c5de891`.

This roadmap is not production approval, real merchant approval, AgenticOrg public discovery approval, Grantex public discovery approval, checkout/payment approval, live provider approval, live Plural approval, production allowlist approval, protocol publication, external standards submission, certification, conformance evidence, compliance evidence, or a production-ready claim. It approves no public discovery, buyer channel launch, real merchant, checkout/payment, live provider, live Plural, production config, allowlist, cloud resource, provider call, merchant private API call, protocol publication, external submission, certification, compliance, conformance, standardization, or production launch.

## Executive Summary

This document converts the merged Grantex brutal gap analysis into the AgenticOrg execution roadmap. AgenticOrg owns buyer-agent creation, buyer channel UX, Grantex-only commerce connector behavior, buyer-safe error translation, channel-specific refusal behavior, session propagation, and cross-repo release ordering.

AgenticOrg must remain a buyer-agent layer. It must not become the merchant control plane, payment provider integration, Plural integration, merchant private API caller, production allowlist owner, or standards publisher. All commerce facts, consent/passport authority, merchant policy, checkout/payment safety, provider boundaries, connector source governance, audit evidence, and public discovery approval come from Grantex.

The safe AgenticOrg sequence is:

1. Keep public commerce discovery hidden until Grantex read-only discovery is approved and a separate AgenticOrg approval exists.
2. Prove connector alias parity with Grantex contracts.
3. Build a first-party web read-only buyer session before chat platform adapters.
4. Add buyer-agent creation and session linking.
5. Add channel-specific refusal, consent handoff, and revocation handling.
6. Add grounded buyer answers and support/order status only after Grantex exposes safe contracts.
7. Add sandbox checkout behavior only after Grantex consent/passport, order, fulfillment, and payment safety gates exist.
8. Keep live provider, live Plural, and direct provider calls out of AgenticOrg.
9. Package A2A/MCP adapter metadata only as internal preparation until external publication is separately approved.

## Current Go-Live Verdict

AgenticOrg commerce is not live-ready. It is not ready for public buyer channel launch, public commerce discovery, real merchant exposure, checkout/payment enablement, live provider use, live Plural use, production allowlists, standards publication, certification, conformance, compliance, or production readiness claims.

Current useful foundation:

- Grantex-only commerce connector aliases.
- Read-only buyer discovery preview consumption.
- Channel-neutral buyer session orchestration.
- Refusal behavior for missing consent, stale inventory, disabled merchant/agent, policy denial, and unsupported claims.
- Tests and evals proving no direct provider call behavior for the current commerce demo/eval path.

Current blockers:

- No production-grade buyer-agent creation flow for commerce.
- No production channel launch for ChatGPT, Claude, Gemini, WhatsApp, Telegram, web, mobile, API, MCP, or A2A.
- No approved Grantex public discovery dependency.
- No real merchant handoff approval.
- No post-purchase order/fulfillment/refund/support contracts to consume.
- No live payment or provider readiness.
- No external protocol publication approval.

## P0/P1/P2 Backlog Table

| Priority | Workstream | Owner | Dependencies | Acceptance criteria | Validation gates |
| --- | --- | --- | --- | --- | --- |
| P0 | Grantex contract parity for AgenticOrg aliases | AgenticOrg + Grantex | Grantex OpenAPI/MCP/tool schemas | Every AgenticOrg commerce alias maps to one current Grantex contract and fails closed on schema mismatch. | Alias mapping test, response schema test, no-provider-call test. |
| P0 | Public commerce discovery gate discipline | AgenticOrg | Grantex read-only approval and smoke | Public MCP/A2A commerce metadata remains hidden until separate approval. | Gate test, metadata scan, rollback smoke. |
| P0 | First-party web buyer read-only session | AgenticOrg | Grantex read-only preview, session envelope | Buyer can browse approved Grantex facts in one web flow with clear limitations and no write/payment capability. | Web smoke, Grantex-only call trace, refusal evals. |
| P0 | Buyer-agent creation and onboarding | AgenticOrg | Identity/session model, channel policy | Buyer can create/select a commerce buyer agent with Grantex-only labels and safe scope boundaries. | UI/API smoke, channel label scan, unsupported-action refusals. |
| P0 | Consent/session/revocation propagation | AgenticOrg + Grantex | Grantex consent/passport lifecycle | Revocation, merchant disable, disabled agent, and expired passport block active AgenticOrg sessions. | Cache invalidation tests, refusal evals, session expiry tests. |
| P0 | Error translation from Grantex | AgenticOrg | Grantex error taxonomy | Buyer messages are safe, actionable, and do not leak raw provider, merchant private API, or secret details. | Error fixture tests, private-detail scan. |
| P0 | Cross-repo release ordering | AgenticOrg + Grantex | Grantex roadmap stages | AgenticOrg never advertises a commerce capability before Grantex has approved and shipped the dependency. | Release checklist, dependency matrix, PR template review. |
| P0 | Direct-call ban regression coverage | AgenticOrg | Current commerce connector/evals | Every commerce-capable path proves no direct payment provider and no merchant private API calls. | Static scan, unit tests, eval assertions. |
| P0 | Channel-specific refusal packs | AgenticOrg | Buyer web baseline, Grantex refusals | Each channel has refusal copy for disabled discovery, missing consent, checkout blocked, stale inventory, provider call blocked, private API blocked, and unsupported claims. | Golden evals per channel. |
| P0 | Buyer support and order status dependency | AgenticOrg + Grantex | Grantex order/support contracts | Buyer can ask for safe order/support status only after Grantex provides source facts. | Contract test, no-hallucination evals. |
| P1 | ChatGPT, Claude, Gemini adapters | AgenticOrg | Web buyer read-only baseline, channel approval | Each LLM/chat adapter has exact tool availability, auth/session model, rate limit, and consent handoff. | Adapter smoke, prompt-injection/refusal evals. |
| P1 | WhatsApp and Telegram adapters | AgenticOrg | Async consent/session model | Async channel UX handles expiry, delayed replies, refusal, and support handoff. | Channel simulator tests, privacy retention review. |
| P1 | MCP/A2A adapter metadata | AgenticOrg + Grantex | Public discovery approval and adapter map | Metadata reflects only approved Grantex capabilities and does not imply checkout/payment/live readiness. | Metadata diff scan, overclaim scan, gate smoke. |
| P1 | Buyer privacy and transcript minimization | AgenticOrg | Privacy policy review, support model | Buyer transcripts and commerce context are minimized, redacted, retained, and deleted according to policy. | Privacy review, redaction tests. |
| P2 | Multi-merchant discovery and ranking | AgenticOrg + Grantex | Grantex multi-merchant public discovery | Ranking is explainable, fair, policy-safe, and grounded. | Ranking audit, sponsored-result disclosure if applicable. |

## Grantex-Owned Workstreams

AgenticOrg depends on these Grantex-owned streams and must not duplicate them:

| Workstream | AgenticOrg dependency | Required Grantex output before AgenticOrg consumes it |
| --- | --- | --- |
| Merchant control plane | Buyer-facing metadata depends on approved merchant facts. | Reviewed public-safe profile, merchant status, approval state, rollback owner. |
| Public discovery and allowlist | AgenticOrg can expose commerce metadata only after Grantex read-only approval. | Read-only public discovery gate, approved merchant scope, smoke evidence. |
| Consent/passport/policy | AgenticOrg must rely on Grantex for protected-action authority. | Consent request, passport exchange, verify, revoke, policy refusal, audit evidence. |
| Cart/checkout/payment | AgenticOrg may call Grantex only after Grantex approves the matching stage. | Idempotent cart, checkout, payment intent, mock/sandbox provider, no live mode unless later approved. |
| Order/fulfillment/support/refund | AgenticOrg can only answer from Grantex facts. | Order and support APIs with buyer-safe status and refusal semantics. |
| Connector/source governance | AgenticOrg must not call merchant private APIs. | Source precedence, connector health, public-safe facts, stale-data markers. |
| Open protocol packaging | AgenticOrg can provide A2A/MCP mapping only as adapter input. | Canonical schemas, adapter map, examples, security/privacy posture. |

## AgenticOrg-Owned Workstreams

| Workstream | Implementation responsibility | Dependencies | Exit criteria |
| --- | --- | --- | --- |
| Buyer-agent creation | Create/select commerce buyer agent, label capability boundaries, bind buyer session. | Identity/session model, Grantex read-only contract. | Buyer can start a read-only session without public discovery or payment claims. |
| Buyer channel adapters | Web first; ChatGPT, Claude, Gemini, WhatsApp, Telegram, API, MCP, A2A later. | Web baseline, channel limitations, consent model. | Each channel has exact capabilities, refusal copy, smoke tests, and rollback. |
| Grantex connector/client | Maintain aliases, request/response mapping, idempotency requirements, and error translation. | Grantex contract parity. | No direct provider or merchant private API path exists in commerce flows. |
| Grounded buyer responses | Use only Grantex facts; refuse when data is stale, missing, or unsupported. | Grantex preview and error taxonomy. | No unsupported claims about availability, delivery, warranty, tax, returns, refunds, discounts, EMI, support, or payment. |
| Session/revocation propagation | Invalidate sessions on consent/passport revocation, merchant disable, agent disable, expiry, or policy denial. | Grantex revocation and status APIs. | Active sessions fail closed when authority changes. |
| Release ordering | Block PRs and releases that advertise unavailable Grantex capability. | Grantex roadmap stages. | Release checklist links every AgenticOrg capability to a Grantex-approved dependency. |

## Cross-Repo Dependencies

| Dependency | Grantex owns | AgenticOrg owns | Safe sequencing rule |
| --- | --- | --- | --- |
| Read-only discovery | Approved public-safe merchant payload and discovery gate. | Hidden-by-default buyer metadata and rendering. | Grantex approval and smoke first; AgenticOrg approval second. |
| Buyer session | Capability profile, refusal taxonomy, merchant status. | Channel session binding and cache invalidation. | Session read-only first; checkout later. |
| Consent/passport | Consent authority, passport verification, revocation. | User-facing handoff, scoped storage, revocation propagation. | Payment-affecting actions remain blocked without active passport. |
| Checkout/payment | Cart/payment APIs, provider-neutral safety, audit. | Grantex-only tool calls and final user confirmation messaging. | Sandbox checkout first; live provider never direct from AgenticOrg. |
| Order/support | Source-of-truth order and support facts. | Buyer-safe status and escalation messages. | No post-purchase answer without Grantex facts. |
| Protocol adapters | Canonical schemas and adapter mapping. | A2A/MCP channel metadata mapping. | Internal preparation only until external approval. |

## Buyer-Agent Creation Workstream

Owner: AgenticOrg.

Goal: make buyer-agent creation understandable and safe before any public channel launch.

Dependencies:

- Grantex read-only capability profile.
- AgenticOrg identity and session binding.
- Channel labeling.
- Refusal copy.
- Public discovery gate remains disabled until separate approval.

Acceptance criteria:

- A buyer can create or select a commerce buyer agent in a first-party web flow.
- The UI and agent profile say the agent uses Grantex-controlled commerce facts only.
- The agent does not advertise checkout, payment, live provider, direct merchant API, direct provider API, certification, conformance, compliance, or production-ready capability.
- Unsupported actions are refused before any Grantex call when the scope is not available.

Validation gates:

- Web smoke test.
- Static scan for direct provider and merchant private API paths.
- Golden refusal evals.
- Overclaim scan.

## Seller Self-Serve Onboarding Workstream

Owner: Grantex. AgenticOrg dependency only.

AgenticOrg must not build seller onboarding, merchant approval, KYB/KYC collection, production allowlists, connector credential entry, or public discovery approval. It consumes only public-safe, Grantex-approved outputs.

AgenticOrg acceptance criteria:

- Commerce metadata is hidden until Grantex has approved one read-only merchant scope and a separate AgenticOrg approval exists.
- Buyer-facing screens reference only Grantex-approved display fields.
- No real merchant names or IDs are hardcoded in AgenticOrg before approval.

Validation gates:

- Metadata scan.
- Gate-state test.
- Public payload preview review.

## Merchant Existing-System Connector Workstream

Owner: Grantex. AgenticOrg dependency only.

AgenticOrg must never call ERP, PIM, POS, OMS, ecommerce admin APIs, payment providers, or merchant private APIs for commerce. It receives only Grantex-controlled public-safe facts, stale markers, refusal codes, and support references.

AgenticOrg acceptance criteria:

- Connector responses include freshness and source markers from Grantex.
- Buyer answers refuse when source freshness or authority is insufficient.
- Tests prove no merchant private API connector is reachable through commerce buyer flows.

Validation gates:

- Static direct-call scan.
- Refusal eval for stale inventory and source unavailable.
- Private-detail scan.

## Catalog, Inventory, Pricing, Tax, Warranty, And Returns Workstream

Owner: Grantex for facts; AgenticOrg for buyer wording.

AgenticOrg must ground all buyer responses in Grantex tool data. If Grantex does not provide final price, inventory quantity, serviceability, tax, warranty, return, refund, discount, coupon, EMI, or support facts, AgenticOrg must say it cannot confirm rather than infer.

Acceptance criteria:

- Buyer answers cite only available facts in plain language.
- Missing final price, stale inventory, unknown delivery, ambiguous warranty, unknown return policy, unsupported discount, or unsupported EMI produces safe refusal or caveat.
- No local AgenticOrg merchant catalog cache becomes a source of truth.

Validation gates:

- Golden no-hallucination evals.
- Stale inventory evals.
- Response text scan.

## Fulfillment, Delivery, And Order Lifecycle Workstream

Owner: Grantex for order source of truth; AgenticOrg for buyer-facing status.

AgenticOrg must not promise delivery or order status unless Grantex exposes it as source data.

Acceptance criteria:

- Buyer can ask order status only after Grantex order APIs exist.
- AgenticOrg displays statuses using Grantex error and state taxonomy.
- Unknown or stale status results in support handoff, not invented delivery dates.

Validation gates:

- Order status fixture tests.
- No unsupported delivery claim scan.
- Support fallback eval.

## Refunds, Disputes, And Support Workstream

Owner: Grantex for workflow; AgenticOrg for intake/handoff UX after contracts exist.

Acceptance criteria:

- Buyer support flow uses Grantex support/refund/dispute references.
- AgenticOrg does not execute refunds, call payment providers, or promise refund approval.
- AgenticOrg preserves privacy by minimizing transcripts and redacting sensitive details.

Validation gates:

- Support handoff tests.
- Privacy/transcript review.
- Provider-call scan.

## Payments, Live Provider, And Plural Readiness Workstream

Owner: Grantex. AgenticOrg must remain a Grantex-only caller.

AgenticOrg must not store Plural credentials, call Plural directly, call any payment provider directly, hold provider credentials, or set live payment flags.

Acceptance criteria:

- Payment-affecting tools are available only when Grantex exposes and approves the matching capability.
- Final user confirmation remains explicit.
- Live provider paths stay out of AgenticOrg code and docs except as blocked dependencies.

Validation gates:

- Direct provider marker scan.
- Payment action guardrail tests.
- Consent/passport refusal evals.
- Overclaim scan.

## Consent, Passport, Session, And Revocation Workstream

Owner: Grantex for authority; AgenticOrg for user journey and propagation.

Acceptance criteria:

- Buyer sees clear consent purpose, merchant, items or scope, max amount, currency, expiry, and revocation path where applicable.
- AgenticOrg refuses checkout/payment when consent is missing, denied, expired, revoked, over cap, wrong merchant, wrong currency, or blocked by policy.
- Revocation and merchant disable invalidate active sessions and cached capability labels.

Validation gates:

- Session expiry tests.
- Revocation propagation tests.
- Consent copy review.
- Passport redaction scan.

## Audit, Evidence, Observability, And Incident Response Workstream

Owner: Grantex + AgenticOrg.

AgenticOrg must provide channel and connector observability without logging secrets, passports, raw provider payloads, merchant private data, or sensitive buyer transcript content.

Acceptance criteria:

- AgenticOrg logs correlation IDs and high-level refusal/error classes.
- Buyer channel incidents have owners, runbooks, rollback, and support paths.
- Commerce public discovery rollback hides MCP/A2A metadata again.

Validation gates:

- Log redaction tests.
- Alert route review.
- Rollback smoke.
- Secret/private scan.

## Security, Privacy, Compliance, KYC/KYB, Fraud, And Moderation Workstream

Owner: Grantex + AgenticOrg.

AgenticOrg responsibilities:

- Buyer account/session security.
- Channel abuse controls.
- Prompt-injection and product-content caution in buyer responses.
- Transcript minimization and retention policy.
- Moderation of agent responses when Grantex facts include unsafe content.
- Refusal of regulated or unsupported claims.

Acceptance criteria:

- No public buyer channel launches without privacy, moderation, abuse, support, and incident review.
- AgenticOrg does not treat Grantex merchant approval as broad AgenticOrg launch approval.
- Fraud and abuse signals from channels can be routed to Grantex when commerce actions are involved.

Validation gates:

- Threat model review.
- Prompt-injection evals.
- Privacy retention review.
- Abuse test cases.

## Public Discovery And Production Allowlist Readiness Workstream

Owner: Grantex for merchant/allowlist; AgenticOrg for public metadata gate.

Acceptance criteria:

- AgenticOrg public MCP/A2A metadata stays hidden until Grantex read-only discovery is approved, Grantex smoke passes, and AgenticOrg receives separate approval.
- AgenticOrg does not set or document any allowlist entries.
- AgenticOrg rollback hides commerce metadata without affecting non-commerce health.

Validation gates:

- MCP/A2A metadata scan.
- Public discovery gate test.
- Rollback smoke.
- Overclaim scan.

## Open Protocol Packaging And Adapter-Readiness Workstream

Owner: Grantex + AgenticOrg.

AgenticOrg role: provide channel and A2A/MCP mapping input to Grantex-led internal protocol preparation. AgenticOrg must not publish protocol materials externally or claim standardization.

Acceptance criteria:

- A2A/MCP mapping is internal and adapter-based.
- Grantex and AgenticOrg are implementation examples only.
- No public submission, public protocol publication, certification, conformance, compliance, standard, RFC, IETF, NIST, NCCoE, UCP, ACP, AP2, schema.org, MCP, A2A, provider, or live-payment claim is introduced.

Validation gates:

- Adapter wording review.
- Overclaim scan.
- Public-safe example scan.

## schema.org/UCP/ACP/AP2/MCP/A2A Adapter Dependency Map

| Surface | AgenticOrg dependency | AgenticOrg posture | Blocker before public claim |
| --- | --- | --- | --- |
| schema.org JSON-LD | Grantex product/offer preview. | Consume only through Grantex-controlled facts. | Final price, availability, delivery, warranty, return, and freshness proof. |
| UCP-style capability profile | Grantex capability profile. | Render scoped capabilities and refusals. | Capability versioning and policy semantics. |
| ACP-style checkout shape | Grantex cart/checkout safety. | Refuse checkout unless Grantex approves the stage. | Consent/passport, order, fulfillment, and payment safety gates. |
| AP2-style evidence | Grantex consent and audit evidence. | Surface only buyer-safe summaries. | Signature, privacy, registry, and retention reviews. |
| MCP/native API | Grantex MCP/REST tools. | Call Grantex aliases only. | Contract parity and auth/tenant boundary tests. |
| A2A | AgenticOrg public metadata. | Hidden until separately approved. | Grantex public discovery and AgenticOrg approval. |
| Merchant connector metadata | Grantex source precedence. | Display freshness and refusal reasons only. | Connector dry-run and source-of-truth enforcement. |

## What Must Happen Before Sandbox Pilot

- AgenticOrg branch contains docs/tests only for planning unless a later implementation PR is approved.
- Commerce connector remains Grantex-only.
- Public commerce discovery remains hidden.
- No real merchant data, provider details, secrets, private URLs, production config, or allowlists are introduced.
- Synthetic/demo data is labeled.
- Buyer refusals cover missing consent, public discovery disabled, checkout disabled, provider call blocked, merchant private API blocked, and stale inventory.

## What Must Happen Before Limited Real Merchant Pilot

- Grantex real merchant readiness packet exists with non-secret evidence references.
- AgenticOrg receives only Grantex-approved public-safe merchant fields.
- Legal, security, product, ops, support, rollback, and evidence owners exist.
- AgenticOrg dependency approval exists.
- Public commerce discovery remains separately gated.
- Checkout/payment remains separately gated.

## What Must Happen Before Public Discovery

- Grantex read-only public discovery is approved and smoke-tested.
- AgenticOrg public MCP/A2A commerce metadata is reviewed before exposure.
- Metadata includes no secrets, private merchant data, provider credentials, direct provider paths, checkout/payment readiness, live provider language, live Plural language, certification, compliance, conformance, standardization, or production-ready claims.
- Rollback smoke proves metadata can be hidden again.

## What Must Happen Before Checkout/Payment Enablement

- Grantex order, fulfillment, support, return/refund, consent/passport, policy, audit, idempotency, webhook, and reconciliation gates pass.
- AgenticOrg checkout/payment aliases remain Grantex-only and require active consent/passport.
- Buyer channel UX requires final user confirmation and safe refusal.
- No live provider or live Plural path exists in AgenticOrg.

## What Must Happen Before Live Plural/Live Provider Enablement

- Grantex live provider readiness is separately approved.
- AgenticOrg does not receive provider credentials.
- AgenticOrg does not call Plural or any payment provider directly.
- AgenticOrg wording says only that Grantex controls the approved payment path.
- Buyer support, incident response, rollback, consent, and privacy gates are approved for the target channel.

## What Must Happen Before External Protocol Publication Or Standards Submission

- Grantex owns the protocol publication decision.
- AgenticOrg contributes only internal adapter mapping input.
- A2A/MCP metadata examples are public-safe and synthetic until legal/publication review.
- No external submission, public protocol publication, standards-track, RFC, NIST, NCCoE, certification, conformance, compliance, or standardization claim is made without separate approval.

## Stop Conditions

Stop the AgenticOrg path if any condition is true:

- A change enables public commerce discovery without separate approval.
- A change exposes `commerce_sales_agent`, `agenticorg_commerce_sales_agent`, or `grantex_commerce:*` metadata before Grantex approval and AgenticOrg approval.
- A change calls payment providers, Plural, or merchant private APIs directly.
- A change stores provider credentials or merchant connector credentials.
- A change sets production allowlists or production config values.
- A change claims production readiness, certification, compliance, conformance, standardization, public-launch readiness, IETF submission, NIST submission, NCCoE acceptance, RFC status, or live-payment readiness.
- A change treats synthetic/demo data as real merchant approval.
- A buyer channel cannot revoke or hide commerce capability after merchant disable, consent revocation, or rollback.

## Suggested Next 10 Implementation PR Slices In Exact Order

1. C6U1: Grantex docs-only CI/deploy guard and release-control inventory.
2. C6U2: Cross-contract parity matrix for Grantex OpenAPI, MCP tools, SDKs, portal clients, and AgenticOrg aliases.
3. C6U3: Real merchant readiness packet with reviewer RBAC and non-secret evidence references, still no public discovery.
4. C6U4: Connector credential-reference design with vault-only references, still no outbound sync.
5. C6U5: First connector sandbox dry-run against a merchant-approved non-production source with source precedence evidence.
6. C6U6: AgenticOrg first-party web buyer read-only session smoke using Grantex-only calls.
7. C6U7: Grantex order foundation with line item snapshot, status, support reference, and audit.
8. C6U8: Fulfillment, delivery, support, and return/refund handoff contracts across Grantex and AgenticOrg.
9. C6U9: Sandbox checkout E2E with consent/passport, order handoff, mock or sandbox provider, webhook/reconciliation, and rollback drill.
10. C6U10: Live provider readiness packet and review checklist, with live mode still blocked until separate approval.

## Readiness Checklist By Stage

| Stage | AgenticOrg ready evidence | Must remain false |
| --- | --- | --- |
| Internal sandbox | Synthetic labels, Grantex-only connector calls, refusal evals, no-secret scans. | Public discovery, real merchant exposure, checkout/payment, live provider, live Plural. |
| Real merchant private readiness | Grantex-approved public-safe fields and AgenticOrg dependency approval plan. | Public MCP/A2A exposure, production allowlist, checkout/payment. |
| Read-only public pilot | Grantex smoke passed, AgenticOrg metadata reviewed, gate enabled only after approval, rollback smoke. | Checkout/payment, live provider, certification/conformance/compliance claim. |
| Sandbox checkout pilot | Grantex sandbox checkout, AgenticOrg consent UX, final user confirmation, support fallback, no provider direct call. | Live money movement, live provider credentials, broad public launch. |
| Paid limited pilot | Grantex live readiness approved, AgenticOrg channel approved, support staffed, caps and rollback in place. | Direct provider calls, unreviewed channels, autonomous delegated payments. |
| Broader launch | Repeatable channel onboarding, privacy/moderation/abuse ops, grounded answers, support and incident operations. | Unsupported channel/provider/category claims. |
| External protocol publication | Internal A2A/MCP adapter input reviewed and public-safe. | External standards claim, certification, compliance, conformance, or RFC status. |

## Layman Explanation

For sellers: sellers will eventually use Grantex to set up their merchant profile, connect or upload product data, prove readiness, and ask for public discovery or checkout approval. AgenticOrg does not approve sellers and does not connect to seller private systems directly. It only shows buyer agents the facts Grantex has approved for agent use.

For buyers: buyers will eventually create or select a commerce buyer agent in a supported channel. The agent can browse approved Grantex merchant facts, explain what it knows, refuse when facts are missing or stale, ask for consent before protected actions, and show order/support status only when Grantex provides it. If payment is not approved, the agent must say so rather than improvise.

## Explicit Non-Approval

This roadmap does not approve production launch, public commerce discovery, real merchants, buyer channel launch, checkout/payment, live providers, live Plural, production allowlists, protocol publication, external standards submission, certification, compliance, conformance, standardization, production readiness, merchant approval, provider calls, merchant private API calls, cloud resources, production config changes, or secrets handling. Every AgenticOrg launch stage requires a later explicit approval and fresh gate review.
