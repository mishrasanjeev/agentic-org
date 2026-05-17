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
- Failed: 0
- Skipped: 2

## C2G Consent Exchange Check

- `consent_request` remained a live positive case.
- `consent_exchange` was skipped because a pre-exported checkout passport fixture was present and no granted consent request fixture was provided.
- Stable blocker code recorded: `preexported_checkout_passport_without_granted_consent_fixture`.
- `payment_create_intent`, `checkout_create`, and `payment_get_status` proceeded through the Grantex-only mock-provider path.
- No passport values, consent runtime material, provider credentials, raw payloads, or secret values were recorded.

## Cleanup And Production Safety

- Cleanup completed after evidence capture.
- Deleted temporary Cloud Run service: `grantex-auth-smoke`.
- Deleted temporary Cloud SQL instance: `grantex-commerce-smoke-pg`.
- Deleted temporary Redis instance: `grantex-commerce-smoke-redis`.
- Deleted temporary smoke secrets: `grantex-smoke-*` only.
- Deleted temporary smoke image tag: `auth-service-smoke:a664af2d4cae7c50cf6567205c4986ddb54805a1`.
- Verified temporary smoke Cloud Run, Cloud SQL, Redis, smoke secrets, and smoke image tag absent after cleanup.
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
| consent_exchange | skipped |  |  |  |  | preexported_checkout_passport_without_granted_consent_fixture |
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
      "blocker": "preexported_checkout_passport_without_granted_consent_fixture",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "consent_exchange",
      "status": "skipped",
      "tool_alias": null
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
    "AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID": "cprd_01KRT7Y4ZFHB6FE2Z4PDHP3H3K",
    "AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID": "cvar_01KRT7Y4ZJNS9KQ3NT1HA0AX6D"
  },
  "fixture_value_hashes": [
    {
      "name": "AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT",
      "sha256_12": "4f442828e984"
    },
    {
      "name": "AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT",
      "sha256_12": "ba4d1bea43ef"
    },
    {
      "name": "AGENTICORG_COMMERCE_DENIED_CONSENT_REF",
      "sha256_12": "814d4d266f60"
    },
    {
      "name": "AGENTICORG_COMMERCE_EXPIRED_PASSPORT_JWT",
      "sha256_12": "2302192d135c"
    },
    {
      "name": "AGENTICORG_COMMERCE_REVOKED_PASSPORT_JWT",
      "sha256_12": "4d1db2bc9ee1"
    },
    {
      "name": "GRANTEX_API_KEY",
      "sha256_12": "6e09ee09ccc4"
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
    "grantex_commerce:payment_create_intent",
    "grantex_commerce:checkout_create",
    "grantex_commerce:payment_get_status"
  ]
}
```
