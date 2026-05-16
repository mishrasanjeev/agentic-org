# Commerce Sales Agent Real-Staging Evidence

- Run mode: `real-staging`
- Grantex host: `grantex-auth-smoke-dd4mtrt2gq-uc.a.run.app`
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

## Summary

- Passed: 13
- Failed: 1
- Skipped: 1

## C2F Payment-Intent Check

- Positive `payment_create_intent` passed against the temporary Grantex Option A smoke service.
- The C2F contract fix kept `passport_max_amount_minor_units` as local preflight metadata only.
- The payment intent request used the Grantex-supported MCP field allowlist.
- The amount-cap breach negative case remained a local fail-safe refusal.
- No direct provider calls or provider credential handling were recorded.

## Cleanup And Production Safety

- Cleanup completed after evidence capture.
- Deleted temporary Cloud Run service: `grantex-auth-smoke`.
- Deleted temporary Cloud SQL instance: `grantex-commerce-smoke-pg`.
- Deleted temporary Redis instance: `grantex-commerce-smoke-redis`.
- Deleted temporary smoke secrets: `grantex-smoke-*` only.
- Deleted temporary smoke image tag: `auth-service-smoke:81003bae4ce32b98e847c7f1ab536945079eb96a`.
- Verified temporary smoke Cloud Run, Cloud SQL, Redis, and smoke secrets absent after cleanup.
- Verified production resources still present: `grantex-auth`, `grantex-pg16`, `grantex-redis`.
- Production Commerce V1, live payment, and live Plural flags were not changed by this run.

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
| payment_create_intent | pass | grantex_commerce:payment_create_intent |  |  |  |  |
| checkout_create | pass | grantex_commerce:checkout_create |  |  |  |  |
| payment_get_status | pass | grantex_commerce:payment_get_status |  |  |  |  |
| amount_cap_breach | pass |  |  |  | amount_cap_exceeded |  |
| denied_revoked_expired_passport | pass |  |  |  | consent_denied |  |
| disabled_merchant_untrusted_agent | pass |  |  |  | merchant_disabled |  |
| hosted_agenticorg_discovery | skipped |  |  |  |  | requires hosted AgenticOrg service |

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
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "payment_create_intent",
      "status": "pass",
      "tool_alias": "grantex_commerce:payment_create_intent"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "checkout_create",
      "status": "pass",
      "tool_alias": "grantex_commerce:checkout_create"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "payment_get_status",
      "status": "pass",
      "tool_alias": "grantex_commerce:payment_get_status"
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
    "AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID": "cprd_01KRRW5GHPGK90F3CD7TH1X5C6",
    "AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID": "cvar_01KRRW5GHY3M38WKMDXKD1MHKR"
  },
  "fixture_value_hashes": [
    {
      "name": "AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT",
      "sha256_12": "341767110f12"
    },
    {
      "name": "AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT",
      "sha256_12": "72df306a6570"
    },
    {
      "name": "AGENTICORG_COMMERCE_DENIED_CONSENT_REF",
      "sha256_12": "03deec08c04c"
    },
    {
      "name": "AGENTICORG_COMMERCE_EXPIRED_PASSPORT_JWT",
      "sha256_12": "c09982ea0b33"
    },
    {
      "name": "AGENTICORG_COMMERCE_REVOKED_PASSPORT_JWT",
      "sha256_12": "9c26c71f69f6"
    },
    {
      "name": "GRANTEX_API_KEY",
      "sha256_12": "b98d47d6f189"
    }
  ],
  "grantex_host": "grantex-auth-smoke-dd4mtrt2gq-uc.a.run.app",
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
    "grantex_commerce:payment_create_intent",
    "grantex_commerce:checkout_create",
    "grantex_commerce:payment_get_status"
  ]
}
```
