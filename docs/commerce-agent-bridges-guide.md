# OACP Buyer Bridge Developer Guide

Canonical runtime docs: `docs/oacp/README.md` and
`docs/oacp/end-user-flow.md`. This page is retained as a developer bridge note
and should follow the current OACP owner split.

All buyer bridges call the same artifact-backed answer path. They must show
source/freshness labels and refuse unsupported final commitments unless required
OACP and provider evidence exists.

| Surface | Runtime path |
| --- | --- |
| Web | `POST /api/v1/commerce/runtime/bridges/web/ask` |
| ChatGPT-style | MCP `seller.*` tools or OpenAPI bridge |
| Claude-style | MCP stdio/remote `seller.*` tools |
| Gemini-style | OpenAPI schema plus A2A card |
| Perplexity-style | OpenAPI hosted answer/action schema |
| WhatsApp | `POST /api/v1/commerce/runtime/bridges/whatsapp/webhook` with `X-Hub-Signature-256` |
| Telegram | `POST /api/v1/commerce/runtime/bridges/telegram/webhook` with `X-Telegram-Bot-Api-Secret-Token` |

## Public Catalog Publishing

Public catalog publishing is a separate read-only surface from the authenticated
runtime bridge. It is fail-closed unless `OACP_PUBLIC_CATALOG_ENABLED=true` is
set by the operator.

| Surface | Path |
| --- | --- |
| Seller profile page | `GET /api/v1/public/commerce/sellers/{merchant_id}?tenant_id=...&seller_agent_id=...` |
| Buyer-safe catalog JSON | `GET /api/v1/public/commerce/sellers/{merchant_id}/catalog.json?tenant_id=...&seller_agent_id=...` |
| Product detail page | `GET /api/v1/public/commerce/sellers/{merchant_id}/products/{product_slug}?tenant_id=...&seller_agent_id=...` |
| Product detail JSON | `GET /api/v1/public/commerce/sellers/{merchant_id}/products/{product_slug}.json?tenant_id=...&seller_agent_id=...` |
| Schema.org JSON-LD | `GET /api/v1/public/commerce/sellers/{merchant_id}/schema-org.jsonld?tenant_id=...&seller_agent_id=...` |
| Merchant sitemap | `GET /api/v1/public/commerce/sellers/{merchant_id}/sitemap.xml?tenant_id=...&seller_agent_id=...` |
| Merchant llms.txt | `GET /api/v1/public/commerce/sellers/{merchant_id}/llms.txt?tenant_id=...&seller_agent_id=...` |

These endpoints render only public-safe product, SKU, price, image, inventory
snapshot, source, and freshness fields from stored Shopify/OACP evidence. They
do not publish raw Shopify payloads, credentials, provider payloads, checkout
links, mandate tokens, order state, or payment state.

## Adapter Payloads

Use `GET /api/v1/commerce/runtime/protocol-adapters` for all surfaces or
`GET /api/v1/commerce/runtime/protocol-adapters/{surface}` for one surface.
Available surfaces:

- `schema_org_product_offer_jsonld`
- `ucp_style_capability_profile`
- `acp_style_commerce_interaction_profile`
- `ap2_style_mandate_payment_evidence_profile`
- `a2a_agent_card_task_metadata`
- `mcp_tool_resource_metadata`
- `openapi_buyer_safe_bridge_schema`

Payloads are compatibility mappings generated from cached OACP artifacts. They
are not external certification, publication, checkout, or payment execution.

