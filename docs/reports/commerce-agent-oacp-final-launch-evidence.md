# Commerce Agent OACP Final Launch Evidence

Status: local internal runtime demo complete; production C6Z launch remains blocked by external Shopify credential and Grantex tenant-token provisioning.

Evidence date: 2026-06-18.

## Repository And Deployment State

| Item | Evidence |
| --- | --- |
| AgenticOrg `origin/main` | `2fdccc7ca1337b3b2caa20a2e9ac1d03c7bfbc9c` |
| AgenticOrg API revision | `agenticorg-api-00103-jbk`, 100 percent traffic |
| AgenticOrg UI revision | `agenticorg-ui-00064-kcp`, 100 percent traffic |
| Grantex `origin/main` | `7fd4dd3c865fbd8187d92aec792cff249c9c01c3` |
| Grantex revision | `grantex-auth-00204-zhz`, 100 percent traffic |

## Production Health

- `https://app.agenticorg.ai/api/v1/health`: healthy; commit `2fdccc7ca1337b3b2caa20a2e9ac1d03c7bfbc9c`; DB healthy; Redis healthy.
- `https://app.agenticorg.ai/`: HTTP 200.
- `https://api.grantex.dev/health`: healthy; database ok; Redis ok.

## Production Vertical Result

The required production vertical did not complete.

Completed before blocker:

- Authenticated smoke login succeeded.
- Seller Commerce Agent onboarding packet was created for merchant id `mch_shopify_mgx0n6_22` and seller agent id `seller_oacp_launch_evidence_20260618`.
- Identity fields used:
  - tenant id: redacted; production smoke tenant is managed through Secret Manager and is not printed in this packet.
  - merchant id: `mch_shopify_mgx0n6_22`
  - seller agent id: `seller_oacp_launch_evidence_20260618`
  - buyer agent id requested for the vertical: `buyer_oacp_launch_evidence_20260618`
  - packet id: created by the API, but not captured because the smoke aborted before emitting the final summary; a later safe recapture attempt was blocked by current Secret Manager access.
  - evidence id: not created because Shopify sync failed before connector evidence was produced.

Blocking evidence:

- AgenticOrg Shopify Admin GraphQL read-only sync reached `mgx0n6-22.myshopify.com` and failed with `401 Unauthorized`.
- Direct status-only Shopify GraphQL probe with the mounted AgenticOrg C6Z Shopify token also returned `401`.
- Grantex C6Z authority called with AgenticOrg's configured internal token returned `422 tenant_not_provisioned`.

Follow-up implementation in this PR adds the AgenticOrg-owned merchant connector path that was missing from the original closure run:

- `POST /commerce/runtime/seller-agents/connectors/shopify/credentials` stores a merchant-scoped Shopify credential in tenant-aware encrypted `ConnectorConfig` storage.
- The endpoint accepts either a direct Admin API token or Shopify OAuth code exchange material, validates read-only product access, and returns only redacted status.
- Shopify sync now prefers the encrypted merchant connector config before falling back to legacy process environment variables.
- The Commerce Runtime UI exposes the connector setup and status path without rendering credential values.

This removes the single global Shopify token dependency after merge/deploy, but does not by itself make the production vertical complete. The merchant credential still has to be valid in Shopify, and Grantex tenant-token provisioning must still be fixed.

Because Shopify sync did not produce connector evidence and the configured Grantex token is not mapped, the run did not complete:

- Shopify product count and variant count from a real sync: blocked.
- Grantex authority request through the configured AgenticOrg path: blocked.
- AgenticOrg artifact cache record count: blocked.
- Buyer answer sample from real cached artifacts: blocked.
- MCP production seller facts from real cached artifacts: blocked.
- Plural/Pine capability metadata verification in the same vertical: blocked.

## Local Launch-Closure Evidence

`python scripts/oacp_runtime_launch_check.py` now produces a redacted local
runtime evidence packet for the non-executing vertical:

- generated at: `2026-06-18T06:11:59.907283Z` in the latest local run
- tenant id: `11111111-1111-1111-1111-111111111111`
- merchant id: `merchant_oacp_launch_evidence`
- seller agent id: `seller_agent_oacp_launch`
- packet id: `c6z_onboarding_4dc2161a8d4767ea3bc297f5`
- evidence id: `c6z_shopify_evidence_04f7708266931ba7bacf98b3`
- Shopify product count: `1`
- Shopify variant count: `1`
- Shopify sync timestamp: `2026-06-18T06:11:59.907283Z`
- Grantex authority request status: `local_contract_fixture_ready`
- artifact family count: `11`
- artifact verifier summary: `11` valid, `0` invalid
- AgenticOrg cache records count: `11`
- buyer answer sample: `OACP Launch Sample Tote: price snapshot 1299.00 INR; inventory snapshot 4; final price requires merchant/source confirmation.`
- buyer source label: `Source: Shopify via Grantex artifact`
- buyer freshness label: `Freshness: synced 0s ago`
- bridge result: OpenAPI bridge contract with `11` artifact refs; MCP smoke covered by `npm --prefix mcp-server test`
- Plural/Pine capability status: `blocked_missing_credentials`
- Plural/Pine redacted evidence ref: `provider:plural_pine:capability:missing-env:redacted`
- public discovery flag: `false`
- `allowed_to_execute=false`
- `raw_payload_stored=false`
- `no_payment_execution=true`
- `non_authoritative_for_transaction=true`

The artifact IDs are local fixture IDs only. They use the
`local-c6z:<family>:<tenant>:<merchant>:<seller>` shape and cover:
`merchant_profile`, `seller_agent_card`, `connector_evidence`,
`catalog_snapshot`, `offer_price_snapshot`, `inventory_snapshot`,
`policy_scope`, `public_discovery_state`, `mandate_capability`,
`protocol_adapter`, and `authority_request_status`.

## Grantex Issuer Isolation Proof

Grantex C6Z authority was also tested with a platform-admin operator token to isolate the route:

- status `artifact_issuance_ready`
- route kind `grantex_internal_c6z_authority_request`
- artifact count `11` after this branch
- artifact families: `merchant_profile`, `seller_agent_card`, `connector_evidence`, `catalog_snapshot`, `offer_price_snapshot`, `inventory_snapshot`, `policy_scope`, `public_discovery_state`, `mandate_capability`, `protocol_adapter`, `authority_request_status`
- verifier summary expected after this branch: 11 valid, 0 invalid
- `allowed_to_execute=false`
- `no_payment_execution=true`
- `no_public_discovery_enablement=true`
- `non_authoritative_for_transaction=true`

This proves the Grantex issuer route works, but not the configured AgenticOrg-to-Grantex production path.

## MCP And Local Validation

- `npm --prefix mcp-server test`: passed; MCP build plus smoke, 4 backend calls.
- `python -m pytest tests/unit/test_oacp_c6z_runtime_vertical.py --no-cov`: covers onboarding, Shopify credential custody, read-only sync preference, authority handoff payload, cache intake, buyer answer, bridge contract, MCP tools, Plural/Pine capability verifier, and runtime migration.
- `python scripts/oacp_runtime_launch_check.py`: local-safe evidence harness for the runtime contract; external Shopify/Grantex checks require `OACP_LAUNCH_EXTERNAL_CHECKS=true` and configured env.
- `python -m pytest tests/integration/test_c6z_external_integrations.py --no-cov`: skipped 2 tests because local Shopify and Plural/Pine env vars are absent.
- `python -m ruff check core/commerce/c6z_runtime_vertical.py api/v1/commerce_runtime.py tests/unit/test_oacp_c6z_runtime_vertical.py tests/integration/test_c6z_external_integrations.py`: passed.
- `python -m mypy core/commerce/c6z_runtime_vertical.py api/v1/commerce_runtime.py`: passed.

## Safety

- AgenticOrg public discovery flag: `false`.
- Grantex public discovery flag: `false`.
- `allowed_to_execute=false`.
- `raw_payload_stored=false` for C6Z code paths; no raw Shopify payload was persisted by the failed sync.
- No checkout, payment, order, mandate, refund, return, shipment, inventory hold, provider execution, public discovery publication, or OACP external publication was performed.
- No secrets, bearer tokens, raw Shopify payloads, raw provider payloads, raw Grantex artifacts, JWTs, passports, DB URLs, Redis URLs, or Secret Manager values are included in this evidence packet.

## Exact Launch Status

- Internal runtime demo: complete locally in this branch; blocked in production until valid Shopify and Grantex mapped credentials are available and the full vertical is rerun.
- Closed merchant pilot: blocked.
- Public OACP preview: blocked.

## Required Next Actions

1. AgenticOrg owner: rotate or replace `agenticorg-c6z-shopify-admin-access-token`, then verify a status-only Shopify GraphQL query returns 200.
2. Grantex owner: provision the commerce tenant/developer mapping for the AgenticOrg internal token or replace it with an approved mapped credential.
3. Re-run the production vertical and update this evidence packet only after Shopify sync, Grantex issuance, AgenticOrg cache, buyer answer, MCP seller facts, and Plural/Pine capability metadata all complete without execution.
