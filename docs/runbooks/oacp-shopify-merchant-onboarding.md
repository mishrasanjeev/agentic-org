# Shopify Merchant OACP Onboarding Runbook

Canonical runtime docs: `docs/oacp/README.md` and
`docs/oacp/end-user-flow.md`. This runbook is retained for operator continuity
and should follow the current OACP owner split.

## Owner Split

| Area | Owner |
| --- | --- |
| Seller and buyer agent runtime | AgenticOrg |
| Shopify catalog source of record | Merchant |
| OACP trust authority and artifact signing | Grantex |
| Mandate/payment rail execution | Pine Labs Plural/P3P |
| Offline POS handoff orchestration | AgenticOrg |
| POS/payment confirmation | Merchant POS or payment provider |

## Operator Steps

1. Save merchant commerce configuration in `/dashboard/commerce-runtime`:
   `PUT /api/v1/commerce/runtime/merchant-configs/{merchant_id}`.
2. Create or sync the Seller Commerce Agent onboarding packet:
   `POST /api/v1/commerce/runtime/seller-agents/onboarding-packets`.
3. Store Shopify credentials through encrypted connector storage:
   `POST /api/v1/commerce/runtime/seller-agents/connectors/shopify/credentials`.
4. Run read-only sync:
   `POST /api/v1/commerce/runtime/seller-agents/shopify/sync`.
5. Request Grantex authority:
   `POST /api/v1/commerce/runtime/authority/grantex/request`.
6. Cache returned artifacts (each is verified first; in strict runtimes one failure stores
   nothing): `POST /api/v1/commerce/runtime/artifacts/cache`. To re-fetch a scope from Grantex
   replay, for example after the verification release is deployed:
   `POST /api/v1/commerce/runtime/artifacts/refresh`.
7. Generate adapter payloads:
   `GET /api/v1/commerce/runtime/protocol-adapters`.
8. Ask buyer questions:
   `POST /api/v1/commerce/runtime/buyer-sessions/ask`.
9. Verify Pine/Plural capability:
   `POST /api/v1/commerce/runtime/providers/plural-pine/mandate-capability/verify`.
10. Attempt purchase preparation:
   `POST /api/v1/commerce/runtime/purchase/prepare`. The commerce arbiter decides it; an `allow`
   returns a `decision_id` that a second call for the same handoff consumes once.
11. Check Offline POS readiness when in-store handoff is offered:
   `GET /api/v1/commerce/runtime/pos/offline/readiness`.
12. Create a non-executing POS handoff packet:
   `POST /api/v1/commerce/runtime/pos/offline/handoffs` (first call returns `decision_issued`
   with a `decision_id`; the repeat with that `decision_id` stores the packet).
13. Run simulator confirmation for local smoke only:
   `POST /api/v1/commerce/runtime/pos/offline/simulator/confirm`.
14. Check the ledger: `GET /api/v1/commerce/runtime/ledger/events` and
   `GET /api/v1/commerce/runtime/ledger/decisions`.
15. Optional, behind `AGENTICORG_UCP_PROFILE_ENABLED` (default off): claim and verify a serving
   host (`POST /api/v1/commerce/runtime/merchant-configs/{merchant_id}/ucp/serving-host`, then
   `.../serving-host/verify` after adding the DNS TXT record), create a signing key
   (`.../ucp/keys`) and publish (`.../ucp/publish`). See `docs/oacp/ucp-profile.md`; checkout
   flags follow `docs/oacp/explainers/launch-rollback-runbook.md`.

## Required Config

| Capability | Required config |
| --- | --- |
| Merchant self-service config | `commerce.merchant_config.write` scope or `agenticorg:admin`; save source, channels, payment provider, public publishing, and POS metadata per tenant/merchant/seller. |
| Shopify read-only sync | Merchant Shopify Admin token or OAuth code exchange material with product read scopes. |
| Shopify webhooks | `SHOPIFY_WEBHOOK_SECRET`. |
| Grantex authority | `GRANTEX_COMMERCE_BASE_URL`, `GRANTEX_COMMERCE_INTERNAL_TOKEN`, tenant allowlist in Grantex. |
| Artifact verification | `AGENTICORG_GRANTEX_ISSUER` (or `GRANTEX_BASE_URL`); the Grantex environment must list an `oacp-` key in its JWKS and serve its status list. `AGENTICORG_OACP_VERIFY_STRICT` is on in strict runtimes. |
| Commerce arbiter | Merchant config `provider_policy.arbiter` limits; in strict runtimes Grantex countersignature of allows (`AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT`) and refusal of caller-asserted buyer authority (`AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY`). |
| ChatGPT/Claude/Gemini/Perplexity bridges | `AGENTICORG_API_KEY` and platform approval/config. |
| WhatsApp | `WHATSAPP_BUSINESS_ACCESS_TOKEN`, `WHATSAPP_BUSINESS_PHONE_NUMBER_ID`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`. |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET_TOKEN`. |
| Public catalog publishing | Merchant commerce config `public_publishing.enabled=true`; optional public base URL; `OACP_PUBLIC_CATALOG_PLATFORM_DISABLED=true` disables all merchants. |
| Plural/Pine P3P setup | `PLURAL_PINE_CLIENT_ID`, `PLURAL_PINE_CLIENT_SECRET`, `PLURAL_PINE_ENVIRONMENT=sandbox`, optional `PLURAL_PINE_CAPABILITY_URL`. |
| Offline POS handoff | Store/POS location metadata. Simulator is available locally; real provider needs `OFFLINE_POS_PROVIDER_ID` and `OFFLINE_POS_WEBHOOK_SECRET`. |
| Live provider flow | External merchant/provider/legal/security/ops approval plus `PLURAL_PINE_LIVE_EXECUTION_ENABLED=true` and a consumed arbiter `allow` decision. On `main` this releases a prepared provider-owned handoff; no payment executor is registered. |

## Safe Failure Mode

If Shopify credentials are missing, sync returns `blocked_missing_shopify_credentials`.
If Grantex credentials or tenant allowlist are missing, authority request returns
`blocked_missing_grantex_env` or Grantex refuses the tenant. If Plural/Pine is
not configured, purchase preparation returns `plural_pine_capability_missing_or_stale`.
If POS location metadata or provider callback evidence is missing, Offline POS
routes return a handoff blocker or staff-review outcome; they must not claim
payment or order success.

Do not bypass these blockers by using raw payloads, pasted tokens, synthetic
production claims, or manually edited cache records.

