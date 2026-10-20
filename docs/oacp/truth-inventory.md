# OACP Runtime Truth Inventory

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md). Launch closure source of truth: [OACP Runtime Launch Closure PRD](runtime-launch-closure-prd.md).

This inventory is based on `api/v1/commerce_runtime.py`, `core/commerce/c6z_runtime_vertical.py`, `core/commerce/oacp_artifacts.py`, `core/commerce/oacp_merchant_config.py`, the AgenticOrg Commerce Runtime UI, and OACP unit tests.

| Category | AgenticOrg reality | Owner/action |
| --- | --- | --- |
| Implemented runtime | Tenant/merchant/seller scoped commerce config UI/API, Seller onboarding packets, encrypted merchant-scoped Shopify credential setup, read-only Shopify sync, Shopify webhook HMAC checks, Grantex C6Z authority request, artifact cache intake and Grantex replay refresh that verify each artifact (ES256 detached JWS against the pinned Grantex JWKS, scope, TTL, Token Status List) and, in strict runtimes, refuses the whole issuance on any failure, buyer Q&A from cache, public catalog publishing gate, web/MCP/OpenAPI/A2A/search/WhatsApp/Telegram bridge routes, protocol adapter payloads, Plural/Pine capability verifier, bank/provider pending-adapter config, purchase preparation blockers, the commerce arbiter (deterministic allow/refuse decisions sealed to the ledger, single-use `decision_id` on purchase and POS handoff), the commerce ledger (signed, chained `audit_log` rows for every commerce write, enforced `audit_event`), Offline POS Bridge handoff packets, POS confirmation intake, local POS simulator, and reconciliation status. | AgenticOrg keeps runtime tests, UI tests, route inventory, and docs aligned. |
| Implemented docs only | Older Commerce Sales Agent planning reports and launch plans. | Link to this docs set when they discuss the current OACP split. |
| Implemented but not broad launch | Channel routes, provider capability checks, and real POS provider callbacks require secrets and partner approvals before public use. | Operators must configure channel/provider/POS env and run smoke tests. |
| Requires external credentials/approval | Shopify access, Grantex service token and tenant allowlist, WhatsApp/Telegram webhook secrets, Plural/Pine credentials, POS provider approval, merchant and provider approval. | Runtime owner collects evidence before launch. |
| Missing | Broad order/payment/mandate/POS execution, universal buyer-surface rollout, live non-Shopify adapters, live bank/custom provider adapters, external public protocol approval, and public launch approvals. | Track as pending runtime/product gaps. |
| Depends on a Grantex release | Grantex issues artifacts under the ES256 detached-JWS profile only after its OACP integrity release is deployed. Until then every Grantex artifact carries the legacy template signature, and strict runtimes refuse to cache them (`AGENTICORG_OACP_VERIFY_STRICT=false` is the opt-out, logging each refusal as an irregularity). The Grantex Python SDK verifier replaces the local verifier once that SDK release is published. | Deploy the Grantex release before relying on strict caching; track the SDK switch. |
| Stale/confusing docs | Older text that says AgenticOrg only talks to Grantex Commerce V1 tools or implies Grantex owns merchant runtime. | Supersede with this OACP runtime docs set. |

## Evidence Pointers

- API routes: `api/v1/commerce_runtime.py`
- Runtime helpers: `core/commerce/c6z_runtime_vertical.py`
- Offline POS bridge helpers: `core/commerce/offline_pos_bridge.py`
- Merchant config helpers: `core/commerce/oacp_merchant_config.py`
- Artifact verification: `core/commerce/oacp_jws.py` (ES256 detached JWS, status-list token), `verify_oacp_artifact` in `core/commerce/oacp_artifacts.py`, `core/commerce/oacp_grantex_trust.py` (JWKS and status-list fetch and caching)
- Artifact cache records: `core/commerce/oacp_artifacts.py`
- Commerce ledger and arbiter: `core/commerce/ledger.py`, `core/commerce/arbiter.py`, `core/commerce/arbiter_store.py`, migration `v6_z88_commerce_ledger.py` ([arbiter and ledger](arbiter-ledger.md))
- Mandates: `core/commerce/mandates_client.py` (requests), `core/commerce/oacp_mandates.py` (local verifier), vectors `tests/fixtures/oacp/oacp-mandates-v1.json` and `oacp-mandates-v1-extra.json`, `tests/unit/test_oacp_mandates_vectors.py`, `tests/integration/test_ucp_mandates_postgres.py`, live `tests/integration/test_grantex_mandates_live.py`
- UI demo: `ui/src/pages/CommerceRuntimeDemo.tsx`
- UI tests: `ui/src/__tests__/CommerceRuntimeDemo.test.tsx`
- Unit tests: `tests/unit/test_oacp_c6z_runtime_vertical.py`, `tests/unit/test_oacp_artifact_jws_profile.py` (shared vectors)
- Regression tests: `tests/regression/test_oacp_cache_verifies_grantex_artifacts_20261019.py`, `tests/regression/test_commerce_ledger_20261020.py`, `tests/unit/test_commerce_arbiter.py`
- Postgres proofs: `tests/integration/test_commerce_arbiter_ledger_postgres.py`, `tests/integration/test_rls_nobypass_commerce_ledger.py`
- Conformance against a running Grantex: `tests/integration/test_oacp_grantex_conformance.py`

## Pending Runtime Gaps

| Gap | Owner | Action |
| --- | --- | --- |
| Provider or bank rail execution | Pine Labs Plural/P3P, bank/fintech/custom providers + merchant + AgenticOrg | The Plural executor is implemented behind `PLURAL_PINE_LIVE_EXECUTION_ENABLED` (off by default; charges and refunds only on a consumed arbiter decision, see [Plural executor](plural-executor.md)). Production use and other rails need provider/bank, merchant, legal, security, ops, rollback, webhook, and monitoring approval. |
| Live POS provider integration | AgenticOrg + merchant + POS/payment provider | Replace simulator with verified provider callbacks, receipt evidence refs, operator monitoring, and rollback. |
| Public channel launch | AgenticOrg + channel owners | Configure secrets, review channel policy, run webhook smoke, and verify source/freshness labels. |
| Verified buyer authority | Grantex + AgenticOrg | Implemented for UCP Complete: the server requests Intent, Cart and Payment mandates from Grantex (`core/commerce/mandates_client.py`, behind `AGENTICORG_GRANTEX_MANDATES_ENABLED`, default off) and verifies them locally (`core/commerce/oacp_mandates.py`, every case of the shared mandate vectors) before the arbiter's `buyer_authority_verified` can pass ([Mandates](mandates.md#grantex-mandates)). Purchase prepare, POS handoff and mandate-request handoffs still carry caller-asserted references and are refused in strict runtimes. Needs the Grantex mandate release and `OACP_STATUS_LIST_PUBLIC_ENABLED` deployed first. |
| Pending and post-3-D Secure payments | AgenticOrg + Plural | Implemented: a verified Plural order event relayed to `POST /api/v1/commerce/runtime/ucp/plural/events` reconciles the payment journal and the checkout once (capture after 3-D Secure, a pending payment captured at the provider, a failed payment). Plural's event field names are verified only against the fake transport until the sandbox confirms them. |
| Ledger export, operator decisions, witness | AgenticOrg + Grantex | Ledger routes and offline-verifiable export, operator decisions bound to arbiter decisions, Grantex countersignature and chain-head witness (Phase 2 G14-G16). |
| Multi-source conflict resolution | AgenticOrg + merchants | Define precedence when Shopify, OMS, ERP, WMS, and provider facts disagree. |
| Public self-service rollout | AgenticOrg | Self-service config UI/API is implemented; complete support runbooks, approval queue, and merchant-launch ops before broad public rollout. |
