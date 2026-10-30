```summary
Sell everywhere puts your shop on every AI surface the AgenticOrg network reaches, with one switch.
Buyers then find you in ChatGPT, Claude and other AI apps, in the AgenticOrg shopping bot on Telegram, and on the open web.
You do no setup on each surface: AgenticOrg connects to them once, for every shop on the network.
If you have no web address for buyer agents yet, AgenticOrg gives you one, such as your-shop.agenticorg.ai.
You stay the seller. Buyers pay on your own Shopify checkout or Plural page, and you can pause it at any time.
```

## One switch, every surface

```figure
sell-everywhere-network | One switch, every surface | You turn it on once. AgenticOrg lists your shop and connects it to each surface. Buyers finish and pay with you.
```

| Surface | How buyers reach you | What you do |
| --- | --- | --- |
| ChatGPT, Claude and other AI apps | They add AgenticOrg's server, `https://mcp.agenticorg.ai/mcp`, once as a custom MCP server. They can then list the network's shops, search them, open or update a checkout and get the payment link | Nothing |
| Telegram | They tap your link, `t.me/<bot>?start=<your-shop>`, or search across shops in the AgenticOrg shopping bot. The page shows your exact link | Share your link if you like |
| The open web | AI search engines and agents read your shop's profile at your web address | Nothing |
| Google Gemini and AI Mode, Microsoft Copilot, Perplexity | Coming: AgenticOrg is registering with each program for the whole network | Nothing yet |
| Payment | When the checkout is ready, buyers get a link to your own payment page and pay you there; see [Getting paid](/docs/oacp-getting-paid) | Choose Shopify checkout or Plural once |

## Watch it end to end

The recording below shows each step on this page for a demo coffee shop. It then follows a buyer's AI agent as it shops there and the buyer pays on the shop's Plural page.

```video
oacp-commerce-walkthrough | A real OACP order, end to end | Recorded on the live service with a demo shop and a Plural sandbox payment. A merchant sets up the shop in Sell everywhere. A buyer's AI agent finds it through the AgenticOrg MCP server, builds the order and gets a payment link. The buyer pays the merchant on the merchant's Plural page, and the order appears on the Orders page. Narrated, with English captions.
```

## Set up and turn it on

Everything happens on one page: **Sell Everywhere** (`/dashboard/sell-everywhere`). It takes about ten minutes.
Each step shows **Done** or **To do**. The switch stays off until your catalog, your policies, your shipping
fee (for a shop without Shopify) and your order limits are done; the page names whatever is missing.
Payments is not needed to turn it on, but until you choose a method buyers get no payment link and are told
to pay you directly.

```flow
Your shop | Choose your shop, or type a shop name and select Create shop. AgenticOrg gives it an id; you never type one.
Your catalog | Connect Shopify, or upload a CSV or JSON file. This is what buyers will search.
Your policies | Write your returns and shipping terms, and store pickup if you offer it. Without Shopify, also set your shipping fee, its currency and the countries you ship to. Buyer agents read your policies before they buy.
Order limits | Set the largest single order, the most per day, the most items per order and your currency. Nothing is set for you.
Payments | Choose where buyers pay: your own Shopify checkout or your own Plural account. See Getting paid.
Turn on Sell everywhere | Choose your shop name, accept the network terms and turn it on. AgenticOrg enrols you with Grantex, gives you a web address, publishes your shop and has your catalog facts signed.
```

### Connect Shopify

AgenticOrg reads your products with a custom app you create in your own store. It only reads products,
prices and stock.

1. In your Shopify admin, open **Settings**, then **Apps and sales channels**, then **Develop apps**.
2. Create an app, open **Configuration** and allow the Admin API scopes `read_products` and `read_inventory`.
3. Install the app, open **API credentials** and reveal the Admin API access token. Shopify shows it once.
4. On the Sell Everywhere page, enter your store address (`your-store.myshopify.com`) and the token, then
   select **Connect Shopify**. AgenticOrg checks the token with one read, stores it encrypted and reads your
   catalog.

To keep products current, the page shows your own webhook address once you are connected. In Shopify,
open **Settings**, **Notifications**, **Webhooks**, create a **Product update** webhook in JSON to that
address, then paste the signing secret Shopify shows there and connect again. AgenticOrg checks every
delivery with your secret; a changed product shows **Changed in your store** until the catalog is read
again.

### Upload a file instead

One product per row, one currency, up to 2,000 products:

| Column | Required | Example |
| --- | --- | --- |
| `sku` | Yes | `TEA-1` (letters, digits, dots, hyphens, underscores) |
| `title` | Yes | `Nilgiri green tea` |
| `description` | No | `Loose leaf, 100 g` |
| `price` | Yes | `499.00` |
| `currency` | Yes | `INR` |
| `quantity` | Yes | `12` (stock you have) |
| `image_url` | No | `https://cdn.merchant.example/tea-1.png` |
| `url` | No | `https://merchant.example/tea-1` |

A JSON file is a list of objects with the same keys. If a row has a problem, nothing is stored and the
page lists each row and column to fix. Your catalog is recorded as uploaded by you, never as coming from
a store system. Upload again whenever your products change.

### Shipping fee

A shop that uploads its catalog sets one shipping fee per order in the policies step, with its currency and
the countries it ships to (two-letter codes such as `IN`; leave them empty to ship everywhere). Enter 0 to
ship free. Buyers are offered this fee at checkout, and Sell everywhere stays off until it is set. A
Shopify shop uses the shipping rates of its Shopify delivery profiles instead.

### Store pickup

The pickup text is part of your policies: buyer agents read it as wording. Checkout offers store pickup
only when your shop also has a store location in its merchant settings, which the Commerce Runtime page
sets. Sell Everywhere does not add store locations, so a shop that only uploads a catalog offers shipping
and no pickup.

### Order limits

AI agents can only start orders within your limits: the largest single order, the most per day and the
most items per order, in your currency. The daily maximum must be at least the per-order maximum.

Before a buyer gets a payment link, the arbiter checks the order against these limits. The daily maximum
counts what buyers paid today and the payment links still open, in that currency, by the UTC day. An
order that would go over it gets no link.

## After you turn it on

The page shows how going live is progressing:

| Row | Status | Meaning |
| --- | --- | --- |
| Grantex enrolment | Enrolled | Grantex signs your catalog facts. No operator edit is needed for your shop. |
| Grantex enrolment | Waiting | Your deployment's Grantex is not ready for enrolment yet; nothing to do. |
| Grantex enrolment | Needs attention | Grantex refused or revoked the enrolment, or could not be reached. The message says whether to retry or contact your platform administrator. |
| Shop profile | Published | Buyer agents can read your shop at your web address. |
| Shop profile | Not published | The page says exactly why, for example that your web address is still being set up. |
| Verified catalog | Done | Grantex has verified your catalog, prices and stock. |
| Verified catalog | To do | Grantex has not verified your catalog yet. Select Publish to try again. |

Grantex's signed facts for your prices and stock last only minutes. AgenticOrg renews them whenever a buyer's agent reads your shop, and each time you open this page, so **Done** means Grantex verifies your catalog now, not only when you turned the shop on.

Select **Publish** (or **Refresh listing** once your profile is published) to run the steps again at any
time: Grantex enrolment, your web address, publication and the signed catalog facts. Each row then shows
the exact reason if something still waits.

Your shop name is the short name buyers see in AI apps and in your Telegram link, such as `nilgiritea`. Use 3 to 40 lowercase letters, digits or hyphens. It cannot change later, so choose it with care.

The network terms say that AgenticOrg lists your shop and opens checkouts for buyers who find it, and that you remain the seller and take payment yourself.

## Your web address

Buyer agents read your shop's profile at `https://<your address>/.well-known/ucp`.

- **You already have an address.** If you connected your own domain in Custom Domains, or your profile is already published, the network uses it.
- **You have no address yet.** AgenticOrg gives you `<your-shop>.agenticorg.ai` and sets it up for you, certificate included. It usually goes live within an hour.

AgenticOrg can give out 20 of these addresses in all, and the page shows how many are in use. If they are taken, you can still turn on Sell everywhere and connect your own domain instead; see [Your own domain for buyer agents](/docs/oacp-custom-domains).

Your shop appears on the network only once its profile is published at that address and the address is live, certificate included. Buyer agents read your profile and search your catalog there, so a shop whose address is still being set up would only give them errors. Until then, the AI apps and Telegram rows show **Waiting for your web address**, or **Waiting for publication** if the profile is not published yet. A new `agenticorg.ai` address usually takes a few minutes, and at most an hour.

## Where buyers can find you

The page lists each surface and where it stands:

| Status | Meaning |
| --- | --- |
| Live | Buyers can reach you there now. For Payments: you chose where buyers pay. |
| Waiting for DNS, Setting up, Issuing certificate | Your web address is being connected. Check back in a few minutes. |
| Waiting for your web address | You are on the network and your profile is published, but your web address is still being set up. AI apps and the Telegram bot offer your shop as soon as it is live. |
| Waiting for publication | You are on the network, but your profile is not published yet. |
| Coming soon | Telegram only: the shopping bot is not reachable for your shop yet, for example because your profile is not published. |
| Coming: registration in progress | AgenticOrg is still registering with that program. |
| Off | Sell everywhere is paused or not turned on. For Payments: you have not chosen where buyers pay. |

To see what buyers see in ChatGPT, follow [the ChatGPT run in the Nilgiri Tea House example](/docs/oacp-example-nilgiri-tea).

## Pause or resume

Choose **Pause** on the page. Your shop leaves the directory at once: AI apps and the Telegram bot stop offering it, and new network checkouts are refused. Your web address and your shop name stay yours. Choose **Resume Sell everywhere** to come back.

## What stays with you

- **Payment.** AgenticOrg never takes the buyer's money. A buyer opens a checkout in the chat; when it is ready, the buyer gets a link to your own Shopify checkout or Plural payment page and pays you there. See [Getting paid](/docs/oacp-getting-paid).
- **Your rules.** Every network checkout goes through the same checks as any other: your signed facts, your limits and the arbiter's fixed rules, recorded in your ledger.
- **Your data.** The directory shows your shop name, display name, categories, web address, profile address and Telegram link, nothing else.

Agents never pay on a buyer's behalf here. The AI apps and the Telegram bot have no tool that pays, and a
network Complete that carries a payment instrument, decision or mandate is refused.

## Read next

- [Getting paid: buyers pay on your own page](/docs/oacp-getting-paid)
- [Merchant setup checklist](/docs/oacp-merchant-setup)
- [Your own domain for buyer agents](/docs/oacp-custom-domains)
- [Example: Nilgiri Tea House, end to end](/docs/oacp-example-nilgiri-tea)
- [Feature switches](/docs/oacp-feature-switches)
