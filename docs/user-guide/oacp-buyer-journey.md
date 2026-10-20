```summary
A buyer's agent finds a product, opens a checkout and asks to complete it.
Grantex issues three mandates: Intent (what the buyer allowed), Cart (this exact cart) and Payment (this amount, on this rail).
When the buyer is present, their passkey signs the exact cart. In India, a UPI AutoPay payment also needs the buyer's RBI AFA step.
AgenticOrg checks the mandates itself, then the arbiter says allow or refuse at once.
Only an allow lets the payment provider charge, once. Everything lands in the ledger.
```

## The journey in one picture

The example uses a synthetic shop, Merchant Example, and a buyer who allows up to ₹2,000.

```figure
oacp-docs-journey | A purchase with mandates | The buyer agent opens a checkout. Grantex issues the Intent, Cart and Payment mandates, the buyer's passkey signs the cart when the buyer is present, and AgenticOrg verifies all three. The arbiter decides; on allow the payment provider charges once and the order is confirmed; everything is recorded.
```

## The three mandates, in plain words

A mandate is a signed note of what the buyer allowed. Grantex issues all three. Each lives at most 10 minutes.

| Mandate | Plain meaning | Example |
| --- | --- | --- |
| Intent | "My agent may spend up to this much, at these shops." | Up to ₹2,000 at Merchant Example |
| Cart | "This exact cart, and nothing else." | One office tote, ₹1,499, by its hash |
| Payment | "Charge this amount, on this rail, to this token." | ₹1,499 by card token on Plural |

The Cart mandate binds the cart by a hash. Change one item or one price and the hash changes, so the mandate no longer matches.

The Payment mandate never holds the card or UPI token itself. It holds a fingerprint of it.

## Step by step: who does what

```flow
Find the product | The buyer agent reads the shop's catalog through UCP or the ACP feed. Prices and stock come from verified facts. | Owner: Buyer agent | If blocked: Without fresh verified facts the catalog answers with no products.
Open a checkout | The agent creates a checkout session. AgenticOrg prices it from the synced catalog and moves it to ready_for_complete. | Owner: Buyer agent and AgenticOrg | If blocked: An unknown or out-of-stock item keeps the session incomplete.
Intent mandate | Grantex records what the buyer allowed: the description, the spending cap and the shops. | Owner: Grantex | If blocked: Grantex refuses when the buyer's passport cap or consent does not allow it.
Cart mandate and passkey | Grantex binds the exact cart. When the buyer is present, the buyer's passkey on their own device signs the cart hash. | Owner: Buyer and Grantex | If blocked: A missing, wrong or replayed passkey signature refuses the cart.
Payment mandate | Grantex binds the amount, the currency, the rail and a fingerprint of the payment token. | Owner: Grantex | If blocked: Card data in the request, or an unknown rail, is refused.
AgenticOrg verifies | AgenticOrg checks all three signatures, the revocation list and that the chain links to this exact checkout. | Owner: AgenticOrg | If blocked: Any failed check stops the purchase with its refusal code.
Arbiter decides | Eight fixed rules run in order. The answer is allow or refuse, at once. | Owner: AgenticOrg arbiter | If blocked: The first failing rule is the reason, and it is recorded.
RBI AFA, India only | A UPI AutoPay payment needs the buyer to approve with their UPI PIN in their own UPI app. AgenticOrg returns that step and waits for nothing. | Owner: Buyer and their bank | If blocked: Completion is refused until verified AFA evidence arrives from the provider.
Payment | The payment provider charges once, against the consumed allow decision. The three mandate hashes travel with the order. | Owner: Payment provider | If blocked: With payment execution off, checkout stops at executor_unavailable and nothing is charged.
Order and receipt | The order is created and a signed order update goes to the agent platform. The ledger holds the mandates, the decision and every payment call. | Owner: AgenticOrg and the merchant | If blocked: A failed payment returns the session to ready_for_complete.
```

## What the decision can say

```figure
oacp-decision-fork | Three possible answers | The arbiter allows, and the payment runs once; or it refuses and names the first failing rule, and nothing is charged; or, for UPI AutoPay, it asks for the buyer's AFA step, and the next attempt gets a fresh decision.
```

## When the buyer is not present

Sometimes an agent buys while the person is away. This is called "human not present".

- There is no passkey step, because nobody is there to touch it.
- The shop must set stricter limits for these purchases, for example a lower amount cap. Without them, every not-present purchase is refused.
- The Intent mandate's cap must cover the total.
- For UPI AutoPay, the AFA exemption never applies.

## RBI AFA for India, in plain words

RBI rules ask for an additional factor of authentication (AFA) on some UPI AutoPay payments.

| Payment | AFA needed? |
| --- | --- |
| Setting up the mandate, and the first debit with it | Always |
| A later debit up to ₹15,000 | No |
| A later debit up to ₹1,00,000 for insurance premiums, mutual fund subscriptions or credit-card bills | No |
| A later debit above those amounts | Yes |
| Amount, currency or debit type unknown | Yes, to stay safe |

A checkout is always a set-up plus a first debit, so AFA always applies there. The limits are settings, so a future RBI change is a configuration change.

## What is on by default

> The checkout routes, mandates and payment execution each start switched off. With the defaults, a checkout can reach ready_for_complete but never completes, and no money moves. See [feature switches](/docs/oacp-feature-switches).

## Related guides

- [Safety and trust](/docs/oacp-safety-and-trust)
- [Merchant setup checklist](/docs/oacp-merchant-setup)
- Technical references: [mandates, UPI AutoPay and RBI AFA](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/mandates.md) and [UCP checkout](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/ucp-checkout.md)
