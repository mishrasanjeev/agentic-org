# AgenticOrg C3 Hosted Commerce Smoke Evidence

- Run mode: `run`
- AgenticOrg host: `agenticorg-api-commerce-smoke-dd4mtrt2gq-uc.a.run.app`
- Grantex host: `grantex-auth-smoke-dd4mtrt2gq-uc.a.run.app`
- Auth source env name: `GRANTEX_API_KEY`
- Fixture source: `local_tmp_fixture_env`
- Fixture binding name: ``
- Secret values recorded: false
- Raw passports/JWTs recorded: false
- Idempotency values recorded: false
- Provider material recorded: false
- Raw request/response bodies recorded: false
- DB/Redis URLs recorded: false

## Case Results

| Case | Status | HTTP | Latency ms | Error | Blocker |
| --- | --- | --- | --- | --- | --- |
| liveness | pass | 200 | 316 |  |  |
| liveness_status_alive | pass |  |  |  |  |
| health | pass | 200 | 333 |  |  |
| health_status_healthy | pass |  |  |  |  |
| mcp_tools | pass | 200 | 356 |  |  |
| mcp_commerce_sales_agent_discovery | pass |  |  |  |  |
| a2a_agent_card | pass | 200 | 319 |  |  |
| a2a_card_uses_agenticorg_smoke_origin | pass |  |  |  |  |
| a2a_card_uses_grantex_smoke_issuer | pass |  |  |  |  |
| a2a_card_uses_grantex_smoke_jwks | pass |  |  |  |  |
| a2a_agents | pass | 200 | 327 |  |  |
| a2a_commerce_sales_agent_discovery | pass |  |  |  |  |
| a2a_commerce_tools_grantex_only | pass |  |  |  |  |
| consent_exchange_expected_skip_evidence | pass |  |  |  |  |

## Validation, Refusals, And Cleanup

- Hosted C3 result: 14 passed, 0 failed.
- Production AgenticOrg URL refusal: passed.
- Production Grantex URL refusal: passed.
- Arbitrary run.app refusal without exact allowlist: passed.
- HTTP localhost/non-HTTPS refusal: passed.
- Cleanup completed: temporary AgenticOrg smoke Cloud Run service, smoke migration job, Cloud SQL, Redis, Secret Manager secrets, and smoke image tag were deleted.
- Production untouched: no production AgenticOrg or Grantex services, config, secrets, DB/Redis resources, Commerce V1 flags, live payments, or live Plural settings were changed.
- Provider path: no direct Stripe, Plural, Pinecone, or provider credential handling was used or added.

## Migration Coverage Caveat

- Full AgenticOrg migration coverage remains blocked by the low-cost smoke DB baseline/pgvector issue.
- API-only hosted discovery passed after a temporary Alembic-head stamp in the smoke DB.
- This evidence is sufficient for hosted discovery readiness only; it is not full runtime or migration certification.

## Redacted Summary

```json
{
  "agenticorg_host": "agenticorg-api-commerce-smoke-dd4mtrt2gq-uc.a.run.app",
  "auth_source_env_name": "GRANTEX_API_KEY",
  "cases": [
    {
      "blocker": null,
      "error_code": null,
      "http_status": 200,
      "latency_ms": 316,
      "name": "liveness",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "liveness_status_alive",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": 200,
      "latency_ms": 333,
      "name": "health",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "health_status_healthy",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": 200,
      "latency_ms": 356,
      "name": "mcp_tools",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "mcp_commerce_sales_agent_discovery",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": 200,
      "latency_ms": 319,
      "name": "a2a_agent_card",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "a2a_card_uses_agenticorg_smoke_origin",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "a2a_card_uses_grantex_smoke_issuer",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "a2a_card_uses_grantex_smoke_jwks",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": 200,
      "latency_ms": 327,
      "name": "a2a_agents",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "a2a_commerce_sales_agent_discovery",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "a2a_commerce_tools_grantex_only",
      "status": "pass"
    },
    {
      "blocker": null,
      "error_code": null,
      "http_status": null,
      "latency_ms": null,
      "name": "consent_exchange_expected_skip_evidence",
      "status": "pass"
    }
  ],
  "cleanup_by": "2026-05-17T23:59:00+05:30",
  "commit_sha": "09960c1d2351e03b11c81e02b3d9ef4512c9fec0",
  "fixture": {
    "env_var_names": [
      "AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL",
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
      "GRANTEX_API_KEY",
      "GRANTEX_BASE_URL",
      "GRANTEX_COMMERCE_BASE_URL"
    ],
    "fixture_binding_name": null,
    "sensitive_value_hashes": [
      {
        "name": "GRANTEX_API_KEY",
        "sha256_12": "e9a4be004099"
      }
    ],
    "source": "local_tmp_fixture_env",
    "synthetic_ids": {
      "AGENTICORG_COMMERCE_FIXTURE_AGENT_ID": "cag_staging_agenticorg_sales",
      "AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID": "mch_staging_electronics_pilot",
      "AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID": "cprd_01KRTGDBAB09RB0CZK9C1H7P53",
      "AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID": "cvar_01KRTGDBAVSD5MZNSZYYJXQX1P"
    }
  },
  "grantex_host": "grantex-auth-smoke-dd4mtrt2gq-uc.a.run.app",
  "image_tag": "us-central1-docker.pkg.dev/grantex-prod/grantex-images/agenticorg-api-commerce-smoke:09960c1d2351",
  "no_provider_call_confirmation": true,
  "public_env_var_names": [
    "AGENTICORG_ENV",
    "AGENTICORG_BASE_URL",
    "AGENTICORG_PUBLIC_API_BASE_URL",
    "AGENTICORG_CORS_ALLOWED_ORIGINS",
    "AGENTICORG_GIT_SHA",
    "AGENTICORG_ENABLE_LEGACY_STARTUP_DDL",
    "AGENTICORG_COMMERCE_REAL_STAGING",
    "GRANTEX_COMMERCE_BASE_URL",
    "GRANTEX_BASE_URL",
    "AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL",
    "COMMERCE_LIVE_MODE_ENABLED",
    "PLURAL_LIVE_ENABLED",
    "PLURAL_ENV"
  ],
  "redaction": {
    "bearer_tokens_recorded": false,
    "db_redis_urls_recorded": false,
    "idempotency_values_recorded": false,
    "passport_values_recorded": false,
    "private_keys_recorded": false,
    "provider_material_recorded": false,
    "raw_payloads_recorded": false,
    "secret_values_recorded": false
  },
  "report_type": "agenticorg-c3-hosted-commerce-smoke",
  "resources": {
    "agenticorg_service_name": "agenticorg-api-commerce-smoke",
    "database_resource_name": "agenticorg-commerce-smoke-pg",
    "eval_job_name": "agenticorg-commerce-smoke-eval",
    "migrate_job_name": "agenticorg-commerce-smoke-migrate",
    "redis_resource_name": "agenticorg-commerce-smoke-redis"
  },
  "run_mode": "run",
  "smoke_binding_names": [
    "agenticorg-commerce-smoke-secret-key",
    "agenticorg-commerce-smoke-db-url",
    "agenticorg-commerce-smoke-redis-url",
    "agenticorg-commerce-smoke-grantex-api-key"
  ]
}
```
