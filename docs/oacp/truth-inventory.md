# OACP Runtime Truth Inventory

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md). Launch closure source of truth: [OACP Runtime Launch Closure PRD](runtime-launch-closure-prd.md).

This inventory is based on `api/v1/commerce_runtime.py`, `core/commerce/c6z_runtime_vertical.py`, `core/commerce/oacp_artifacts.py`, `core/commerce/oacp_merchant_config.py`, the AgenticOrg Commerce Runtime UI, and OACP unit tests.

| Category | AgenticOrg reality | Owner/action |
| --- | --- | --- |
| Implemented runtime | Tenant/merchant/seller scoped commerce config UI/API, Seller onboarding packets, encrypted merchant-scoped Shopify credential setup, read-only Shopify sync, Shopify webhook HMAC checks, Grantex C6Z authority request, artifact cache intake, buyer Q&A from cache, public catalog publishing gate, web/MCP/OpenAPI/A2A/search/WhatsApp/Telegram bridge routes, protocol adapter payloads, Plural/Pine capability verifier, bank/provider pending-adapter config, purchase preparation blockers, Offline POS Bridge handoff packets, POS confirmation intake, local POS simulator, and reconciliation status. | AgenticOrg keeps runtime tests, UI tests, route inventory, and docs aligned. |
| Implemented docs only | Older Commerce Sales Agent planning reports and launch plans. | Link to this docs set when they discuss the current OACP split. |
| Implemented but not broad launch | Channel routes, provider capability checks, and real POS provider callbacks require secrets and partner approvals before public use. | Operators must configure channel/provider/POS env and run smoke tests. |
| Requires external credentials/approval | Shopify access, Grantex service token and tenant allowlist, WhatsApp/Telegram webhook secrets, Plural/Pine credentials, POS provider approval, merchant and provider approval. | Runtime owner collects evidence before launch. |
| Missing | Broad order/payment/mandate/POS execution, universal buyer-surface rollout, live non-Shopify adapters, live bank/custom provider adapters, external public protocol approval, and public launch approvals. | Track as pending runtime/product gaps. |
| Stale/confusing docs | Older text that says AgenticOrg only talks to Grantex Commerce V1 tools or implies Grantex owns merchant runtime. | Supersede with this OACP runtime docs set. |

## Evidence Pointers

- API routes: `api/v1/commerce_runtime.py`
- Runtime helpers: `core/commerce/c6z_runtime_vertical.py`
- Offline POS bridge helpers: `core/commerce/offline_pos_bridge.py`
- Merchant config helpers: `core/commerce/oacp_merchant_config.py`
- Artifact cache and verification: `core/commerce/oacp_artifacts.py`
- UI demo: `ui/src/pages/CommerceRuntimeDemo.tsx`
- UI tests: `ui/src/__tests__/CommerceRuntimeDemo.test.tsx`
- Unit tests: `tests/unit/test_oacp_c6z_runtime_vertical.py`

## Pending Runtime Gaps

| Gap | Owner | Action |
| --- | --- | --- |
| Provider or bank rail execution | Pine Labs Plural/P3P, bank/fintech/custom providers + merchant + AgenticOrg | Add only after provider/bank, merchant, legal, security, ops, rollback, webhook, and monitoring approval. |
| Live POS provider integration | AgenticOrg + merchant + POS/payment provider | Replace simulator with verified provider callbacks, receipt evidence refs, operator monitoring, and rollback. |
| Public channel launch | AgenticOrg + channel owners | Configure secrets, review channel policy, run webhook smoke, and verify source/freshness labels. |
| Multi-source conflict resolution | AgenticOrg + merchants | Define precedence when Shopify, OMS, ERP, WMS, and provider facts disagree. |
| Public self-service rollout | AgenticOrg | Self-service config UI/API is implemented; complete support runbooks, approval queue, and merchant-launch ops before broad public rollout. |
