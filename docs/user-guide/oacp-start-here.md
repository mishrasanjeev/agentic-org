```summary
AI assistants such as ChatGPT and Claude can now shop for people. AgenticOrg lets your shop sell to them.
You set up once: add your products from Shopify or a spreadsheet, write your rules, set your limits and choose where you get paid.
A buyer asks their assistant for something. It finds your shop, fills the cart and asks for a payment link.
The buyer pays you on your own payment page. The assistant can never pay by itself.
You get the order with the delivery address. The video below shows one real order, start to finish.
```

## Watch one real order

This recording was made on the live AgenticOrg service with a demo coffee shop, a demo buyer and a Plural test payment. Every screen is the real product, and every answer from the shop's side comes live from AgenticOrg. The buyer's assistant is shown as a simple console that makes the same calls an AI app makes.

```video
oacp-commerce-walkthrough | One real order, start to finish | A shop owner sets up a demo coffee shop: products from a spreadsheet (a Shopify store connects in one step instead), rules, a shipping fee, limits and their own Plural account. A buyer's AI assistant finds the shop, fills the cart and gets a payment link. The buyer pays the shop, and the order reaches the shop owner with the delivery address. Narrated, with English captions.
```

## How it works, in three parts

### 1. You set up your shop once

It takes about ten minutes, on one page in AgenticOrg: **Sell everywhere** (`/dashboard/sell-everywhere`).

1. **Create your shop.** Give it a name.
2. **Add your products.** Pick the way that suits you:
   - **On Shopify?** Connect your store. In your Shopify admin, create an app that may read products and stock, then paste your store address and its access token. AgenticOrg reads your products, prices and stock by itself and never changes them.
   - **Not on Shopify?** Upload a spreadsheet (a CSV file) with your products, prices and stock.
3. **Write your rules.** Your returns and delivery policy. Without Shopify, also set a shipping fee; Shopify shops use their Shopify shipping rates.
4. **Set your limits.** The biggest single order, the most per day and the most items in one order. An AI assistant can never go over them. Nothing is set for you.
5. **Choose where you get paid.** Your own Shopify checkout, or your own Plural account.
6. **Turn it on.** AgenticOrg gives your shop a web address, such as `your-shop.agenticorg.ai`, and Grantex checks your products, prices and stock. Once that address is live, usually within minutes and at most an hour, buyers' assistants can find you.

```figure
sell-everywhere-network | One switch, every surface | You turn it on once. AgenticOrg lists your shop and connects it to each surface. Buyers finish and pay with you.
```

### 2. A buyer shops through their AI assistant

- The buyer connects their assistant to AgenticOrg once: ChatGPT, Claude or another app that can add an MCP server, using `https://mcp.agenticorg.ai/mcp`. Or they open the AgenticOrg shopping bot on Telegram.
- They ask in their own words, for example: "Two packs of filter coffee, delivered to Bengaluru."
- The assistant finds your shop, picks the product, adds the delivery address and the shipping, and asks for a payment link.
- AgenticOrg checks the order against your limits and the facts Grantex checked, records the decision, and only then hands back your payment link. If something is off, for example the order is over your limit, no link is given and the reason is recorded.

### 3. The buyer pays you, and you get the order

```figure
getting-paid-handoff | The buyer pays on your page | When the checkout is ready, AgenticOrg checks your shop's facts and limits, then the buyer gets your payment link and pays you there. AgenticOrg never takes the money.
```

- The buyer pays on **your** page: your Shopify checkout, or a Plural payment page under your own Plural account (card, UPI or net banking). They confirm the payment there themselves.
- A Plural order appears on your **Orders** page (`/dashboard/orders`) with the items, the amount and the delivery address, and your administrators get an email. A Shopify order appears in your Shopify admin, like any other.
- You pack and ship it as usual.

## Who does what

| Who | Does | Never does |
| --- | --- | --- |
| You, the shop owner | Add products, write rules, set limits, choose where you get paid, ship the order | Give up the sale: you stay the seller |
| The buyer | Asks for what they want, checks the order and pays you | Share card or UPI details with the assistant |
| The buyer's AI assistant | Finds your shop, fills the cart, asks for a payment link | Pay on its own or go over your limits |
| AgenticOrg | Lists your shop, checks every order against your limits, records every decision | Hold the buyer's money |
| Grantex | Checks and signs your products, prices and stock | Sell anything or take payments |

## What you need

- An AgenticOrg account where you can open **Sell everywhere**.
- Your products: a Shopify store, or a spreadsheet.
- A way to get paid: your Shopify store, or a Plural account. Without one, buyers are told to finish the purchase with you directly.

## Quick answers

**Do I need Shopify?** No. A spreadsheet works just as well. Shopify only saves you typing, and lets buyers pay in your own Shopify checkout.

**Can the AI assistant spend money without the buyer?** No. It can only ask for a payment link. The buyer opens the link and pays you themselves.

**Where does the money go?** Straight to you, through your own Shopify checkout or Plural account. AgenticOrg never touches it.

**Can I stop selling this way?** Yes. Choose **Pause** on the Sell everywhere page and your shop leaves the network at once. Your shop name and web address stay yours.

**Am I in Google Gemini, Microsoft Copilot or Perplexity?** Not yet. AgenticOrg is registering with each of them for every shop on the network; the Sell everywhere page shows each one as it comes.

## Go deeper

- [Sell everywhere: one switch for every AI surface](/docs/oacp-sell-everywhere): every step of the setup page.
- [Getting paid: buyers pay on your own page](/docs/oacp-getting-paid): Shopify checkout, Plural, the Orders page and the email.
- [What OACP is and why you need it](/docs/oacp-overview): the trust layer underneath, for the curious.
- [How OACP works, end to end](/docs/oacp-how-it-works): how your facts are checked, decided and recorded.
- [Example: Nilgiri Tea House](/docs/oacp-example-nilgiri-tea): a live example shop you can try in two minutes.
- [OACP questions and answers](/docs/oacp-faq).
