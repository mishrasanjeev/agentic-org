# OACP Buyer Bridge Developer Guide

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

