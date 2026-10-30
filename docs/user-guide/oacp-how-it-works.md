```summary
Your shop's facts travel through seven steps before an agent sees them.
The shop states the facts. Grantex signs them. AgenticOrg checks every signature before it keeps a copy.
When an agent wants to buy, the arbiter applies fixed rules and answers allow or refuse at once. Nobody waits for a person.
Every decision is recorded in the commerce ledger. Adapters then speak each standard, so every agent gets the same answer.
A buyer gets a link to your own payment page only after an allow decision, and AgenticOrg never holds the money.
```

## The whole flow on one page

```figure
oacp-docs-flow | From your shop to every buyer agent | Seven steps: the shop states its facts, Grantex signs them, AgenticOrg verifies and keeps them, the arbiter decides allow or refuse, the ledger records the decision, adapters speak each standard, and buyer agents receive the same answer.
```

Each step has one owner. The next sections explain them one at a time.

## See the flow in a real order

The recording below runs the whole flow once on the live service: the shop states its facts, Grantex verifies them, the arbiter checks the order against the shop's limits, and the buyer gets the merchant's payment link.

```video
oacp-commerce-walkthrough | A real OACP order, end to end | Recorded on the live service with a demo shop and a Plural sandbox payment. A merchant sets up the shop in Sell everywhere. A buyer's AI agent finds it through the AgenticOrg MCP server, builds the order and gets a payment link. The buyer pays the merchant on the merchant's Plural page, and the order appears on the Orders page. Narrated, with English captions.
```

## Step 1. Your shop states its facts

Your shop already knows its products, prices, stock and policies. AgenticOrg reads them. It does not invent them.

- Shopify is read with read-only access, through a custom app in your own store. A shop without Shopify uploads its catalog as a CSV or JSON file instead, recorded as uploaded by you.
- Your merchant settings add the rest: order limits, your shipping fee, stores, where buyers pay and your policies.
- Other systems (WooCommerce, ERP, PIM and others) can be saved as settings. They stay "pending adapter" and are never called.

## Step 2. Grantex signs each fact

AgenticOrg sends the facts to Grantex and asks for authority. Grantex turns each fact into a signed record, called an artifact.

Every artifact carries:

- a signature (ES256) over the exact content;
- an expiry time, so old facts stop counting;
- a slot on a public revocation list, so a fact can be withdrawn.

Grantex publishes the keys that check these signatures, and the revocation list. Anyone can check an artifact with those two public things.

## Step 3. AgenticOrg checks before it keeps anything

AgenticOrg verifies every artifact before it stores a copy. This is the most important safety step.

```figure
oacp-verify-gate | Four checks before a fact is kept | Each artifact must pass the signature check, come from a published Grantex key, be in scope and unexpired, and be valid on the revocation list. Passing artifacts are kept with source and freshness labels. A failure keeps nothing from that batch.
```

In production, staging and preview, one failed artifact means nothing from that batch is kept. This is called strict mode, and it is on by default there.

## Step 4. The arbiter decides

When an agent wants to buy, the arbiter decides. It is a fixed set of rules, not a person.

- It answers **allow** or **refuse**. It never answers "pending".
- An allow is single use. It is bound to the exact cart, price and quantity, and it expires after 10 minutes.
- A refusal names the first rule that failed, so you know exactly why.

Today the decision comes before the payment link: the arbiter checks your signed facts and your order limits, including the daily maximum, and only an allow gives the buyer a link to your own Shopify checkout or Plural page. The rules are listed in plain words in [Safety and trust](/docs/oacp-safety-and-trust#what-the-arbiter-checks).

## Step 5. The ledger records it

Every commerce write and every decision is written to the commerce ledger in the same database transaction.

- Each row is signed.
- When audit-chain sealing is on, rows are also linked into a hash chain, so a removed or edited row shows up.
- You can export the ledger and check it on your own computer with `scripts/verify_ledger.py`.

Sealing and outside witnessing are separate switches. Both start off.

## Step 6. Adapters speak each standard

The same verified facts are rendered into each standard's own shape:

- a UCP business profile at `/.well-known/ucp`, with catalog, checkout and orders;
- an ACP product feed and ACP checkout sessions;
- Intent, Cart and Payment mandates with AP2 field names, for agent-executed payment, which is switched off;
- checkout tools for MCP clients, the AgenticOrg shopping bot on Telegram, and a seller agent for A2A.

Each surface has its own switch, so you can turn on one channel at a time.

## Step 7. Buyer agents get the same answer

Because every channel reads the same signed facts, agents see the same price, the same stock and the same refusals. Once you have chosen where buyers pay, every channel ends a ready checkout the same way: a link to your own payment page, where the buyer pays. AgenticOrg never holds the money.

## Who does what, at a glance

```flow
States the facts | Products, prices, stock and policies come from your shop. | Owner: Merchant | If blocked: Reconnect Shopify or upload your catalog again, fix the merchant settings, then read the catalog again.
Signs the facts | Each fact becomes a signed, expiring, revocable artifact. | Owner: Grantex | If blocked: AgenticOrg keeps nothing new and asks for a fresh request.
Verifies and keeps | Signature, key, scope, expiry and revocation are checked before storing. | Owner: AgenticOrg | If blocked: The batch is refused with the exact refusal code.
Decides | Fixed rules answer allow or refuse at once. | Owner: AgenticOrg arbiter | If blocked: The refusal names the first failing rule.
Records | The decision and every commerce write go to the ledger. | Owner: AgenticOrg ledger | If blocked: A write route that skips its ledger row is answered as an error.
Hands over the payment link | After a consumed allow, the buyer gets a link to your own Shopify checkout or Plural page and pays there. A confirmed Plural payment becomes an order. | Owner: AgenticOrg, then your payment page | If blocked: Without a payment method, the buyer gets no link and is told to pay with you directly.
```

## Where to go deeper

- [A purchase, step by step](/docs/oacp-buyer-journey)
- [Getting paid](/docs/oacp-getting-paid)
- [Safety and trust](/docs/oacp-safety-and-trust)
- Technical references: [artifact cache guide](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/artifact-cache-guide.md) and [arbiter and ledger](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/arbiter-ledger.md)
