# C6U6 Consent, Session, and Passport Revocation Propagation

Status: internal implementation report only. This document does not approve production launch, real merchants, public discovery, checkout/payment, live providers, live Plural, production config, production allowlists, cloud resources, provider calls, merchant private API calls, protocol publication, external submission, certification, compliance, conformance, standardization, public-launch readiness, merchant approval, or production readiness.

## Executive Summary

C6U6 adds a buyer-safe AgenticOrg authority summary for Grantex consent, Commerce Passport, buyer session, merchant status, agent status, policy, freshness, and revocation state. The helper is fail-closed: stale, missing, revoked, expired, disabled, mismatched, ambiguous, or unsupported authority refuses protected commerce actions.

This slice does not enable checkout, payment creation, public discovery, live payment, live Plural, provider calls, merchant private API calls, production allowlists, or production launch.

## Grantex Authority Model

Grantex remains authoritative for:
- Consent creation, grant, denial, expiry, and revocation.
- Commerce Passport issue, verify, expiry, scope, tenant, merchant, agent, and revocation checks.
- Merchant enablement and emergency disablement.
- Commerce agent trust and disablement.
- Policy evaluation.
- Audit evidence for authority decisions and refusals.

AgenticOrg treats Grantex as the only commerce authority. AgenticOrg does not call providers, Plural, or merchant private APIs.

## AgenticOrg Session-Consumption Model

AgenticOrg may cache only buyer-safe authority summaries. It must not store or expose raw passports, JWTs, raw consent payloads, provider credentials, raw provider payloads, merchant private payloads, private URLs, DB/Redis URLs, webhook secrets, production config values, or concrete allowlist values.

The C6U6 helper returns:
- `authority_valid`
- `protected_action_allowed: false`
- `checkout_payment_enabled: false`
- `live_provider_enabled: false`
- `public_discovery_enabled: false`
- `refresh_required`
- `refusal_code`
- buyer-safe `reason`
- redacted `evidence_keys`

Even when authority is fresh, C6U6 does not turn that authority into checkout/payment permission.

## Required Authority Fields

Buyer-safe authority summaries should include:
- consent status
- passport status
- buyer session status
- merchant status
- agent status
- policy decision
- authority checked timestamp
- consent/passport/session expiry timestamps
- redacted revocation status
- scoped merchant, agent, buyer, and session references
- audit event and policy decision references

If these fields are missing or ambiguous, AgenticOrg refuses protected actions or requires refresh from Grantex.

## Revocation Propagation

AgenticOrg refuses protected actions when any of these are true:
- Consent is missing, denied, revoked, withdrawn, failed, or expired.
- Passport is missing, revoked, expired, invalid, or not yet valid.
- Buyer session is missing, revoked, expired, disabled, invalid, or stale.
- Merchant is disabled, inactive, blocked, or suspended.
- Agent is disabled, inactive, blocked, suspended, untrusted, or revoked.
- Policy is missing, denied, blocked, rejected, or ambiguous.
- Merchant, agent, buyer, or session references mismatch.
- Authority timestamp is missing or stale.
- Cached state conflicts with a revoked or disabled state.

Cached state never overrides Grantex revocation or disablement.

## Buyer-Safe Refusal Wording

Safe wording:
- "Fresh Grantex consent is required before continuing."
- "The Commerce Passport is revoked and cannot be used."
- "The merchant is not enabled for this commerce action."
- "The commerce agent is not enabled for this action."
- "Authority is stale, so the session must be refreshed."

Unsafe wording:
- "Here is the passport JWT."
- "The provider payload says..."
- "The merchant private endpoint returned..."
- "Checkout/payment is enabled."
- "Live Plural is available."

## Private-Only Fields

Do not expose raw passports, JWTs, tokens, provider credentials, raw consent payloads, raw connector payloads, merchant private URLs, provider internals, DB/Redis URLs, webhook secrets, production config values, concrete allowlists, private reviewer notes, or private incident evidence.

## Audit and Evidence

AgenticOrg may carry only redacted evidence keys. Grantex-owned evidence should remain the audit source for consent, passport verification, revocation, merchant disablement, agent disablement, policy denial, and protected-action refusal.

## Stop Conditions

Stop C6U6 work if it adds public discovery enablement, checkout/payment enablement, live payment, live Plural, provider calls, merchant private API calls, production config, production allowlists, cloud resources, secrets, workflow changes, migrations, protocol publication, external submission, certification, compliance, conformance, standardization, merchant approval, public-launch readiness, or production readiness claims.

## Remaining Gaps

- Channel-specific refusal packs.
- Order, fulfillment, refund, support, settlement, and payout contracts.
- Sandbox checkout E2E.
- Live provider readiness.
- AgenticOrg CI/CD cloud-build guard follow-up.
- Production-grade revocation TTL policy.
- Buyer-facing consent and revocation UX.

## Validation

C6U6 validation should run the focused C6U6 regression, nearby commerce/refusal/session/public-discovery tests, ruff, mypy for touched commerce modules, whitespace diff check, ASCII check, secret/private scan, passport/JWT/raw-token scan, production config/allowlist scan, public discovery enablement scan, checkout/payment/live-provider scan, direct provider/Plural scan, merchant private API scan, raw connector/private payload scan, and overclaim scan.
