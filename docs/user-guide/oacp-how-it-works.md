```summary
Your shop's facts travel through seven steps before an agent sees them.
The shop states the facts. Grantex signs them. AgenticOrg checks every signature before it keeps a copy.
When an agent wants to buy, the arbiter applies fixed rules and answers allow or refuse at once. Nobody waits for a person.
Every decision is recorded in the commerce ledger. Adapters then speak each standard, so every agent gets the same answer.
A payment runs only after an allow decision, and only once.
```

## The whole flow on one page

```figure
oacp-docs-flow | From your shop to every buyer agent | Seven steps: the shop states its facts, Grantex signs them, AgenticOrg verifies and keeps them, the arbiter decides allow or refuse, the ledger records the decision, adapters speak each standard, and buyer agents receive the same answer.
```

Each step has one owner. The next sections explain them one at a time.

## Step 1. Your shop states its facts

Your shop already knows its products, prices, stock and policies. AgenticOrg reads them. It does not invent them.

- Shopify is the supported source today. AgenticOrg reads it with read-only access.
- Your merchant settings add the rest: limits, stores, payment rail and policies.
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

The rules are listed in plain words in [Safety and trust](/docs/oacp-safety-and-trust#what-the-arbiter-checks).

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
- Intent, Cart and Payment mandates with AP2 field names;
- checkout tools for MCP clients, and a seller agent for A2A.

Each surface has its own switch, so you can turn on one channel at a time.

## Step 7. Buyer agents get the same answer

Because every channel reads the same signed facts, agents see the same price, the same stock and the same refusals. A payment provider moves money only after an allow decision, and only once.

## Who does what, at a glance

```flow
States the facts | Products, prices, stock and policies come from your shop. | Owner: Merchant | If blocked: Reconnect Shopify or fix the merchant settings, then sync again.
Signs the facts | Each fact becomes a signed, expiring, revocable artifact. | Owner: Grantex | If blocked: AgenticOrg keeps nothing new and asks for a fresh request.
Verifies and keeps | Signature, key, scope, expiry and revocation are checked before storing. | Owner: AgenticOrg | If blocked: The batch is refused with the exact refusal code.
Decides | Fixed rules answer allow or refuse at once. | Owner: AgenticOrg arbiter | If blocked: The refusal names the first failing rule.
Records | The decision and every commerce write go to the ledger. | Owner: AgenticOrg ledger | If blocked: A write route that skips its ledger row is answered as an error.
Pays | The provider charges once, against a consumed allow decision. | Owner: Payment provider | If blocked: Without a registered executor, checkout stops at executor_unavailable.
```

## Where to go deeper

- [A purchase, step by step](/docs/oacp-buyer-journey)
- [Safety and trust](/docs/oacp-safety-and-trust)
- Technical references: [artifact cache guide](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/artifact-cache-guide.md) and [arbiter and ledger](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/arbiter-ledger.md)
