# AgenticOrg Buyer Discovery Consumer Foundation

C6H adds the AgenticOrg-side consumer foundation for Grantex buyer-agent
discovery handoff data. It is a read-only sandbox integration. It does not
deploy anything, expose public commerce discovery, approve a merchant, enable
checkout/payment, enable live payments, enable live Plural, call payment
providers, call merchant private APIs, store provider credentials, write
production configuration, or set allowlists.

## What Buyers Can Ask

A buyer can ask an AgenticOrg buyer agent to inspect a Grantex-grounded merchant
preview. AgenticOrg calls only the Grantex read-only buyer discovery preview
route:

`GET /v1/commerce/merchants/{merchant_id}/agenticorg-buyer-discovery-preview`

The buyer-facing response is built only from the fields Grantex returns. If
Grantex does not return data, reports blockers, or has not requested sandbox
handoff, AgenticOrg refuses or shows a preview-only response.

## Safe Fields

AgenticOrg may show:

- merchant display name;
- category;
- country and currency when Grantex marks them public-safe;
- discovery description;
- read-only preview status;
- capped catalog sample titles, brands, and categories;
- allowed buyer-agent capability labels;
- blocked buyer-agent capability labels;
- safety labels;
- a source reference back to the Grantex handoff preview.

AgenticOrg must not show legal names, private contacts, private approval
references, contracts, provider credentials, payment provider metadata,
tokens/JWTs/passports, raw payloads, DB/Redis URLs, production configuration
values, or allowlist values.

## Refusal Behavior

The C6H buyer-agent workflow refuses:

- checkout and payment requests;
- live payment, live Plural, and provider-access requests;
- fulfillment, delivery, shipment, tracking, and order-status execution;
- returns and refund execution;
- invented sellers, products, prices, discounts, delivery promises, refund
  promises, availability, launch status, or payment readiness.

When Grantex says public discovery is disabled, AgenticOrg public commerce
discovery is disabled, production approval is not granted, or the merchant is
not live, AgenticOrg surfaces a preview-only response. Preview-only is not
public launch and not merchant approval.

## Channel-Neutral Launch Notes

No live channel integration is enabled by C6H. Future channels can wrap the same
read-only buyer discovery workflow after separate implementation, security
review, and approval.

| Channel | Future wrapper shape | C6H posture |
| --- | --- | --- |
| ChatGPT | A future app or remote MCP wrapper could call the AgenticOrg buyer discovery workflow. | Not live; no custom app is shipped by C6H. |
| Claude | A future MCP connector could call the same workflow with tenant-scoped auth. | Not live; no Claude connector is shipped by C6H. |
| Gemini | A future function-calling or hosted agent wrapper could call the workflow. | Not live; no Gemini integration is shipped by C6H. |
| WhatsApp | A future WhatsApp Business adapter could translate messages into the workflow. | Not live; no WABA, phone number, webhook, or template is shipped by C6H. |
| Telegram | A future bot adapter could call the workflow after bot-token and webhook review. | Not live; no Telegram bot is shipped by C6H. |
| generic web/chat | A future AgenticOrg web chat can call the same workflow. | Not live; C6H only adds the backend consumer foundation. |

## Operator And Developer Notes

Merchants still onboard, prepare evidence, request review, and request handoff
through Grantex. AgenticOrg does not duplicate Grantex seller onboarding,
catalog, policy, passport, checkout, payment, fulfillment, refund, or provider
logic.

The AgenticOrg connector exposes one new read-only alias:

`grantex_commerce:buyer_discovery_preview`

It maps to the Grantex C6G preview route and does not expose Grantex operator
handoff request or withdrawal endpoints.

## Stop Conditions

Stop and do not proceed if a change would:

- enable AgenticOrg public commerce discovery;
- enable Grantex public discovery;
- call Plural or any payment provider directly;
- call merchant private APIs directly;
- store provider credentials or secrets;
- create checkout, payment, fulfillment, return, or refund actions;
- write production configuration or allowlists;
- treat sandbox/demo/synthetic data as merchant production approval.
