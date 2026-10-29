```summary
Sell everywhere puts your shop on every AI surface the AgenticOrg network reaches, with one switch.
Buyers then find you in ChatGPT, Claude and other AI apps, in the AgenticOrg shopping bot on Telegram, and on the open web.
You do no setup on each surface: AgenticOrg connects to them once, for every shop on the network.
If you have no web address for buyer agents yet, AgenticOrg gives you one, such as your-shop.agenticorg.ai.
You stay the seller. Buyers always finish and pay with you, and you can pause it at any time.
```

## One switch, every surface

```figure
sell-everywhere-network | One switch, every surface | You turn it on once. AgenticOrg lists your shop and connects it to each surface. Buyers finish and pay with you.
```

| Surface | How buyers reach you | What you do |
| --- | --- | --- |
| ChatGPT, Claude and other AI apps | They add AgenticOrg's server once and can then shop every network shop | Nothing |
| Telegram | They tap your link, `t.me/<bot>?start=<your-shop>`, or search across shops in the AgenticOrg shopping bot | Share your link if you like |
| The open web | AI search engines and agents read your shop's profile at your web address | Nothing |
| Google Gemini and AI Mode, Microsoft Copilot, Perplexity | Coming: AgenticOrg is registering with each program for the whole network | Nothing yet |
| Payment | When the checkout is ready, buyers get a link to your own payment page and pay you there; see [Getting paid](/docs/oacp-getting-paid) | Choose Shopify checkout or Plural once |

## Set up and turn it on

Everything happens on one page: **Commerce > Sell Everywhere**. It takes about ten minutes. Each step shows
**Done** or **To do**, and the switch stays off until the steps before it are done.

```flow
Your shop | Choose your shop, or type a shop name and select Create shop. AgenticOrg gives it an id; you never type one.
Your catalog | Connect Shopify, or upload a CSV or JSON file. This is what buyers will search.
Your policies | Write your returns and shipping terms, and store pickup if you offer it. If you uploaded a file, also set your shipping fee. Buyer agents read them before they buy.
Order limits | Set the largest single order, the most per day, the most items per order and your currency. Nothing is set for you.
Payments | Choose where buyers pay: your own Shopify checkout or your own Plural account. See Getting paid.
Turn on Sell everywhere | Choose your shop name, accept the network terms and turn it on. AgenticOrg enrols you with Grantex, gives you a web address and publishes your shop.
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

### Order limits

AI agents can only start orders within your limits: the largest single order, the most per day and the
most items per order, in your currency. The daily maximum must be at least the per-order maximum.

## After you turn it on

The page shows how going live is progressing:

| Row | Status | Meaning |
| --- | --- | --- |
| Grantex enrolment | Enrolled | Grantex signs your catalog facts. |
| Grantex enrolment | Waiting | Your deployment's Grantex is not ready for enrolment yet; nothing to do. |
| Grantex enrolment | Needs attention | The enrolment was refused or revoked; contact your platform administrator. |
| Shop profile | Published | Buyer agents can read your shop at your web address. |
| Shop profile | Not published | The page says exactly why, for example that your web address is still being set up. |
| Verified catalog | Done | Grantex has verified your catalog, prices and stock. |

Select **Publish** (or **Refresh listing**) to run these steps again at any time.

Your shop name is the short name buyers see in AI apps and in your Telegram link, such as `nilgiritea`. Use 3 to 40 lowercase letters, digits or hyphens. It cannot change later, so choose it with care.

The network terms say that AgenticOrg lists your shop and opens checkouts for buyers who find it, and that you remain the seller and take payment yourself.

## Your web address

Buyer agents read your shop's profile at `https://<your address>/.well-known/ucp`.

- **You already have an address.** If you connected your own domain in Custom Domains, or your profile is already published, the network uses it.
- **You have no address yet.** AgenticOrg gives you `<your-shop>.agenticorg.ai` and sets it up for you, certificate included. It usually goes live within an hour.

AgenticOrg can give out 20 of these addresses in all. If they are taken, you can still turn on Sell everywhere and connect your own domain instead; see [Your own domain for buyer agents](/docs/oacp-custom-domains).

Your shop appears on the network only once its profile is published at that address. Until then, the page shows **Waiting for publication**.

## Where buyers can find you

The page lists each surface and where it stands:

| Status | Meaning |
| --- | --- |
| Live | Buyers can reach you there now. |
| Setting up, Issuing certificate | Your web address is being connected. Check back in a few minutes. |
| Waiting for publication | You are on the network, but your profile is not published yet. |
| Coming: registration in progress | AgenticOrg is still registering with that program. |
| Off | Sell everywhere is paused or not turned on. |

To see what buyers see in ChatGPT, follow [the ChatGPT run in the Nilgiri Tea House example](/docs/oacp-example-nilgiri-tea).

## Pause or resume

Choose **Pause** on the page. Your shop leaves the directory at once: AI apps and the Telegram bot stop offering it, and new network checkouts are refused. Your web address and your shop name stay yours. Choose **Resume Sell everywhere** to come back.

## What stays with you

- **Payment.** AgenticOrg never takes the buyer's money. A buyer opens a checkout in the chat and finishes and pays with you.
- **Your rules.** Every network checkout goes through the same checks as any other: your signed facts, your limits and the arbiter's fixed rules, recorded in your ledger.
- **Your data.** The directory shows your shop name, display name, categories and profile address, nothing else.

## Read next

- [Merchant setup checklist](/docs/oacp-merchant-setup)
- [Your own domain for buyer agents](/docs/oacp-custom-domains)
- [Example: Nilgiri Tea House, end to end](/docs/oacp-example-nilgiri-tea)
- [Feature switches](/docs/oacp-feature-switches)
