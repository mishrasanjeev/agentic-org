# C6U5 Agentic Commerce Public Discovery State Contract

Status: internal implementation report only. This document does not approve production launch, real merchants, public discovery, checkout/payment, live providers, live Plural, production config, production allowlists, cloud resources, provider calls, merchant private API calls, protocol publication, external submission, certification, compliance, conformance, standardization, public-launch readiness, merchant approval, or production readiness.

## Executive summary

C6U5 defines how AgenticOrg consumes Grantex public discovery state. AgenticOrg remains fail-closed: public buyer discovery stays hidden or refused unless a future separately approved slice proves compatible Grantex and AgenticOrg approval, fresh source evidence, rollback ownership, and safe channel exposure.

This PR does not enable public discovery. It adds a defensive, non-enabling state decision helper and tests that preserve hidden/refused behavior.

## Shared state table

| State | AgenticOrg behavior |
| --- | --- |
| `hidden` | Hide/refuse public discovery. |
| `draft` | Hide/refuse public discovery. |
| `sandbox_review` | Hide/refuse public discovery. |
| `approved_for_sandbox_preview` | Allow only internal preview context; public discovery remains hidden. |
| `blocked` | Hide/refuse public discovery. |
| `rejected` | Hide/refuse public discovery. |
| `expired` | Hide/refuse public discovery. |
| `production_pending` | Hide/refuse public discovery. |
| `future_public_enabled` | Future enum only; still hidden/refused in C6U5. |

## Grantex-owned responsibilities

Grantex owns merchant readiness, discovery review, rollout proposal, source/freshness facts, preview conformance, production allowlist decisions, and audit evidence. AgenticOrg treats Grantex as the only commerce source and does not call providers, Plural, or merchant private APIs.

## AgenticOrg-owned responsibilities

AgenticOrg owns buyer-agent exposure, channel-safe discovery behavior, public metadata hiding, buyer-facing refusal wording, and future channel rollback. AgenticOrg must fail closed when Grantex state is missing, unsupported, stale, expired, blocked, rejected, mismatched, or demo-only.

## Defensive helper behavior

`core/commerce/public_discovery_state.py` returns a buyer-safe decision with:

- Grantex state.
- AgenticOrg state.
- Buyer visibility: `hidden` or `internal_preview`.
- `public_discovery_visible: false`.
- `public_discovery_refusal: true`.
- Required evidence gaps.
- Redacted evidence keys only.

The helper never returns active public discovery in C6U5. Even `future_public_enabled` is treated as a non-active future enum.

## Mismatch, expiry, and rollback

State mismatch refuses public discovery. Missing state refuses public discovery. Unsupported state refuses public discovery. Expired or stale evidence refuses public discovery. Synthetic or demo state refuses public discovery. Rollback from either owner must hide public metadata in all buyer channels before any future public exposure can be considered.

## Buyer-safe wording

Allowed:

- "Public discovery is not enabled for this merchant."
- "This is an internal sandbox preview only."
- "Grantex and AgenticOrg discovery state is not aligned, so discovery remains hidden."

Unsafe:

- "Public discovery is approved."
- "This merchant is production ready."
- "This merchant is certified or compliant."
- "Checkout or payment is enabled."
- "Live provider or live Plural is available."

## Private-only fields

AgenticOrg must not show raw state evidence, real tenant or merchant IDs, private source labels, provider internals, URLs, credentials, tokens, JWTs, passports, DB/Redis URLs, webhook secrets, raw payloads, production config values, concrete allowlists, or private reviewer notes.

## Stop conditions

Stop C6U5 work if it adds public discovery enablement, checkout/payment enablement, live payment, live Plural, provider calls, merchant private API calls, production config, production allowlists, cloud resources, secrets, workflow changes, migrations, protocol publication, external submission, certification, compliance, conformance, standardization, merchant approval, public-launch readiness, or production readiness claims.

## Remaining gaps

- Consent/session/passport revocation propagation.
- Channel-specific refusal packs.
- Order, fulfillment, refund, support, settlement, and payout contracts.
- Sandbox checkout E2E.
- Live provider readiness.
- AgenticOrg CI/CD cloud-build guard follow-up.

## Validation plan

C6U5 validation should run the focused state contract regression, nearby commerce discovery/refusal/session tests, lint/type checks for touched Python files, diff whitespace check, ASCII check, secret/private scan, production config/allowlist scan, public discovery enablement scan, checkout/payment/live-provider scan, direct provider/Plural scan, merchant private API scan, raw connector/private payload scan, and overclaim scan.
