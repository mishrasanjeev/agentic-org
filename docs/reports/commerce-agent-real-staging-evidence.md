# Commerce Sales Agent Real-Staging Evidence

Status: C2D approved real-staging evidence captured locally against the temporary Grantex Option A smoke service. This report is scrubbed and contains no bearer token values, passports/JWTs, idempotency key values, provider credentials, raw payloads, DB/Redis URLs, private keys, or secret values.

- Run mode: `real-staging`
- Grantex host: `grantex-auth-smoke-dd4mtrt2gq-uc.a.run.app`
- Auth source env name: `GRANTEX_API_KEY`
- Fixture env path: `.tmp/commerce-agent-real-staging.env`
- Fixture env variable names recorded: 21
- Fixture synthetic IDs recorded: true
- Fixture sensitive value hashes recorded: true
- Secret values recorded: false
- Raw passports/JWTs recorded: false
- Request correlation values recorded: false
- Provider material recorded: false
- Raw request/response bodies recorded: false
- No direct provider calls: true
- No provider credential handling: true

## Summary

- Passed: 8
- Failed: 3
- Skipped: 4
- Provider: mock
- Grantex-only path confirmed: true

## Case Results

| Case | Status | Tool | HTTP | Latency ms | Error | Blocker |
| --- | --- | --- | --- | --- | --- | --- |
| connector_health_tools_list | pass |  |  |  |  |  |
| merchant_get_profile | pass | grantex_commerce:merchant_get_profile |  |  |  |  |
| catalog_search | pass | grantex_commerce:catalog_search |  |  |  |  |
| catalog_get_item | pass | grantex_commerce:catalog_get_item |  |  |  |  |
| inventory_check | fail | grantex_commerce:inventory_check |  |  | passport_required |  |
| cart_create | pass | grantex_commerce:cart_create |  |  |  |  |
| consent_request | fail | grantex_commerce:consent_request |  |  | validation_failed |  |
| consent_exchange | skipped |  |  |  |  | requires approved synthetic consent fixture |
| payment_create_intent | fail | grantex_commerce:payment_create_intent |  |  | amount_cap_exceeded |  |
| checkout_create | skipped |  |  |  |  | requires checkout passport fixture and payment intent |
| payment_get_status | skipped |  |  |  |  | requires checkout passport fixture and payment intent |
| amount_cap_breach | pass |  |  |  | amount_cap_exceeded |  |
| denied_revoked_expired_passport | pass |  |  |  | consent_denied |  |
| disabled_merchant_untrusted_agent | pass |  |  |  | merchant_disabled |  |
| hosted_agenticorg_discovery | skipped |  |  |  |  | requires hosted AgenticOrg service |

## Synthetic IDs

- Merchant: `mch_staging_electronics_pilot`
- Agent: `cag_staging_agenticorg_sales`
- Product: `cprd_01KRR1DTSAXMY57RRPTDSB6KBZ`
- Variant: `cvar_01KRR1DTSG62ZV9RFWFBHWTZ21`

## Cleanup Status

Cleanup completed after evidence capture.

- Temporary Grantex smoke Cloud Run service, Cloud SQL instance, Redis instance, smoke secrets, and smoke image tag were deleted.
- Temporary smoke resources were verified absent after cleanup.
- Production Grantex resources were verified present after cleanup: `grantex-auth`, `grantex-pg16`, `grantex-redis`.
- Production Commerce V1, live payment, and live Plural flags were not changed by this run.

## Remaining Real-Staging Gaps

- AgenticOrg inventory check did not pass the browse passport through this path and failed safely with `passport_required`.
- AgenticOrg consent request still sends an unsupported requested-scope shape and failed safely with `validation_failed`.
- AgenticOrg payment intent path used a fixture-side amount-cap guard and failed safely with `amount_cap_exceeded`.
- Hosted AgenticOrg discovery remains skipped because this run was local AgenticOrg against a temporary Grantex smoke service only.

## Redaction

The evidence records only host, variable names, synthetic IDs, case status, error code, cleanup status, and provider-safety confirmations. It does not record raw response payloads, usable passports/JWTs, auth material, idempotency key values, provider credentials, DB/Redis URLs, private keys, or secret values.
