## The end-to-end ownership model

AgenticOrg runs seller/buyer agents and their skills, knowledge and approved integration paths. Grantex supplies trust, policy and canonical artifact authority. Shopify or another merchant system remains the operational source of truth. Banks, fintechs, providers and POS systems own payment, mandate and fulfillment execution.

```flow
Merchant setup | Configure the Seller Commerce Agent and merchant-scoped sources/channels.
Shopify read-only sync | Fetch real products, variants, images, price and inventory snapshots with approved access.
Grantex authority | Send the bounded authority request and obtain validated artifacts.
AgenticOrg cache | Persist artifacts with scope, source, freshness, TTL and revocation posture.
Buyer conversation | Answer through configured web/agent/channel bridges from supported evidence.
Prepared handoff | Ask the authoritative provider, merchant or POS path to confirm its own action.
```

## Merchant setup checklist

Open **Commerce Runtime** with an administrator or merchant role. Prepare the merchant identity/reference, source system, owner, catalog scope, approved channels, publishing state and provider/POS references. Review the available form and onboarding packet before requesting authority.

For Shopify, obtain the reviewed Admin API access and webhook configuration from the merchant's authorized administrator. Use least-powerful read scopes and protected merchant credential custody. No scraping is needed for the supported Shopify path.

Synchronize a bounded catalog and compare SKU/variant, image, amount/currency and inventory timestamp with Shopify. Confirm a signed webhook is rejected when invalid and accepted only under the configured binding. Webhook receipt alone does not prove catalog freshness; inspect the resulting source snapshot.

## Ask useful buyer questions

For merchant setup through a third-party buyer app and the Plural payment
boundary, follow the [guided seller A2A journey](/docs/seller-a2a-commerce-journey).
To watch a buyer agent that is **not** hosted on AgenticOrg talk to a
synthetic seller over real A2A HTTP+JSON, use the
[external buyer demo](/docs/external-buyer-a2a-demo). It shows catalogue
browsing, source/freshness labels, purchase refusal, and token revocation.
It does not process a payment.

Try `Which variants of this item are available in the latest snapshot?` and `What source and timestamp support that price?`. Verify the response is merchant-scoped, source-grounded and explicit about stale or unsupported facts. Cached authority can support non-binding Q&A without routing every message through Grantex.

Do not promote stale snapshots into final price, stock, delivery, tax, return or warranty promises. Prepared purchase output is not an order confirmation or paid state.

## Protocols and channels

The runtime includes bounded protocol-adapter payloads and web/MCP/OpenAPI/A2A/WhatsApp/Telegram bridge routes. Client configuration, provider credentials, platform/marketplace approval and merchant publishing are separate dependencies. A UCP/ACP/schema.org payload does not automatically list a merchant in ChatGPT, Gemini or another marketplace.

Current WooCommerce/ERP/PIM/OMS/WMS/custom-source configuration does not have universal runtime parity with Shopify. Treat each as an integration project until its adapter is implemented and validated.

## Payments and offline POS

Plural/Pine capability verification records provider-owned, non-sensitive capability evidence. It is not a completed payment or mandate. A human sets up and authorizes payment relationships with the fintech/provider under its own process.

Offline POS handoff means a bounded interaction with a physical-store/POS confirmation path; it does not mean unlimited offline financial authority. Provider/POS/merchant evidence remains authoritative. AgenticOrg does not execute OACP capture, refunds, holds, shipping or order creation merely from cached artifacts.

Public discovery remains off unless explicitly enabled through platform and merchant settings. Readiness, certification and public standardization are not implied by these routes.

Next: [BFSI merchant-services playbook](/docs/bfsi-merchant-services), [API and agent clients](/docs/api-sdk-mcp).
