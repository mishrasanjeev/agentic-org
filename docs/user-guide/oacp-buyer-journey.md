```summary
A buyer's agent finds a product, opens a checkout and asks to complete it.
Today the buyer pays on your own page: the arbiter checks your signed facts and your limits, then the buyer gets a link to your Shopify checkout or Plural payment page.
The buyer authenticates and pays there, for example with 3-D Secure or their UPI PIN. AgenticOrg never holds the money.
Agent-executed payment is built but switched off: Grantex Intent, Cart and Payment mandates, the buyer's passkey and RBI AFA for UPI AutoPay.
Every decision lands in the ledger, whichever way the buyer pays.
```

## A purchase today: the buyer pays on your page

The example uses a synthetic shop, Merchant Example, that has chosen where its buyers pay on the Sell Everywhere page.

```figure
getting-paid-handoff | The buyer pays on your page | The buyer's checkout is ready. The arbiter checks the shop's signed facts and limits and records its decision. The buyer gets a link to the shop's own Shopify cart or Plural payment page and pays there. The shop gets the order.
```

```flow
Find the product | The buyer's agent searches your catalog through UCP, the ACP feed, the AgenticOrg MCP server or the AgenticOrg bot on Telegram. Prices and stock come from verified facts. | Owner: Buyer agent | If blocked: Without fresh verified facts the catalog answers with no products.
Open a checkout | The agent opens a checkout and chooses shipping, or store pickup where your shop has store locations. AgenticOrg prices it from the verified catalog and your shipping rates and moves it to ready_for_complete. | Owner: Buyer agent and AgenticOrg | If blocked: An unknown or out-of-stock item, or no shipping option for the buyer's country, keeps the session incomplete.
Arbiter decides | Before any link exists, the arbiter checks your signed facts and your order limits, including the daily maximum. The allow is used once, for exactly this checkout. | Owner: AgenticOrg arbiter | If blocked: A refusal names the rule and the reason, for example daily_amount_cap_exceeded, and no link is issued.
Payment link | Complete answers requires_escalation with continue_url: your Shopify cart with the items in it, or a Plural payment page under your own Plural account. Asking again returns the same link while it is valid. | Owner: AgenticOrg | If blocked: payment_setup_incomplete means your payment method is not fully set up; payment_handoff_unavailable means the shop has none for this channel.
Buyer pays | The buyer opens the link and pays on your page by card, UPI or net banking, authenticating with 3-D Secure or their UPI PIN. | Owner: Buyer and your payment page | If blocked: A Plural link lasts 30 minutes; after that the buyer asks for a new one.
Order | Plural confirms the payment through its signed webhook or the check AgenticOrg runs every minute, and the checkout becomes an order on your Orders page, with an email to your administrators. A Shopify order is in your Shopify admin. | Owner: Your payment provider and AgenticOrg | If blocked: A payment for the wrong amount never becomes an order; it is logged so you can refund it in Plural.
```

No mandate, passkey or spending cap is asked for on this path. The agent holds no payment credential and moves no money, and the buyer authenticates on the payment page. See [Getting paid](/docs/oacp-getting-paid) to set it up.

## Agent-executed payment with mandates (switched off)

AgenticOrg also has a path where the purchase completes without leaving the chat: the payment provider charges against signed buyer mandates. It is built but switched off. Payment execution stays off until a payment-provider sandbox run has passed, and a shop that chose where buyers pay always gets a payment link instead. The rest of this page explains how that path works.

The example uses Merchant Example and a buyer who allows up to ₹2,000.

```figure
oacp-docs-journey | A purchase with mandates | The buyer agent opens a checkout. Grantex issues the Intent, Cart and Payment mandates, the buyer's passkey signs the cart when the buyer is present, and AgenticOrg verifies all three. The arbiter decides; on allow the payment provider charges once and the order is confirmed; everything is recorded. This path is switched off.
```

### The three mandates, in plain words

A mandate is a signed note of what the buyer allowed. Grantex issues all three. Each lives at most 10 minutes.

| Mandate | Plain meaning | Example |
| --- | --- | --- |
| Intent | "My agent may spend up to this much, at these shops." | Up to ₹2,000 at Merchant Example |
| Cart | "This exact cart, and nothing else." | One office tote, ₹1,499, by its hash |
| Payment | "Charge this amount, on this rail, to this token." | ₹1,499 by card token on Plural |

The Cart mandate binds the cart by a hash. Change one item or one price and the hash changes, so the mandate no longer matches.

The Payment mandate never holds the card or UPI token itself. It holds a fingerprint of it.

### Step by step: who does what

```flow
Find the product | The buyer agent reads the shop's catalog through UCP or the ACP feed. Prices and stock come from verified facts. | Owner: Buyer agent | If blocked: Without fresh verified facts the catalog answers with no products.
Open a checkout | The agent creates a checkout session. AgenticOrg prices it from the synced catalog and moves it to ready_for_complete. | Owner: Buyer agent and AgenticOrg | If blocked: An unknown or out-of-stock item keeps the session incomplete.
Intent mandate | Grantex records what the buyer allowed: the description, the spending cap and the shops. | Owner: Grantex | If blocked: Grantex refuses when the buyer's passport cap or consent does not allow it.
Cart mandate and passkey | Grantex binds the exact cart. When the buyer is present, the buyer's passkey on their own device signs the cart hash. | Owner: Buyer and Grantex | If blocked: A missing, wrong or replayed passkey signature refuses the cart.
Payment mandate | Grantex binds the amount, the currency, the rail and a fingerprint of the payment token. | Owner: Grantex | If blocked: Card data in the request, or an unknown rail, is refused.
AgenticOrg verifies | AgenticOrg checks all three signatures, the revocation list and that the chain links to this exact checkout. | Owner: AgenticOrg | If blocked: Any failed check stops the purchase with its refusal code.
Arbiter decides | Eight fixed rules run in order. The answer is allow or refuse, at once. | Owner: AgenticOrg arbiter | If blocked: The first failing rule is the reason, and it is recorded.
RBI AFA, India only | A UPI AutoPay payment needs the buyer to approve with their UPI PIN in their own UPI app. AgenticOrg returns that step and waits for nothing. | Owner: Buyer and their bank | If blocked: Completion is refused until verified AFA evidence arrives from the provider.
Payment | The payment provider charges once, against the consumed allow decision. The three mandate hashes travel with the order. | Owner: Payment provider | If blocked: With payment execution off, as it is today, checkout stops at executor_unavailable and nothing is charged.
Order and receipt | The order is created and a signed order update goes to the agent platform. The ledger holds the mandates, the decision and every payment call. | Owner: AgenticOrg and the merchant | If blocked: A failed payment returns the session to ready_for_complete.
```

### What the decision can say

```figure
oacp-decision-fork | Three possible answers | On the mandate path the arbiter allows, and the payment runs once; or it refuses and names the first failing rule, and nothing is charged; or, for UPI AutoPay, it asks for the buyer's AFA step, and the next attempt gets a fresh decision.
```

### When the buyer is not present

Sometimes an agent buys while the person is away. This is called "human not present".

- There is no passkey step, because nobody is there to touch it.
- The shop must set stricter limits for these purchases, for example a lower amount cap. Without them, every not-present purchase is refused.
- The Intent mandate's cap must cover the total.
- For UPI AutoPay, the AFA exemption never applies.

### RBI AFA for India, in plain words

RBI rules ask for an additional factor of authentication (AFA) on some UPI AutoPay payments.

| Payment | AFA needed? |
| --- | --- |
| Setting up the mandate, and the first debit with it | Always |
| A later debit up to ₹15,000 | No |
| A later debit up to ₹1,00,000 for insurance premiums, mutual fund subscriptions or credit-card bills | No |
| A later debit above those amounts | Yes |
| Amount, currency or debit type unknown | Yes, to stay safe |

A checkout is always a set-up plus a first debit, so AFA always applies there. The limits are settings, so a future RBI change is a configuration change.

On today's path a buyer who pays by UPI on your Plural page approves the payment with their UPI PIN there, as with any online payment.

## What is on by default

> The checkout routes, the AgenticOrg network, mandates and payment execution each start switched off. Once checkout is on and you have chosen where buyers pay, a ready checkout gives the buyer a link to your own payment page. Agent-executed payment stays off, so no money moves without the buyer paying on that page. See [feature switches](/docs/oacp-feature-switches).

## Related guides

- [Getting paid](/docs/oacp-getting-paid)
- [Safety and trust](/docs/oacp-safety-and-trust)
- [Merchant setup checklist](/docs/oacp-merchant-setup)
- Technical references: [payment hand-off](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/payment-handoff.md), [mandates, UPI AutoPay and RBI AFA](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/mandates.md) and [UCP checkout](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/ucp-checkout.md)
