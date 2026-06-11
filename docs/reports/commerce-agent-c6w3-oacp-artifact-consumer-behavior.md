# Commerce Agent C6W3 - OACP Artifact Consumer Behavior

Status: implementation foundation, internal-only, non-enabling.

## Scope

C6W3 adds local AgenticOrg artifact-family validation for Grantex-issued OACP artifacts.

This slice adds:

- Runtime schema descriptors for every OACP artifact family.
- Synthetic public-safe fixtures matching the Grantex C6W3 family posture.
- Local validation for envelope fields, safety fields, required payload fields, payload hash, detached JWS metadata, TTL, scope, and family-specific rules.
- Runtime checks that prevent seller cards, mandate references, public discovery artifacts, and protocol adapter artifacts from becoming unlimited commerce authority.

AgenticOrg does not invent commerce facts. It consumes Grantex-signed canonical artifacts, verifies local cache and freshness posture, and refuses unsupported or unsafe actions.

No endpoint, migration, workflow, provider adapter, public discovery, checkout/payment, live provider, merchant private API, allowlist, cloud, deploy, or protocol publication behavior is added.

## Buyer-Agent Handling

Buyer agents may use valid unexpired artifacts for:

- Merchant discovery.
- Product browsing.
- Product comparison.
- Non-binding recommendation.
- Draft cart or quote preview when clearly non-final.
- Policy explanation.
- Source and freshness display.

Buyer agents must refuse when:

- Required artifact types are missing for a final commitment.
- Any artifact is expired, stale, revoked, out of scope, missing all-four cache dimensions when required, or unverifiable.
- Payloads include private, raw, credential, provider, connector, allowlist, or enablement fields.
- Public discovery publish or unpublish is attempted offline.
- A mandate capability is used for payment intent without direct provider verification.
- A seller card or protocol adapter is treated as final commerce authority.

## Seller-Agent Handling

Seller agents may use valid artifacts to:

- Explain approved merchant capability.
- Explain Grantex blockers.
- Prepare public-safe remediation tasks.
- Show source and freshness status.
- Render public-safe seller-card metadata.

Seller agents must not:

- Self-approve public discovery.
- Treat connector access as publication approval.
- Expose credentials, raw connector output, raw provider payloads, private merchant API values, or production allowlists.
- Treat a seller card as merchant approval, payment approval, or unlimited authority.
- Start payment capture, refund execution, settlement, payout, fulfillment, or merchant approval from a commitment-evidence artifact.

## Channel-Safe Refusals

Use short refusal wording in compact channels:

| Condition | Refusal wording |
| --- | --- |
| Missing artifact | I need verified commerce data before I can continue. |
| Stale artifact | Data is stale. I can browse, not commit. |
| Revoked artifact | This merchant or offer changed. I need fresh verification. |
| Scope mismatch | This artifact is not valid for this buyer, seller, or merchant. |
| Mandate verification missing | Payment permission must be checked with the provider before purchase. |
| Public discovery change | Public discovery changes require online authority. |
| Seller card overuse | A seller card can describe capabilities, not approve a transaction. |
| Protocol adapter overuse | Adapter metadata can route or display facts, not approve commerce. |

## Cache And Freshness

AgenticOrg cache behavior remains local and fail-closed:

- Cache keys include tenant, merchant, seller agent, buyer agent, artifact type, artifact ID, schema version, and policy version when all-four scope is required.
- Cache retrieval re-runs validation with current time, issuer key metadata, revocation snapshot, and expected scope.
- Protocol adapter artifacts cannot outlive referenced artifact expiries.
- Mandate capability artifacts remain non-sensitive evidence and require direct provider verification at payment-intent time.
- Public discovery artifacts are read/display only and cannot publish or unpublish offline.
- Final commitments require the full set of required artifacts for the action, plus C6W1 Offline Commitment Mode constraints when Grantex is unavailable.

## What This Does Not Enable

C6W3 does not enable:

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

## Stop Conditions

Stop later implementation if:

- Buyer agents receive connector credentials or merchant private API access.
- AgenticOrg treats cached artifacts as unlimited authority.
- Seller cards are used as payment, merchant, checkout, or public discovery approval.
- Protocol adapters are used as external publication or approval artifacts.
- Public discovery, checkout/payment, live provider, live Plural, production allowlists, or external protocol publication are enabled before approved gates.
- Caps, TTLs, revocation windows, or direct-provider verification rules are weakened without explicit approval.
