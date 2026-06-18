# Commerce Agent C6W5 - Commitment Boundary Resolver

Status: implementation foundation, internal-only, non-enabling.

## Scope

C6W5 adds a local AgenticOrg resolver for commitment-boundary decisions over cached signed Grantex OACP artifacts and C6W4 adapter previews.

This slice adds pure helper logic, tests, and internal documentation only. It does not add endpoints, migrations, workflows, provider adapters, public discovery, checkout/payment, live provider, live Plural, merchant private API, allowlist, cloud, deploy, or external protocol publication behavior.

AgenticOrg remains the buyer/seller agent runtime. Grantex remains the trust, protocol, policy, and canonical-artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own payment and mandate execution.

## Commitment Boundary Model

The resolver classifies actions into:

- Non-binding preview: browse merchant profile, inspect seller card, compare catalog summaries, explain policy, explain available capabilities, show source/freshness labels, prepare buyer question, and prepare seller-agent remediation suggestion.
- Commitment-adjacent: prepare draft quote, prepare draft cart, ask merchant/seller agent to refresh source facts, prepare non-binding reservation request, prepare mandate capability check request, and prepare human confirmation prompt.
- Commitment-bound: price lock, inventory hold, reservation, order placement, payment intent, mandate setup/use, cancellation, refund request, return authorization, and support escalation with merchant SLA promise.
- Always blocked in C6W5: live payment execution, live Plural/provider calls, public discovery enablement, production checkout/payment creation, merchant or provider private API calls, protocol publication/submission, certification/conformance claims, and final delivery/refund/settlement/payout promises without source artifact authority.

Every result includes action_class, allowed_to_preview, allowed_to_prepare, allowed_to_execute, refusal_or_escalation_reason, required_fresh_artifact_families, source_artifact_ids, freshness_summary, risk_tier, offline_mode_status, buyer_safe_message, blocked_capabilities, and non-authoritative transaction flags.

allowed_to_execute is always false in C6W5.

## Offline Commitment Mode

Non-binding preview may continue from valid cached artifacts and a valid adapter preview. Commitment-adjacent and commitment-bound actions require the local artifact set to be present, scoped, fresh, in TTL, and within revocation/risk posture before AgenticOrg may prepare a request.

If Grantex is unavailable but cached TTL is valid, AgenticOrg may continue preview and prepare requests. If a buyer asks for final commitment while Grantex is unavailable, the resolver returns buyer-safe prepared-not-executed wording. Critical actions are blocked offline.

Adapter previews cannot override expired, revoked, missing, stale, or ambiguous OACP artifacts. Seller cards and protocol adapters are never transaction authority.

## TTL And Risk Defaults

Artifact TTL defaults:

- merchant capability: 24h.
- seller agent capability: 6h.
- policy: 6h.
- catalog: 6h.
- offer: 15m.
- price: 5m, or 60s for dynamic price.
- inventory: 60s, or 30s for high-velocity goods.
- public discovery: 15m display/read only.
- mandate capability: 2m at commitment boundary.
- protocol adapter: 24h max and never longer than referenced artifacts.

Risk defaults:

- Low: up to INR 25,000 / USD 300 equivalent, non-binding draft/quote only.
- Medium: up to INR 10,000 / USD 125 equivalent for price-lock, inventory-hold, reservation, and support preparation with source confirmation.
- High: up to INR 5,000 / USD 60 equivalent for order/payment/cancel/refund/return preparation, still non-executing in C6W5.
- Critical: blocked offline.

Missing or ambiguous amount, currency, or quantity fails closed for preparation.

## Toll Booth Boundary

Grantex does not become a synchronous toll booth for browse, comparison, education, recommendation, or other non-binding messages. AgenticOrg can use cached valid Grantex artifacts locally subject to TTL, freshness, revocation, unsupported capability, and scope rules.

AgenticOrg does not invent commerce facts from adapter previews. Channel messages must preserve source, freshness, unsupported capability, and non-authoritative wording.

## What This Does Not Enable

C6W5 does not enable:

- Public discovery.
- Production Commerce V1.
- Checkout/payment creation.
- Payment capture or debit.
- Live payments.
- Live provider use.
- Live Plural use.
- Provider calls.
- Carrier or shipping provider calls.
- Merchant private API calls.
- Connector credential export.
- Production allowlists.
- Public OACP publication.
- External protocol submission.
- Certification, compliance, conformance, standardization, production readiness, public-launch readiness, merchant approval, checkout approval, payment approval, live provider readiness, or OACP public readiness claims.

## Future Slices

Future slices must add separate evidence and approval paths before any real execution:

- Commitment evidence reconciliation with merchant systems.
- Provider-owned mandate verification with non-production evidence first.
- Human confirmation prompts and audit records.
- Explicit execution rails outside C6W5 with provider and merchant system authority.
