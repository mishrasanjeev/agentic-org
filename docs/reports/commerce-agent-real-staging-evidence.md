# Commerce Sales Agent Real-Staging Evidence

- Run mode: `real-staging`
- Grantex host: `grantex-auth-smoke-dd4mtrt2gq-uc.a.run.app`
- Auth source env name: `GRANTEX_API_KEY`
- Secret values recorded: false
- Raw passports/JWTs recorded: false
- Request correlation values recorded: false
- Provider material recorded: false
- Raw request/response bodies recorded: false
- C2B result: 2 passed, 2 failed-safe, 10 skipped
- Grantex-only path confirmed: true
- No provider credential handling: true
- C2C blocker: synthetic consent/passport fixture support is needed before checkout and payment coverage can run through AgenticOrg real-staging.

## Case Results

| Case | Status | Tool | HTTP | Latency ms | Error | Blocker |
| --- | --- | --- | --- | --- | --- | --- |
| connector_health_tools_list | pass |  |  |  |  |  |
| merchant_get_profile | pass | grantex_commerce:merchant_get_profile |  |  |  |  |
| catalog_search | failed-safe | grantex_commerce:catalog_search |  |  | passport_required | Grantex refused without synthetic passport fixture |
| catalog_get_item | skipped |  |  |  |  | catalog search returned no product |
| inventory_check | skipped |  |  |  |  | no variant ID available |
| cart_create | skipped |  |  |  |  | no variant ID available |
| consent_request | failed-safe | grantex_commerce:consent_request |  |  | validation_failed | Grantex refused invalid/incomplete synthetic consent fixture |
| consent_exchange | skipped |  |  |  |  | requires approved synthetic staging fixture or hosted AgenticOrg service |
| payment_create_intent | skipped |  |  |  |  | requires approved synthetic staging fixture or hosted AgenticOrg service |
| checkout_create | skipped |  |  |  |  | requires approved synthetic staging fixture or hosted AgenticOrg service |
| payment_get_status | skipped |  |  |  |  | requires approved synthetic staging fixture or hosted AgenticOrg service |
| denied_revoked_expired_passport | skipped |  |  |  |  | requires approved synthetic staging fixture or hosted AgenticOrg service |
| disabled_merchant_untrusted_agent | skipped |  |  |  |  | requires approved synthetic staging fixture or hosted AgenticOrg service |
| hosted_agenticorg_discovery | skipped |  |  |  |  | requires approved synthetic staging fixture or hosted AgenticOrg service |

## Redacted Summary

```json
{
  "auth_source_env_name": "GRANTEX_API_KEY",
  "c2b_result": {
    "failed_safe": 2,
    "passed": 2,
    "skipped": 10
  },
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
      "error_code": "passport_required",
      "http_status": null,
      "latency_ms": null,
      "name": "catalog_search",
      "status": "failed-safe",
      "tool_alias": "grantex_commerce:catalog_search"
    },
    {
      "blocker": "catalog search returned no product",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "catalog_get_item",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": "no variant ID available",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "inventory_check",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": "no variant ID available",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "cart_create",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": null,
      "error_code": "validation_failed",
      "http_status": null,
      "latency_ms": null,
      "name": "consent_request",
      "status": "failed-safe",
      "tool_alias": "grantex_commerce:consent_request"
    },
    {
      "blocker": "requires approved synthetic staging fixture or hosted AgenticOrg service",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "consent_exchange",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": "requires approved synthetic staging fixture or hosted AgenticOrg service",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "payment_create_intent",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": "requires approved synthetic staging fixture or hosted AgenticOrg service",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "checkout_create",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": "requires approved synthetic staging fixture or hosted AgenticOrg service",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "payment_get_status",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": "requires approved synthetic staging fixture or hosted AgenticOrg service",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "denied_revoked_expired_passport",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": "requires approved synthetic staging fixture or hosted AgenticOrg service",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "disabled_merchant_untrusted_agent",
      "status": "skipped",
      "tool_alias": null
    },
    {
      "blocker": "requires approved synthetic staging fixture or hosted AgenticOrg service",
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "hosted_agenticorg_discovery",
      "status": "skipped",
      "tool_alias": null
    }
  ],
  "grantex_host": "grantex-auth-smoke-dd4mtrt2gq-uc.a.run.app",
  "grantex_only_path_confirmed": true,
  "no_provider_call_confirmation": true,
  "no_provider_credential_handling": true,
  "redaction": {
    "auth_values_recorded": false,
    "bearer_tokens_recorded": false,
    "idempotency_keys_recorded": false,
    "passport_or_jwt_values_recorded": false,
    "provider_material_recorded": false,
    "raw_request_response_bodies_recorded": false,
    "secret_values_recorded": false
  },
  "run_mode": "real-staging",
  "synthetic_consent_passport_fixture_support_needed_for_c2c": true,
  "tool_sequence": [
    "grantex_commerce:merchant_get_profile",
    "grantex_commerce:catalog_search",
    "grantex_commerce:consent_request"
  ]
}
```
