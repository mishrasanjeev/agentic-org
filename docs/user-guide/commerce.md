## The end-to-end ownership model

AgenticOrg runs seller/buyer agents and their skills, knowledge and approved integration paths. Grantex supplies trust, policy and canonical artifact authority. Shopify, another merchant system or a catalog file the merchant uploads remains the operational source of truth. Buyers pay on the merchant's own payment page, its Shopify checkout or its own Plural account; AgenticOrg never collects the money. Banks, fintechs, providers and POS systems own payment, mandate and fulfillment execution.

For a bank-led pilot with explicit owners, human decisions and blocked paths,
see the [merchant-services process map](/docs/bfsi-merchant-services#process-map).

```flow
Merchant setup | Create the shop on Sell Everywhere, or configure the Seller Commerce Agent and merchant-scoped sources/channels on Commerce Runtime.
Catalog read | Read real products, variants, images, price and inventory snapshots from Shopify with the merchant's own read-only token, or take them from a CSV or JSON upload.
Grantex authority | Send the bounded authority request and obtain validated artifacts. Turning on Sell everywhere does this once by itself.
AgenticOrg cache | Check each artifact's Grantex signature, scope, expiry and revocation status, then persist it with source and freshness.
Buyer conversation | Answer through configured web/agent/channel bridges from supported evidence.
Payment link or prepared handoff | A ready checkout gives the buyer a link to the merchant's own payment page after an allow decision; other paths ask the authoritative provider, merchant or POS to confirm its own action.
```

## Merchant setup checklist

A merchant sets up a real shop on **Sell Everywhere** (`/dashboard/sell-everywhere`): create or pick the shop, add a catalog, write the returns, shipping and pickup policies (and a flat shipping fee without Shopify), set order limits, choose where buyers pay, then turn it on. Going live enrols the tenant with Grantex, gives the shop an address, publishes its profile and refreshes its artifacts. See [Sell everywhere](/docs/oacp-sell-everywhere) and the [merchant setup checklist](/docs/oacp-merchant-setup).

**Commerce Runtime** is the operator console for the same runtime. Open it with an administrator or merchant role. Prepare the merchant identity/reference, source system, owner, catalog scope, approved channels, publishing state and provider/POS references. Review the available form and onboarding packet before requesting authority.

For Shopify, the merchant creates a custom app in its own store with the `read_products` and `read_inventory` Admin API scopes and enters its token; AgenticOrg checks it with one read and stores it encrypted. Every sync uses the merchant's own stored token. A deployment-wide Shopify store is read only for the one operator tenant named in `AGENTICORG_SHOPIFY_ENV_OPERATOR_TENANT_ID`. Product webhooks go to the merchant's own webhook address and are checked with the merchant's own signing secret. No scraping is needed for the supported Shopify path.

A shop without Shopify uploads a CSV or JSON catalog: one product per row, one currency, up to 2,000 products. It is recorded as uploaded by the merchant, never as coming from a store system.

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

With Sell everywhere turned on, a published shop is listed in the AgenticOrg network: buyers reach it from ChatGPT and other MCP apps through `https://mcp.agenticorg.ai/mcp`, from the AgenticOrg shopping bot on Telegram, and on the open web through its UCP profile. That is AgenticOrg's own network, not a listing in another company's marketplace.

Current WooCommerce/ERP/PIM/OMS/WMS/custom-source configuration does not have universal runtime parity with Shopify. Treat each as an integration project until its adapter is implemented and validated; until then, a CSV or JSON upload is the supported way to bring such a catalog.

## Commerce decisions and the ledger

Before a purchase handoff, an Offline POS handoff, a payment link or a checkout completion goes anywhere, a fixed set of rules decides it. A payment link needs valid artifacts and the merchant's own limits, including its daily maximum; agent-executed completion also needs mandate, consent and the buyer's Commerce Passport cap. The answer is allow or refuse, with the first failing rule as the reason, and it never waits for a person. An allow can be used once, for exactly that payload. Every commerce change is written to the commerce ledger, which an administrator can export and check offline. Optional human review copies a refusal into the review queue without changing it.

UCP and ACP checkout for agent platforms, the AgenticOrg network and the public UCP profile are switched off by default. Turning them on is an operator step, one at a time. Once checkout is on, a merchant that chose where buyers pay hands each ready checkout to its own payment page; see [Getting paid](/docs/oacp-getting-paid). The agent-executed payment executor stays off, and refunds of completed checkouts are also decided by the same rules.

## Payments and offline POS

**Payment hand-off.** A merchant chooses its own Shopify checkout or its own Plural account. The buyer pays on that page and authenticates the payment there. A Plural payment confirmed for the right amount, by Plural's signed webhook or the once-a-minute status check, becomes an order on the Orders page with an email to the tenant's administrators; any other payment is refused and logged for a refund. A Shopify-paid order stays in the merchant's Shopify admin.

Plural capability verification records provider-owned, non-sensitive capability evidence. It is not a completed payment or mandate. A human sets up and authorizes payment relationships with the fintech/provider under its own process.

Offline POS handoff means a bounded interaction with a physical-store/POS confirmation path; it does not mean unlimited offline financial authority. Provider/POS/merchant evidence remains authoritative. AgenticOrg does not execute OACP capture, refunds, holds, shipping or order creation merely from cached artifacts; an order is created only from a confirmed provider payment.

Public discovery remains off unless explicitly enabled through platform and merchant settings. Readiness, certification and public standardization are not implied by these routes.

Next: [BFSI merchant-services playbook](/docs/bfsi-merchant-services), [API and agent clients](/docs/api-sdk-mcp).
