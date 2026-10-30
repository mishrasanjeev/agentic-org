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
| Shopping from ChatGPT and other MCP apps | Live, no sign-in, no payment tools | `https://mcp.agenticorg.ai/mcp` ([how to connect](#chatgpt-through-the-hosted-agenticorg-mcp-server)) |
| Payments | Off: no payment method chosen, and agent-executed payment is off | No money can move in this demo |

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

These are the steps behind Nilgiri Tea House, in order. For the demo, a setup script runs them in one go, calling the same AgenticOrg functions the dashboard uses. For your shop, the **Sell Everywhere** page (`/dashboard/sell-everywhere`) takes you through the same steps and runs the Grantex and publishing steps itself; see [Sell everywhere](/docs/oacp-sell-everywhere). The detail of each step is in the [Merchant setup checklist](/docs/oacp-merchant-setup).

```flow start=1
Create the merchant | The shop got its own tenant, an administrator and a merchant record: merchant ID nilgiri_tea_house and seller agent seller_agent_nilgiri_tea. For you: sign up, then type a shop name on Sell Everywhere and select Create shop; the id is generated. | Owner: Merchant | If blocked: You need the administrator or merchant role.
Load the catalog | Its 10 products, prices and stock went in, plus one store with opening hours. A real shop connects Shopify read-only, or uploads a CSV or JSON file, on Sell Everywhere. | Owner: Merchant | If blocked: A missing Shopify credential, a wrong store domain or a bad file row stops the read and says why.
Set purchase limits | The largest order, the allowed currency (INR) and the most items per order. For you: the Order limits step on Sell Everywhere, which also asks for your daily maximum. | Owner: Merchant | If blocked: Without limits, Sell everywhere stays off for your shop.
Get the facts signed | The operator added the shop to the Grantex allowlist. AgenticOrg then asked Grantex to sign the catalog, prices, stock, policies and capabilities, and got 11 signed artifacts back. For you: turning on Sell everywhere enrols you with Grantex and asks for the signatures, with no allowlist edit for your shop. | Owner: Platform operator for the demo; AgenticOrg for you | If blocked: The Grantex enrolment row says Waiting while your deployment's Grantex cannot enrol tenants yet.
Check every signature | AgenticOrg checked each artifact's signature, key, expiry and revocation status before keeping it. All 11 passed. | Owner: AgenticOrg | If blocked: A refusal names the reason, for example revocation_status_unavailable; nothing from that batch is kept.
Create the signing key | One signing key for the shop. Its public half is published in the profile, so agents can check the shop's own answers. For you: Sell everywhere creates it if it is missing. | Owner: Merchant | If blocked: Publishing is refused with signing_key_missing until the key exists.
```

## Part 2. Its own address

Buyer agents need a web address to find a shop. Nilgiri Tea House uses `nilgiritea.agenticorg.ai`, connected by hand with the two records below, the way you would connect a domain of your own. Your shop can get `<your-shop>.agenticorg.ai` from Sell everywhere with no DNS work (while fewer than 20 shops have one), or use a subdomain you own, such as `agent.your-store.example`.

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
nilgiri-buyer | A buyer agent shops at Nilgiri Tea House | The agent reads the profile, searches, and opens a checkout with its API key. The mandate step belongs to agent-executed payment, which is switched off. The arbiter allows or refuses and records the decision. This demo has no payment method, so it stops before payment.
```

```flow start=11
Discover | The agent reads /.well-known/ucp and learns the address of the shop's API and which UCP features it supports. | Owner: Buyer agent | If blocked: A 404 means the profile is not published, or its signed facts have lapsed and could not be renewed.
Search | The agent searches for chai and gets the Masala Chai Blend at ₹449, in stock. The answer comes only from verified facts. | Owner: Buyer agent | If blocked: A catalog_unavailable message means the verified facts lapsed and could not be renewed; nothing is guessed. Try again shortly.
Open a checkout | The agent creates a checkout for NTH-MASALA-250, quantity 1, using the API key the merchant issued to its platform. The shop prices it from the same verified facts. | Owner: Buyer agent | If blocked: Without a valid key the shop answers 401.
Mandates, switched off | On the agent-executed path, Grantex would issue three signed mandates: what the buyer allowed (Intent), this exact cart (Cart) and the amount and payment rail (Payment). That path is off, so no mandate is issued here. | Owner: Grantex | If blocked: A mandate is refused when it goes beyond what the buyer allowed.
Decide | For a shop with a payment method, the arbiter checks its signed facts and its limits before any payment link exists, and answers allow or refuse. The decision is sealed in the ledger. | Owner: AgenticOrg arbiter | If blocked: A refusal names the first rule that failed; no link is issued and nothing is charged.
Pay | Nilgiri Tea House has no payment method, so there is no payment step: once pickup is chosen the checkout is ready, and completing it answers payment_failed, "A payment instrument is required to complete." Nothing is charged. A shop that chose where buyers pay answers requires_escalation with continue_url instead: the link to its own Shopify checkout or Plural page, where the buyer pays. | Owner: The shop's payment page | If blocked: Without a payment method, buyers get no payment link.
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

How a payment link is issued, and how the switched-off mandate path works, is in [A purchase, step by step](/docs/oacp-buyer-journey).

## See it with real agents

These are real runs against the live shop on 22 and 25 October 2026, not mock-ups. Each one
searches the catalog, opens a checkout and stops at the payment step. Nothing was charged.

### ChatGPT, through the hosted AgenticOrg MCP server

ChatGPT connects straight to AgenticOrg's hosted MCP server at `https://mcp.agenticorg.ai/mcp`. The
server needs no sign-in and offers shop tools only: search, product lookup, and opening or updating a
checkout. It has no tool that pays, and each caller is rate-limited.

For these runs the server served Nilgiri Tea House alone. For Sell everywhere the same address serves
every shop on the AgenticOrg network and adds two tools: `list_shops`, and `complete_checkout`, which
returns the shop's payment link for a ready checkout. It still has no tool that pays. See
[Sell everywhere](/docs/oacp-sell-everywhere).

Connect it once:

```flow
Add the server | In ChatGPT on the web, open Plugins, choose Add, then Add custom MCP server.
Fill in three fields | Name it Nilgiri Tea House, set Server URL to https://mcp.agenticorg.ai/mcp and set Authentication to No authentication.
Create it | Tick that you trust the server, then choose Create as a plugin.
Shop | In any chat, type @Nil, pick the plugin and ask in plain words.
```

The run below used a free ChatGPT account on 25 October 2026. ChatGPT's menu names may change over
time.

![ChatGPT's message box with @Nil typed; the picker offers the plugin Nilgiri Tea House (AgenticOrg demo).](/screenshots/nilgiri-chatgpt-picker.webp)

1. **Pick the plugin.** Type `@Nil` in the message box and choose Nilgiri Tea House.

![The buyer's message, sent with the Nilgiri Tea House plugin: find masala chai, tell me the price, open a checkout for 1 pack for store pickup in INR, and stop before any payment. ChatGPT worked for 24 seconds and shows Masala Chai Blend from Nilgiri Tea House, a 250 g pack at Rs 449.00, in stock, black tea blended with cardamom, ginger, clove and cinnamon.](/screenshots/nilgiri-chatgpt-request.webp)

2. **Ask in plain words.** Ask it to find masala chai, open a checkout for one pack for store pickup,
   and stop before paying. In 24 seconds ChatGPT searched the catalog and found Masala Chai Blend
   (250 g, in stock, ₹449.00).

![ChatGPT's checkout card: checkout created successfully, store pickup selected at Nilgiri Tea House, Ooty, India (demo store). Item 1 at Rs 449.00, pickup fee Rs 0.00, total Rs 449.00, checkout status Ready for completion, payment: no payment made. Checkout ID chk_b874442731f24f9f8308752406904b57. What this means: the checkout is ready for the next step with one 250 g pack and store pickup configured; it stopped before payment, no payment instrument was added and no charge was made.](/screenshots/nilgiri-chatgpt-checkout.webp)

3. **Checkout, then stop at payment.** ChatGPT opened the checkout and chose pickup at the Ooty
   store, which costs nothing. The checkout is ready for completion with a total of ₹449.00, and no
   payment was added or charged. A shop that has chosen where buyers pay would give the buyer a link
   to its own payment page here, only after the arbiter allows it.

ChatGPT may show a product photo it found on its own. The shop's catalog has no real photos, so any
picture you see there does not come from Nilgiri Tea House.

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
   instrument is required to complete." That is the payment layer. A shop that has chosen where
   buyers pay would answer with a link to its own payment page here, only after the arbiter allows it.

### Telegram, through an AgenticOrg agent

An AgenticOrg agent with the `ucp_buyer` connector answers in a Telegram bot. It may search, read
and open checkouts at sandbox shops, and it has no tool that pays. Asked "Find masala chai at
Nilgiri Tea House and open a checkout for one", it replied:

![In Telegram, the buyer asks "Find masala chai at Nilgiri Tea House and open a checkout for one." The bot replies: Item found: Masala Chai Blend, 250 g, Rs 449, in stock. Checkout ID: chk_de0b0a92da6446259b4147662d72c60f. Status: incomplete. Total: Rs 449. The order stops before payment, which the buyer would complete with the shop.](/screenshots/nilgiri-telegram-reply.webp)

Behind that one message the agent searched the catalog, opened the checkout at the shop (HTTP 201)
and answered, in about 30 seconds. To give your own agent these tools, see the
[UCP buyer connector](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/ucp-buyer-connector.md).

## Do the same for your shop

| Nilgiri Tea House did | You do | Where |
| --- | --- | --- |
| Created its merchant and seller agent | Sign up and create your shop | Sell Everywhere |
| Loaded 10 synthetic products | Connect Shopify read-only, or upload a CSV or JSON file | Sell Everywhere |
| Had no shipping fee | Write your policies, and set a shipping fee if you have no Shopify | Sell Everywhere |
| Set limits in INR | Set your order limits, daily maximum and currency | Sell Everywhere |
| Had no payment method | Choose your Shopify checkout or your Plural account | Sell Everywhere, Payments |
| Got 11 artifacts signed and verified | Turn on Sell everywhere; it enrols you with Grantex and asks for the signatures | Sell Everywhere |
| Proved nilgiritea.agenticorg.ai | Take the address Sell everywhere gives you, or add your own subdomain and its records | Sell Everywhere, or Custom Domains |
| Published its profile | Nothing: Sell everywhere creates the key and publishes, and Publish shows why if it waits | Sell Everywhere |
| Opened checkout and the ACP feed | Issue each agent platform its own API key | Settings, API keys |

Most steps take a few minutes. Waiting for DNS and the certificate usually takes the longest.

## What this demo does not do

- **No real products or customers.** Every product, price, store and address is synthetic, and each product description starts with "Synthetic demo product".
- **No money moves.** The demo has no payment method, so no checkout gets a payment link, and agent-executed payment is off.
- **No shipping rates.** The demo sets no shipping fee, so buyers choose store pickup. A real shop gets its rates from Shopify, or sets a flat fee on Sell Everywhere. The demo is for discovery, search and checkout decisions.
- **No store-location search yet.** Store locations are part of UCP 2026-08-25. The shop serves UCP 2026-04-08, so a location search answers that the feature is not served.

## Read next

- [Merchant setup checklist](/docs/oacp-merchant-setup)
- [Your own domain for buyer agents](/docs/oacp-custom-domains)
- [A purchase, step by step](/docs/oacp-buyer-journey)
- [What OACP is and why you need it](/docs/oacp-overview)
