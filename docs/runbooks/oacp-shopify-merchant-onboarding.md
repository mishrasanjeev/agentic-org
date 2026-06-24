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

1. Create the Seller Commerce Agent onboarding packet:
   `POST /api/v1/commerce/runtime/seller-agents/onboarding-packets`.
2. Store Shopify credentials through encrypted connector storage:
   `POST /api/v1/commerce/runtime/seller-agents/connectors/shopify/credentials`.
3. Run read-only sync:
   `POST /api/v1/commerce/runtime/seller-agents/shopify/sync`.
4. Request Grantex authority:
   `POST /api/v1/commerce/runtime/authority/grantex/request`.
5. Cache returned artifacts:
   `POST /api/v1/commerce/runtime/artifacts/cache`.
6. Generate adapter payloads:
   `GET /api/v1/commerce/runtime/protocol-adapters`.
7. Ask buyer questions:
   `POST /api/v1/commerce/runtime/buyer-sessions/ask`.
8. Verify Pine/Plural capability:
   `POST /api/v1/commerce/runtime/providers/plural-pine/mandate-capability/verify`.
9. Attempt purchase preparation:
   `POST /api/v1/commerce/runtime/purchase/prepare`.
10. Check Offline POS readiness when in-store handoff is offered:
   `GET /api/v1/commerce/runtime/pos/offline/readiness`.
11. Create a non-executing POS handoff packet:
   `POST /api/v1/commerce/runtime/pos/offline/handoffs`.
12. Run simulator confirmation for local smoke only:
   `POST /api/v1/commerce/runtime/pos/offline/simulator/confirm`.

## Required Config

| Capability | Required config |
| --- | --- |
| Shopify read-only sync | Merchant Shopify Admin token or OAuth code exchange material with product read scopes. |
| Shopify webhooks | `SHOPIFY_WEBHOOK_SECRET`. |
| Grantex authority | `GRANTEX_COMMERCE_BASE_URL`, `GRANTEX_COMMERCE_INTERNAL_TOKEN`, tenant allowlist in Grantex. |
| ChatGPT/Claude/Gemini/Perplexity bridges | `AGENTICORG_API_KEY` and platform approval/config. |
| WhatsApp | `WHATSAPP_BUSINESS_ACCESS_TOKEN`, `WHATSAPP_BUSINESS_PHONE_NUMBER_ID`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`. |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET_TOKEN`. |
| Public catalog publishing | `OACP_PUBLIC_CATALOG_ENABLED=true` after merchant/operator approval; optional `AGENTICORG_PUBLIC_BASE_URL`. |
| Plural/Pine P3P setup | `PLURAL_PINE_CLIENT_ID`, `PLURAL_PINE_CLIENT_SECRET`, `PLURAL_PINE_ENVIRONMENT=sandbox`, optional `PLURAL_PINE_CAPABILITY_URL`. |
| Offline POS handoff | Store/POS location metadata. Simulator is available locally; real provider needs `OFFLINE_POS_PROVIDER_ID` and `OFFLINE_POS_WEBHOOK_SECRET`. |
| Live provider flow | External merchant/provider/legal/security/ops approval plus `PLURAL_PINE_LIVE_EXECUTION_ENABLED=true`. |

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

