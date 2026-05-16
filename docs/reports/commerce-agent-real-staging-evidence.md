# Commerce Sales Agent Real-Staging Evidence

Status: C2E approved real-staging evidence captured locally against the temporary Grantex Option A smoke service. This report is scrubbed and contains no bearer token values, passports/JWTs, idempotency key values, provider credentials, raw payloads, DB/Redis URLs, private keys, or secret values.

- Run mode: `real-staging`
- Grantex host: `grantex-auth-smoke-876335597959.us-central1.run.app`
- Auth source env name: `GRANTEX_API_KEY`
- Fixture env path: `.tmp/commerce-agent-real-staging.env`
- Fixture env variable names recorded: 21
- Fixture synthetic IDs recorded: True
- Fixture sensitive value hashes recorded: True
- Secret values recorded: false
- Raw passports/JWTs recorded: false
- Request correlation values recorded: false
- Provider material recorded: false
- Raw request/response bodies recorded: false
- No direct provider calls: true
- No provider credential handling: true

## Summary

- Passed: 10
- Failed: 2
- Skipped: 3
- Provider: mock
- Grantex-only path confirmed: true

## Case Results

| Case | Status | Tool | HTTP | Latency ms | Error | Blocker |
| --- | --- | --- | --- | --- | --- | --- |
| connector_health_tools_list | pass |  |  |  |  |  |
| merchant_get_profile | pass | grantex_commerce:merchant_get_profile |  |  |  |  |
| catalog_search | pass | grantex_commerce:catalog_search |  |  |  |  |
| catalog_get_item | pass | grantex_commerce:catalog_get_item |  |  |  |  |
| inventory_check | pass | grantex_commerce:inventory_check |  |  |  |  |
| cart_create | pass | grantex_commerce:cart_create |  |  |  |  |
| consent_request | pass | grantex_commerce:consent_request |  |  |  |  |
| consent_exchange | fail | grantex_commerce:consent_exchange |  |  | consent_not_granted |  |
| payment_create_intent | fail | grantex_commerce:payment_create_intent |  |  | validation_failed |  |
| checkout_create | skipped |  |  |  |  | requires checkout passport fixture and payment intent |
| payment_get_status | skipped |  |  |  |  | requires checkout passport fixture and payment intent |
| amount_cap_breach | pass |  |  |  | amount_cap_exceeded |  |
| denied_revoked_expired_passport | pass |  |  |  | consent_denied |  |
| disabled_merchant_untrusted_agent | pass |  |  |  | merchant_disabled |  |
| hosted_agenticorg_discovery | skipped |  |  |  |  | requires hosted AgenticOrg service |

## Synthetic IDs

- Merchant: `mch_staging_electronics_pilot`
- Agent: `cag_staging_agenticorg_sales`
- Product: `cprd_01KRRN1K5HSQV7MGEDP8PD54Z2`
- Variant: `cvar_01KRRN1K5TB1MQ10GJJB91H2YT`

## C2E Expectations

- Inventory used the browse passport fixture when present and passed.
- Consent request used Grantex-supported checkout scopes.
- Positive payment amount was within the passport cap, but Grantex returned `validation_failed` for AgenticOrg payment intent creation.
- Amount-cap breach remained a fail-safe negative case with `amount_cap_exceeded`.
- Commerce execution stayed on the Grantex connector path; no provider credentials or direct provider calls were used.

## Cleanup Status

Temporary smoke resources were cleaned up after evidence capture. Production Grantex resources were verified present after cleanup, and production Commerce V1 config, live payment flags, and live Plural flags were not changed.

## Redaction

The evidence records only host, variable names, synthetic IDs, case status, error code, cleanup status, and provider-safety confirmations. It does not record raw response payloads, usable passports/JWTs, auth material, idempotency key values, provider credentials, DB/Redis URLs, private keys, or secret values.

## Redacted Summary

```json
{
  "auth_source_env_name": "GRANTEX_API_KEY",
  "cases": [
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "connector_health_tools_list",
      "status": "pass",
      "tool_alias": null
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "merchant_get_profile",
      "status": "pass",
      "tool_alias": "grantex_commerce:merchant_get_profile"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "catalog_search",
      "status": "pass",
      "tool_alias": "grantex_commerce:catalog_search"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "catalog_get_item",
      "status": "pass",
      "tool_alias": "grantex_commerce:catalog_get_item"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "inventory_check",
      "status": "pass",
      "tool_alias": "grantex_commerce:inventory_check"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "cart_create",
      "status": "pass",
      "tool_alias": "grantex_commerce:cart_create"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "consent_request",
      "status": "pass",
      "tool_alias": "grantex_commerce:consent_request"
    },
    {
      "blocker": null,
      "error_code": "consent_not_granted",
      "http_status": null,
      "latency_ms": null,
      "name": "consent_exchange",
      "status": "fail",
      "tool_alias": "grantex_commerce:consent_exchange"
    },
    {
      "blocker": null,
      "error_code": "validation_failed",
      "http_status": null,
      "latency_ms": null,
      "name": "payment_create_intent",
      "status": "fail",
      "tool_alias": "grantex_commerce:payment_create_intent"
    },
    {
      "blocker": "requires checkout passport fixture and payment intent",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "checkout_create",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": "requires checkout passport fixture and payment intent",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "payment_get_status",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": null,
      "error_code": "amount_cap_exceeded",
      "http_status": null,
      "latency_ms": null,
      "name": "amount_cap_breach",
      "status": "pass",
      "tool_alias": null
    },
    {
      "blocker": null,
      "error_code": "consent_denied",
      "http_status": null,
      "latency_ms": null,
      "name": "denied_revoked_expired_passport",
      "status": "pass",
      "tool_alias": null
    },
    {
      "blocker": null,
      "error_code": "merchant_disabled",
      "http_status": null,
      "latency_ms": null,
      "name": "disabled_merchant_untrusted_agent",
      "status": "pass",
      "tool_alias": null
    },
    {
      "blocker": "requires hosted AgenticOrg service",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "hosted_agenticorg_discovery",
      "status": "skipped",
      "tool_alias": null
    }
  ],
  "fixture_env_path": ".tmp/commerce-agent-real-staging.env",
  "fixture_env_var_names": [
    "AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL",
    "AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT",
    "AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT",
    "AGENTICORG_COMMERCE_DENIED_CONSENT_REF",
    "AGENTICORG_COMMERCE_EXPIRED_PASSPORT_JWT",
    "AGENTICORG_COMMERCE_FIXTURE_AGENT_ID",
    "AGENTICORG_COMMERCE_FIXTURE_AMOUNT_MINOR_UNITS",
    "AGENTICORG_COMMERCE_FIXTURE_AUTH_ENV_NAME",
    "AGENTICORG_COMMERCE_FIXTURE_CURRENCY",
    "AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID",
    "AGENTICORG_COMMERCE_FIXTURE_PASSPORT_MAX_AMOUNT_MINOR_UNITS",
    "AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID",
    "AGENTICORG_COMMERCE_FIXTURE_PROVIDER",
    "AGENTICORG_COMMERCE_FIXTURE_SYNTHETIC_ONLY",
    "AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID",
    "AGENTICORG_COMMERCE_FIXTURE_VERSION",
    "AGENTICORG_COMMERCE_REAL_STAGING",
    "AGENTICORG_COMMERCE_REVOKED_PASSPORT_JWT",
    "GRANTEX_API_KEY",
    "GRANTEX_BASE_URL",
    "GRANTEX_COMMERCE_BASE_URL"
  ],
  "fixture_synthetic_ids": {
    "AGENTICORG_COMMERCE_FIXTURE_AGENT_ID": "cag_staging_agenticorg_sales",
    "AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID": "mch_staging_electronics_pilot",
    "AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID": "cprd_01KRRN1K5HSQV7MGEDP8PD54Z2",
    "AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID": "cvar_01KRRN1K5TB1MQ10GJJB91H2YT"
  },
  "fixture_value_hashes": [
    {
      "name": "AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT",
      "sha256_12": "a7ba849a221c"
    },
    {
      "name": "AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT",
      "sha256_12": "3b7c61c9fbf3"
    },
    {
      "name": "AGENTICORG_COMMERCE_DENIED_CONSENT_REF",
      "sha256_12": "198f071592a6"
    },
    {
      "name": "AGENTICORG_COMMERCE_EXPIRED_PASSPORT_JWT",
      "sha256_12": "866ab9c5589c"
    },
    {
      "name": "AGENTICORG_COMMERCE_REVOKED_PASSPORT_JWT",
      "sha256_12": "1fe9fcebc894"
    },
    {
      "name": "GRANTEX_API_KEY",
      "sha256_12": "9450cfa462a1"
    }
  ],
  "grantex_host": "grantex-auth-smoke-876335597959.us-central1.run.app",
  "no_provider_call_confirmation": true,
  "redaction": {
    "auth_values_recorded": false,
    "idempotency_values_recorded": "[redacted]",
    "passport_values_recorded": "[redacted]",
    "provider_material_recorded": false,
    "raw_payloads_recorded": "[redacted]"
  },
  "run_mode": "real-staging",
  "tool_sequence": [
    "grantex_commerce:merchant_get_profile",
    "grantex_commerce:catalog_search",
    "grantex_commerce:catalog_get_item",
    "grantex_commerce:inventory_check",
    "grantex_commerce:cart_create",
    "grantex_commerce:consent_request",
    "grantex_commerce:consent_exchange",
    "grantex_commerce:payment_create_intent"
  ]
}
```
