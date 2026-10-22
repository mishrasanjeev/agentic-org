```summary
Nilgiri Tea House is a live example shop on AgenticOrg. Buyer agents can find it at nilgiritea.agenticorg.ai today.
It is a made-up tea shop in Ooty with 10 products in rupees. Every product, price and address is synthetic demo data.
This page follows it from an empty account to a published shop, then shows a buyer agent shopping there.
You can try it yourself in two minutes: open the shop's profile and search its catalog. No account is needed.
Each step ends with what to do for your own shop, so you can copy the path one step at a time.
```

## Meet Nilgiri Tea House

Nilgiri Tea House is the example merchant we run in production so that you can see OACP working end to end. It sells loose-leaf teas, a gift sampler and a few tea accessories from a (made-up) store in Ooty, in the Nilgiri hills.

```figure
nilgiri-story | Nilgiri Tea House, from shop to sale | The shop's catalog and limits go into AgenticOrg. Grantex signs those facts. The shop proves its own address with a DNS record. Buyer agents find it and search it, and the arbiter decides every checkout.
```

Here is what is switched on for it today.

| What | Status | Where |
| --- | --- | --- |
| Shop profile for buyer agents (UCP) | Live | `https://nilgiritea.agenticorg.ai/.well-known/ucp` |
| Catalog search and lookup | Live, no key needed | `https://nilgiritea.agenticorg.ai/ucp/catalog/search` |
| Facts signed by Grantex | 11 signed artifacts, all verified | Refreshed automatically when they expire |
| Checkout, cart and order (UCP), ACP feed and checkout | On | Needs a platform API key issued by the merchant |
| Real payments | Off | No money can move in this demo |

## Try it yourself in two minutes

You need no account. Any computer with `curl`, or just a web browser for the first step, will do.

```flow
Open the shop's profile | Visit https://nilgiritea.agenticorg.ai/.well-known/ucp in your browser. This is the file every buyer agent reads first. It names the UCP version, where to send requests and what the shop can do.
Search the catalog | Send the search below with the word chai. The shop answers with its Masala Chai Blend, its price in paise and whether it is in stock.
Search for something else | Try green, kettle or an empty query for all 10 products. One tea, the Blue Mountain Oolong, is sold out on purpose, so you can see how stock is shown.
```

The profile, shortened:

```bash
curl -s https://nilgiritea.agenticorg.ai/.well-known/ucp
```

```json
{
  "ucp": {
    "version": "2026-04-08",
    "services": {
      "dev.ucp.shopping": [
        { "transport": "rest", "endpoint": "https://nilgiritea.agenticorg.ai/ucp" }
      ]
    },
    "capabilities": {
      "dev.ucp.shopping.catalog.search": [ ... ],
      "dev.ucp.shopping.checkout": [ ... ],
      "dev.ucp.shopping.ap2_mandate": [ ... ]
    }
  },
  "signing_keys": [ { "kty": "EC", "crv": "P-256", ... } ]
}
```

The search:

```bash
curl -s -X POST https://nilgiritea.agenticorg.ai/ucp/catalog/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"chai","context":{"currency":"INR"}}'
```

```json
{
  "products": [
    {
      "title": "Masala Chai Blend",
      "description": { "plain": "Synthetic demo product. Black tea with cardamom, ginger, clove and cinnamon." },
      "variants": [
        {
          "sku": "NTH-MASALA-250",
          "title": "250 g",
          "price": { "amount": 44900, "currency": "INR" },
          "availability": { "available": true, "status": "in_stock" }
        }
      ]
    }
  ],
  "pagination": { "has_next_page": false, "total_count": 1 }
}
```

Prices are in the smallest unit of the currency, so `44900` means ₹449.00. The whole shelf looks like this:

```figure
nilgiri-shelf | What a buyer agent sees | The 10 products the shop serves, with prices and stock. Every answer comes from facts that Grantex signed and AgenticOrg verified. If those facts expire and cannot be renewed, the shop stops answering instead of guessing.
```

## Part 1. How the shop was set up

These are the steps behind Nilgiri Tea House, in order. For the demo, a setup script runs them in one go, calling the same AgenticOrg functions the dashboard uses. For your shop, you do them on the **Commerce Runtime** and **Custom Domains** pages. The detail of each step is in the [Merchant setup checklist](/docs/oacp-merchant-setup).

```flow start=1
Create the merchant | The shop got its own tenant, an administrator and a merchant record: merchant ID nilgiri_tea_house and seller agent seller_agent_nilgiri_tea. For you: sign up, then save your merchant settings on Commerce Runtime. | Owner: Merchant | If blocked: You need the administrator or merchant role.
Load the catalog | Its 10 products, prices and stock went in, plus one store with opening hours. A real shop connects Shopify read-only and runs a sync instead. | Owner: Merchant | If blocked: A missing Shopify credential or a wrong store domain stops the sync and says why.
Set purchase limits | The largest order, the allowed currency (INR) and the most items per order. For you: the same fields in your merchant settings. | Owner: Merchant | If blocked: Without limits the arbiter refuses every purchase with merchant_limits_missing.
Get the facts signed | The operator added the shop to the Grantex allowlist. AgenticOrg then asked Grantex to sign the catalog, prices, stock, policies and capabilities, and got 11 signed artifacts back. | Owner: Platform operator, then merchant | If blocked: Grantex answers unavailable until your tenant is on its allowlist.
Check every signature | AgenticOrg checked each artifact's signature, key, expiry and revocation status before keeping it. All 11 passed. | Owner: AgenticOrg | If blocked: A refusal names the reason, for example revocation_status_unavailable; nothing from that batch is kept.
Create the signing key | One signing key for the shop. Its public half is published in the profile, so agents can check the shop's own answers. | Owner: Merchant | If blocked: Publishing is refused with signing_key_missing until the key exists.
```

## Part 2. Its own address

Buyer agents need a web address to find a shop. Nilgiri Tea House uses `nilgiritea.agenticorg.ai`. Your shop would use a subdomain you own, such as `agent.your-store.example`.

```figure
nilgiri-records | Nilgiri Tea House's two DNS records | A CNAME routes the address to AgenticOrg. A TXT record with a one-time code proves the shop controls the address. With both in place, the address was attested and the profile went live.
```

```flow start=7
Ask for the challenge | AgenticOrg issued a one-time code for nilgiritea.agenticorg.ai and showed the TXT record to create. On Custom Domains you see the same thing, with copy buttons. | Owner: Merchant | If blocked: An IP address or a one-word host is not accepted.
Add the two DNS records | A CNAME from nilgiritea to ghs.googlehosted.com, and the TXT record _agenticorg-ucp.nilgiritea with the code. Your own domain also needs one Search Console owner; see the custom domain guide. | Owner: Merchant | If blocked: DNS changes can take up to an hour to show; check again later.
Verify and publish | AgenticOrg looked the TXT record up, matched the code and linked the address to the shop. The shop opted in to public discovery, Grantex allowed it, and the profile went live. | Owner: AgenticOrg and Grantex | If blocked: The page lists each failed check with its fix, for example serving_host_not_attested.
Certificate | Google issued the HTTPS certificate for the address. That usually takes 15 minutes and at most 24 hours. | Owner: Google | If blocked: After 48 hours without a certificate, check CAA records and that the CNAME is DNS only.
```

Full instructions for your own domain, including every check and its fix, are in [Your own domain for buyer agents](/docs/oacp-custom-domains).

## Part 3. A buyer agent shops

This is what happens when an AI shopping agent buys tea for someone.

```figure
nilgiri-buyer | A buyer agent shops at Nilgiri Tea House | The agent reads the profile, searches, and opens a checkout with its API key. Grantex issues mandates for that exact cart. The arbiter allows or refuses and records the decision. Payments are off in this demo.
```

```flow start=11
Discover | The agent reads /.well-known/ucp and learns the address of the shop's API and which UCP features it supports. | Owner: Buyer agent | If blocked: A 404 means the profile is not published, or its signed facts have lapsed and could not be renewed.
Search | The agent searches for chai and gets the Masala Chai Blend at ₹449, in stock. The answer comes only from verified facts. | Owner: Buyer agent | If blocked: A catalog_unavailable message means the verified facts lapsed and could not be renewed; nothing is guessed. Try again shortly.
Open a checkout | The agent creates a checkout for NTH-MASALA-250, quantity 1, using the API key the merchant issued to its platform. The shop prices it from the same verified facts. | Owner: Buyer agent | If blocked: Without a valid key the shop answers 401.
Get mandates | Grantex issues three signed mandates: what the buyer allowed (Intent), this exact cart (Cart) and the amount and payment rail (Payment). | Owner: Grantex | If blocked: A mandate is refused when it goes beyond what the buyer allowed.
Decide | The arbiter applies its fixed rules, such as the shop's limits and the mandates, and answers allow or refuse. The decision is sealed in the ledger. | Owner: AgenticOrg arbiter | If blocked: A refusal names the first rule that failed; nothing is charged.
Pay | With live payments on, the payment provider would charge once, only on allow. In this demo there is no payment step: once pickup is chosen the checkout is ready, and completing it answers payment_failed, "A payment instrument is required to complete." Nothing is charged. | Owner: Payment provider | If blocked: Live payment stays off until a payment-provider sandbox run has passed.
```

For an agent platform, the checkout request looks like this. The key and the platform profile are your own.

```bash
curl -s -X POST https://nilgiritea.agenticorg.ai/ucp/checkout-sessions \
  -H "X-API-Key: $PLATFORM_KEY" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H 'UCP-Agent: profile="https://your-agent-platform.example/.well-known/ucp"' \
  -H 'Content-Type: application/json' \
  -d '{"line_items":[{"item":{"id":"NTH-MASALA-250"},"quantity":1}]}'
```

Every step of the mandates, the passkey and the decision is in [A purchase, step by step](/docs/oacp-buyer-journey).

## See it with real agents

These are real runs against the live shop on 22 October 2026, not mock-ups. Each one searches the
catalog, opens a checkout and stops at the payment step. Nothing was charged.

### Gemini, through the AgenticOrg MCP server

Gemini CLI was connected to the `agenticorg-mcp-server` UCP tools, pointed at the shop's profile.
Gemini asked before every tool call, and each call was approved by hand.

![Gemini summarises its run: it found Masala Chai Blend, NTH-MASALA-250, at Rs 449 in stock, opened checkout chk_b07d…f2d for one 250 g pack with a total of Rs 449, and the shop answered that the order still needs a fulfilment choice, shipping or pickup.](/screenshots/nilgiri-gemini-summary.webp)

1. **Search and checkout.** Gemini found Masala Chai Blend (NTH-MASALA-250) at ₹449, in stock,
   and opened a checkout for one pack. The shop priced it at ₹449 and marked it `incomplete`: it
   still needed to know how the tea reaches the buyer.

![Gemini updates the checkout to in-store pickup at Nilgiri Tea House, Ooty, with a pickup fee of Rs 0; the session moves to ready_for_complete with totals of 44900 subtotal, 0 fulfilment and 44900 total.](/screenshots/nilgiri-gemini-pickup.webp)

2. **Pickup chosen.** Gemini chose in-store pickup at the Ooty store, which costs nothing. The
   checkout moved to `ready_for_complete` with a total of ₹449.

![The shop's answer to completing the checkout: an error message with code payment_failed, "A payment instrument is required to complete.", severity recoverable, path $.payment.](/screenshots/nilgiri-gemini-payment-stop.webp)

3. **Stop at payment.** Asked to complete, the shop answered `payment_failed`: "A payment
   instrument is required to complete." That is the payment layer. A real shop with live payments
   would take the buyer's payment here, only after the arbiter allows it.

### Telegram, through an AgenticOrg agent

An AgenticOrg agent with the `ucp_buyer` connector answers in a Telegram bot. It may search, read
and open checkouts at sandbox shops, and it has no tool that pays. Asked "Find masala chai at
Nilgiri Tea House and open a checkout for one", it replied:

> I found the **Masala Chai Blend (250 g)** at Nilgiri Tea House for Rs 449 (44900 INR), which is
> currently in stock.
>
> I have successfully opened a checkout session for one unit.
>
> - **Checkout ID:** `chk_0451d43ddbdf4560a3e97c83563a7c1a`
> - **Status:** incomplete
> - **Total:** Rs 449.00 (44900 INR)
>
> *Note: Payment is not available to me; the order stops before payment, which you would complete
> with the shop.*

Behind that one message the agent searched the catalog, opened the checkout at the shop (HTTP 201)
and answered, in about 30 seconds. To give your own agent these tools, see the
[UCP buyer connector](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/ucp-buyer-connector.md).

## Do the same for your shop

| Nilgiri Tea House did | You do | Where |
| --- | --- | --- |
| Created its merchant and seller agent | Sign up and save your merchant settings | Commerce Runtime |
| Loaded 10 synthetic products | Connect Shopify read-only and run a sync | Commerce Runtime |
| Set limits in INR | Set your order limits and currencies | Commerce Runtime |
| Got 11 artifacts signed and verified | Ask your operator to allowlist you at Grantex, then request authority | Commerce Runtime |
| Proved nilgiritea.agenticorg.ai | Add a subdomain, its CNAME and TXT records, and the Search Console owner | Custom Domains |
| Published its profile | Create the signing key and publish | Commerce Runtime |
| Opened checkout and the ACP feed | Issue each agent platform its own API key | Settings, API keys |

Most steps take a few minutes. Waiting for DNS and the certificate usually takes the longest.

## What this demo does not do

- **No real products or customers.** Every product, price, store and address is synthetic, and each product description starts with "Synthetic demo product".
- **No money moves.** Live payments are off, and the demo has no payment provider attached.
- **No shipping rates.** Delivery rates need a real Shopify shop. The demo is for discovery, search and checkout decisions.
- **No store-location search yet.** Store locations are part of UCP 2026-08-25. The shop serves UCP 2026-04-08, so a location search answers that the feature is not served.

## Read next

- [Merchant setup checklist](/docs/oacp-merchant-setup)
- [Your own domain for buyer agents](/docs/oacp-custom-domains)
- [A purchase, step by step](/docs/oacp-buyer-journey)
- [What OACP is and why you need it](/docs/oacp-overview)
