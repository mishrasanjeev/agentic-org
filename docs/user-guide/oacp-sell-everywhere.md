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
| Google Gemini and AI Mode, Microsoft Copilot, Perplexity | AgenticOrg registers the whole network with each program, which then reads your shop's own pages and product feed at your web address. The page shows where each one stands for your shop | Keep a price, stock and an image on each product |
| Payment | When the checkout is ready, buyers get a link to your own payment page and pay you there; see [Getting paid](/docs/oacp-getting-paid) | Choose Shopify checkout or Plural once |

## Watch it end to end

The recording below shows each step on this page for a demo coffee shop. It then follows a buyer's AI agent as it shops there and the buyer pays on the shop's Plural page.

```video
oacp-commerce-walkthrough | A real OACP order, end to end | Recorded on the live service with a demo shop and a Plural test payment. The shop owner adds products (a Shopify store connects through a read-only app in its Shopify admin; the demo shop uploads a spreadsheet), sets rules, limits and their own Plural account, and turns Sell everywhere on. A buyer's AI assistant finds the shop through the AgenticOrg MCP server, fills the cart and gets a payment link. The buyer pays the shop, and the order reaches the Orders page with the delivery address. Narrated, with English captions.
```

## Set up and turn it on

Everything happens on one page: **Sell Everywhere** (`/dashboard/sell-everywhere`). It takes about ten minutes.
Steps 1 to 4 show **Done** or **To do**; the catalog step can also show **Read your catalog** (your Shopify
store is connected but not read yet) or **Changed in your store**. Payments has no badge, and step 6 shows
**Live** once your shop is on. The switch stays off until your catalog, your policies, your shipping
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
address, then paste the signing secret Shopify shows there and select **Connect Shopify** again. Leave the
access token blank to keep the one you saved: Shopify shows a token only once. AgenticOrg checks every
delivery with your secret; a changed product shows **Changed in your store** until the catalog is read
again.

While your shop is on the network, AgenticOrg reads your Shopify catalog again by itself: usually within ten
minutes of a **Product update** webhook, and about once an hour without one (a little later when many shops are
due at once, or an hour after a read that failed). **Read catalog again** on the page reads it at once. Each read
replaces the one before: a product you removed from your store is no longer offered.

One read takes at most 1,000 products (20 pages of 50). If your store has more, only the first 1,000 that
Shopify returns are offered to buyers. The page says so after a read that stopped at the limit, and shows a
note while your catalog is at the limit.

**Product details from Shopify.** Along with titles, prices and stock, AgenticOrg reads each product's tags,
its barcode, its page on your online store and the product metafields you show on your storefront. In
Shopify, a metafield is shown on the storefront when its definition (**Settings**, **Custom data**,
**Products**) has storefront access turned on; metafields without it stay private. Buyers' AI assistants
read each one under its definition name, as something your shop declares. Text, numbers, dates, yes/no,
links starting with https://, ratings, weights, volumes, sizes and money amounts are read; references to
files, other products or metaobjects are not, and the first 20 metafields of each product are read. Metafields and tags whose names read as private business data
or credentials are left out, and so are barcodes without a valid check digit.

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
| `brand` | No | `Nilgiri Estates` |
| `category` (or `product_type`) | No | `Green tea` |
| `tags` | No | `tea; green tea; loose leaf` (separate with `;`, `,` or `\|`) |
| `mrp` (or `compare_at_price`) | No | `549.00` (shown as the list price when it is higher than `price`) |
| `barcode` (or `gtin`) | No | The 8, 12, 13 or 14 digit number under the barcode |

**Any other column is a product detail.** Add the columns your buyers care about, such as `Ingredients`,
`Allergens`, `Shelf life`, `Storage`, `Weight`, `Country of origin`, `Certifications`, `FSSAI licence number`
or `Lab report` (a link starting with https://). Buyers' AI assistants read each one, under your column name,
as something your shop declares. Up to 30 detail columns, each value up to 1,000 characters; empty cells are
left out. A column whose name starts with `_` is skipped, so you can keep your own notes, such as
`_cost_price`, in the same file. Names that read as private business data (cost, margin, profit, supplier and
the like) are refused, so they are never shown to buyers by mistake. The setup page has an example file to
download.

A JSON file is a list of objects with the same keys; a product can also carry an `attributes` object of
extra details. If a row has a problem, nothing is stored and the
page lists each row and column to fix. Your catalog is recorded as uploaded by you, never as coming from
a store system. Upload again whenever your products change.

A file can also be stored with warnings: products with no `image_url`, no `description` or no `barcode`.
They are still offered to buyers, and the page lists the first rows of each so you can fill them in later.

### Catalog check

Under **Your catalog** the page checks the catalog AgenticOrg last read (your Shopify store or your file)
and lists what to fix, with the number of products or variants, a few examples and how to fix each in
Shopify or in your file. Only you see this check. The network never ranks or hides shops by it, and
search order stays random.

These variants are **not offered to buyers** until you fix them:

| Problem | Why |
| --- | --- |
| No usable price | A variant with no price, a price that is not a number, or no currency is never listed and never checks out. |
| Stock below zero | Shown as out of stock, so it cannot be ordered. Correct the count in Shopify. |

These are offered, but buyers or their AI assistants get less to go on:

| Warning | What it means |
| --- | --- |
| SKU used by more than one variant | The variants are still offered under their own item ids, but the SKU names no one item: a buyer's AI app that asks for that SKU is told it was not found, and a price or stock you set per SKU does not apply to them. Give every variant its own SKU. |
| No SKU | Orders are harder to match to your own records. |
| Stock not tracked | Shopify does not track the quantity, so buyers cannot be told whether it is in stock. Tick **Track quantity** under Inventory in Shopify. |
| Out of stock | Listed as out of stock until you restock it. |
| No image, no description | Buyers' AI assistants show no photo, or have only the title. |
| No barcode (GTIN) | The product cannot be matched to the same product at other shops. |
| Very short title | Harder to find by search. A title such as brand, product and size works best. |

Select **Read catalog again** after a fix in Shopify; upload the file again after a fix in your file.

### Shipping fee

A shop that uploads its catalog sets one shipping fee per order in the policies step, with its currency and
the countries it ships to (two-letter codes such as `IN`; leave them empty to ship everywhere). Enter 0 to
ship free. Buyers are offered this fee at checkout, and Sell everywhere stays off until it is set. A
Shopify shop uses the shipping rates of its Shopify delivery profiles instead.

A Shopify shipping zone that covers only some states or provinces of a country is followed too: a buyer's
address must name a state in that zone, by code or name (`KA` or `Karnataka`; accents do not matter). When
the zone covers only some states and the address has none, the buyer's AI app is asked for the state before
any shipping option is offered. A zone that covers the whole country serves every address there.

### Delivery area

A local shop can deliver only to some postal codes. In the policies step, enter the start of the postal
codes you deliver to under **Delivers only to postal codes starting with**, separated by commas. For
example, `5600, 5601` covers Indian PIN codes 560001 to 560199. Up to 50 prefixes, each up to 10 letters or
digits; spaces and hyphens are ignored. Leave it empty to deliver wherever your shipping rates reach.

UK postcodes: a prefix of letters and a district number, such as `SW1` or `E1`, covers that district only.
`SW1` covers SW1A and SW1P postcodes but not SW10 to SW19, and `E1` does not cover E10. A prefix of letters
only, such as `SW`, covers the whole postcode area.

With a delivery area set, the checkout checks the buyer's postal code before it offers shipping, for
Shopify rates and your own fee alike:

- **Outside the area.** No shipping option is offered and no payment link is issued. The buyer's AI app
  is told, for example "This shop delivers only to postal codes starting with 5600 or 5601; 570001 is
  outside it."
- **No postal code in the address.** The buyer's AI app is asked for it first.
- **Store pickup** is not affected.

### Store pickup

The pickup text is part of your policies: buyer agents read it as wording. Checkout offers store pickup
only when your shop also has a store location in its merchant settings, which the Commerce Runtime page
sets. Sell Everywhere does not add store locations, so a shop that only uploads a catalog offers shipping
and no pickup.

### Shop details

Some facts hold for the whole shop, such as a food-safety licence number, a certification or where your
products are made. Add them once under **Shop details** in the policies step, as a name and a value, for
example `FSSAI licence number` and the number. Buyers' AI assistants read them with every product, marked as
declared by your shop, and the Telegram shopping bot shows them too. Up to 10 shop details, each name up to
40 characters and each value up to 500. They are checked as product details are: names that read as private business data are
refused, and links must start with https://. Details for one product go in a column of your catalog, or in a
Shopify metafield, instead.

### Order limits

AI agents can only start orders within your limits: the largest single order, the most per day and the
most items per order, in your currency. The daily maximum must be at least the per-order maximum.

Before a buyer gets a payment link, the arbiter checks the order against these limits. The daily maximum
counts, in that currency: the payments confirmed today (by the UTC day), the payment links still open
whichever day they were issued, and every payment link issued today, even after it lapses or its checkout
is canceled, because the buyer can still pay it. A link stops counting when its payment becomes an order
(the order then counts as paid) or when Plural reports the payment failed. Shopify does not tell AgenticOrg when a buyer
pays, so a Shopify link counts all the day it was issued. An order that would go over the daily maximum
gets no link.

## After you turn it on

The page shows how going live is progressing:

| Row | Status | Meaning |
| --- | --- | --- |
| Grantex enrolment | Enrolled | Grantex signs your catalog facts. No operator edit is needed for your shop. |
| Grantex enrolment | Waiting | Your deployment's Grantex is not ready for enrolment yet; nothing to do. |
| Grantex enrolment | Needs attention | Grantex refused or revoked the enrolment, or could not be reached. The message says whether to retry or contact your platform administrator. |
| Shop profile | Published | Your profile is published on your web address. Buyer agents can read it once the **Your web address** row shows **Live**. |
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

Your shop appears on the network only once its profile is published at that address and the address is live, certificate included. Buyer agents read your profile and search your catalog there, so a shop whose address is still being set up would only give them errors. While the profile is published but the address is still being set up, the AI apps and Telegram rows show **Waiting for your web address**. Before the profile is published, the AI apps row shows **Waiting for publication** and the Telegram row shows **Coming soon**. A new `agenticorg.ai` address usually takes a few minutes, and at most an hour.

## Where buyers can find you

The page lists each surface and where it stands:

| Status | Meaning |
| --- | --- |
| Live | Buyers can reach you there now. For Payments: you chose where buyers pay. |
| Waiting for DNS, Setting up, Issuing certificate | Your web address is being connected. Check back in a few minutes. |
| Waiting for your web address | You are on the network and your profile is published, but your web address is still being set up. AI apps and the Telegram bot offer your shop as soon as it is live. |
| Waiting for publication | You are on the network, but your profile is not published yet. |
| Coming soon | Telegram only: the shopping bot is not reachable for your shop yet, for example because your profile is not published. |
| Coming: AgenticOrg has not registered yet | Your shop qualifies; AgenticOrg has not registered the network with that program yet. |
| Submitted, waiting for approval | AgenticOrg has applied to that program for the network (or for your shop) and is waiting for its answer. |
| Not eligible yet | Your shop does not qualify yet. The row says why: Sell everywhere is off, your web address is not live yet, Grantex has not verified your catalog yet, or no product has a price, a stock figure and an image. |
| Off | Sell everywhere is paused or not turned on. For Payments: you have not chosen where buyers pay. |

The ChatGPT, Claude and other AI apps row also shows where the AgenticOrg app stands in the ChatGPT app
directory. While it is not listed there, buyers add the server address themselves.

To see what buyers see in ChatGPT, follow [the ChatGPT run in the Nilgiri Tea House example](/docs/oacp-example-nilgiri-tea).

## Your shop pages and product feed

Once your shop is on the network and your web address is live, the address also serves a small shop
site built from your catalog, for search engines, shopping programs and AI agents:

| Address | What it shows |
| --- | --- |
| `https://<your address>/` | Your shop name, your products with price and stock, your shipping and returns text, and how to buy |
| `https://<your address>/products/...` | One page per product: price, stock, images, description, your product details, shipping and returns, and the product's structured data (barcode and shipping included when you gave them) |
| `https://<your address>/feeds/google.xml` | Your product feed, in the format Google Merchant Center and Microsoft Merchant Center read |
| `https://<your address>/sitemap.xml`, `/robots.txt`, `/llms.txt` | The list of your pages for crawlers and AI agents |

Everything comes from your catalog as buyer agents see it: the newest read of each source (Shopify or
your upload), only while Grantex verifies your catalog, prices and stock. While it does not, the pages
answer "try again in a few minutes", so the programs keep what they already have.

The feed lists each product option with its price and stock. A product is left out of the feed when
it has no price, a price of zero, no stock figure or no image. Add the barcode (GTIN) where your products
have one; without it the feed says the product has no standard identifier. Your flat shipping fee goes in
the feed for the countries you named; Shopify shipping rates are set in the merchant center instead. A
used or refurbished item needs a product detail named `condition` with the value `used` or
`refurbished`; otherwise items are listed as new.

The product pages have no basket of their own. They link to your own store's product page when your
catalog gives one, to the AgenticOrg shopping bot on Telegram and to the MCP server address.

### How a search across shops is ordered

AgenticOrg does not rank shops, and no shop can pay to be listed first. When a buyer's AI app or the
Telegram bot searches the whole network, it asks a fresh random selection of shops in a fresh random
order every time, so no shop is always first. Within your shop, your products come best match first: a
search word that is a whole word of the product title counts most, then a whole word of your brand,
category or tags, then part of a word, then the description and product details. When several shops
sell the same product (the same barcode with a valid check digit), the answer also lists it together,
with each shop's price and stock for that item, lowest price first (in the currency most of those shops
use). Adding the barcode to your products lets a buyer's AI app see that you sell the same item as
another shop and compare the offers.

The buyer chooses the shop. A buyer can leave shops out of a search, or keep to a few: AI apps pass the
shops to leave out, and on Telegram the buyer sends `/avoid <shop>`. The other shops are still asked in a
fresh random order, and the buyer is never sent to a checkout at a shop they left out.

### When a price or stock changes

A buyer's AI app may build a cart now and ask for the payment link later. Before any payment link is
issued, AgenticOrg checks the cart against your catalog as it is at that moment:

- **A new price.** The checkout is re-priced at your current price, with the same delivery choice, and no
  link is issued yet. The buyer's AI app is told the new total and asks again once the buyer agrees.
- **Fewer in stock than the cart holds, or a product you no longer sell.** No link is issued. The buyer's AI
  app is told what is short and changes the quantity or the item.
- **Stock held by another buyer's payment.** With your Plural page, a payment link holds its items for the
  30 minutes it is valid. Orders paid through AgenticOrg since your catalog was last read count as sold. When
  the stock left after those is less than the cart needs, no link is issued. The buyer's AI app is told how
  many can be bought now and, when another buyer's pending payment holds the rest, when it frees up if that
  payment does not go through. A canceled checkout holds nothing. Two buyers asking for the last unit at the
  same moment are checked one after the other, so only one gets a link.
- **Loyalty.** When the checkout is re-priced and AgenticOrg can read the buyer's membership from your Shopify
  store, the points forecast is worked out again from the new total. If the store cannot be read at that
  moment, the forecast already shown is kept.

While an earlier payment link of the checkout can still be paid on your Plural page, the cart stays as that link
was issued, so a payment on it still becomes your order.

**Stock when the payment arrives.** The money is already taken when Plural reports a payment, so the order is
always created. AgenticOrg first checks your catalog again, less the orders paid through AgenticOrg since your
catalog was last read. If your stock no longer covers every item (for example, you sold the last one in your
own store meanwhile), the order is marked **Stock was short when the
payment arrived** on the Orders page, with the short items, and every administrator gets an email. Ship what
you can and refund the rest, or refund the order (see Refunds below).

So the price the buyer pays is your current price, as AgenticOrg last read your catalog: when you upload a
file, when you select **Read catalog again**, and on the automatic reads above. On your Shopify checkout, Shopify also
checks the price and stock itself.

### A payment with no order

On your Plural page, a buyer pays for the exact checkout the link was issued for. If a payment reaches your
Plural account and AgenticOrg cannot match it to that checkout (a different amount, a checkout that was
canceled or changed after the link was issued, another Plural order, or a second payment for a checkout the
buyer already paid through another of its links), no order is created for it and nothing more ships. Every administrator of your workspace gets one email that names the Plural order, the reason and
the buyer's contact as given. The money is in your own Plural account: refund it from AgenticOrg, on the
Orders page under **Payments with no order**, or in your Plural dashboard, or contact the buyer.

The email is kept until it is sent. If your mail server cannot take it, AgenticOrg tries again after one
minute, then after longer waits, for up to eight attempts. The new-order email works the same way. A payment
is emailed about once, however often Plural reports it.

### Refunds

- **Orders paid on your Plural page.** On the Orders page, choose **Refund** on the order, enter the amount
  (the whole order or part of it) and confirm. AgenticOrg refunds through your own Plural account, with the
  Plural credentials you saved. You can refund at most the order total less the refunds already made; a
  refund Plural has not confirmed yet counts as made. The order then shows the refund as refunded or
  pending. Only workspace administrators can refund. Each refund is decided by the commerce arbiter and
  recorded in your ledger. Choosing **Confirm refund** twice sends one refund, not two.
- **A refund Plural has not confirmed.** If Plural does not answer clearly (a timeout, a server error, a
  pending status), the refund shows as pending and counts as refunded, so it is never sent twice. Every 5
  minutes AgenticOrg asks Plural again and records the refund as refunded or failed. You can also choose
  **Check refund again** once a refund is 5 minutes old. Only a refund Plural refused is marked failed; you
  can then refund again. If Plural still does not say, the refund stays pending: check it in your Plural
  dashboard.
- **Payments with no order.** Choose **Refund** on the payment under **Payments with no order** and confirm.
  The whole amount the buyer paid is refunded once. A payment whose order was created while stock was short
  is refunded through that order. A refund Plural refused can be tried again.
- **Orders paid in your Shopify checkout.** AgenticOrg never sees these orders. Refund them in your Shopify
  admin.

## Pause or resume

Choose **Pause** on the page. New network checkouts are refused at once, and your shop leaves the directory: AI apps and the Telegram bot stop offering it within about two minutes, as their short-lived lists refresh. Your web address and your shop name stay yours. Choose **Resume Sell everywhere** to come back.

## What stays with you

- **Payment.** AgenticOrg never takes the buyer's money. A buyer opens a checkout in the chat; when it is ready, the buyer gets a link to your own Shopify checkout or Plural payment page and pays you there. See [Getting paid](/docs/oacp-getting-paid).
- **Your rules.** Every network checkout goes through the same checks as any other: your signed facts, your limits and the arbiter's fixed rules, recorded in your ledger.
- **Your data.** The directory shows your shop name, display name, categories, web address, profile address and Telegram link, nothing else. Your web address also shows your shop's pages and product feed, built from the catalog, policies and shop details you publish to buyers.

Agents never pay on a buyer's behalf here. The AI apps and the Telegram bot have no tool that pays, and a
network Complete that carries a payment instrument, decision or mandate is refused.

## Read next

- [Getting paid: buyers pay on your own page](/docs/oacp-getting-paid)
- [Merchant setup checklist](/docs/oacp-merchant-setup)
- [Your own domain for buyer agents](/docs/oacp-custom-domains)
- [Example: Nilgiri Tea House, end to end](/docs/oacp-example-nilgiri-tea)
- [Feature switches](/docs/oacp-feature-switches)
