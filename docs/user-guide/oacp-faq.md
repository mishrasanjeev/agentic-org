```summary
Short answers to the questions sellers ask most about OACP.
OACP is a trust layer under UCP, ACP and AP2. It feeds them and does not replace them.
Your shop stays the source of truth. Grantex signs. AgenticOrg checks, decides and records. The payment provider moves money.
Nothing that publishes your shop or moves money is on by default.
For the full picture, start with the overview and the merchant setup checklist.
```

## Who does what

```figure
oacp-roles | Four roles, clear limits | Your shop owns the facts and orders. Grantex signs facts, runs the revocation list and issues mandates, and never moves money. AgenticOrg verifies, decides and records. The payment provider charges, and only after an allow decision.
```

## Do I have to choose between UCP, ACP and OACP?

No. OACP sits underneath. It renders your facts into UCP and ACP, and maps mandates to AP2 field names. Buyer agents keep using the standard they already speak.

## Is OACP certified or endorsed by the UCP, ACP or AP2 groups?

No. OACP is not a certification, and those groups have not endorsed it. It also does not establish regulatory compliance on its own.

## What do I need to start?

A Shopify store, the merchant or administrator role in AgenticOrg, and a platform operator who connects your tenant to Grantex. Follow the [merchant setup checklist](/docs/oacp-merchant-setup).

## Does every buyer question go through Grantex?

No. AgenticOrg answers questions from its own verified copy of your facts. Grantex takes part in purchases: it issues the mandates and countersigns the decision. It never moves money.

## Can an agent buy something at the wrong price?

OACP is built to refuse it. A decision is bound to the exact cart, price and quantity. Change any of them and the decision no longer matches. Stale, expired or revoked prices are refused.

## Does a person at my shop approve each order?

No. The arbiter decides at once with fixed rules. You can copy refusals into a review queue, but that never changes or delays a decision.

## What happens if Grantex is unreachable?

Plain questions can still be answered from facts that are still valid. In production, anything that commits money is refused until Grantex is back. If the revocation list cannot be read, facts are refused.

## How do I stop selling a product to agents at once?

Revoke the fact that covers it. Grantex marks it invalid, and every channel stops using it. For one channel only, turn that channel's switch off.

## Does this work with UPI in India?

It is built for it: UPI AutoPay mandates on the Plural rail, with RBI AFA as the buyer's own step in their UPI app. Completion is refused until verified AFA evidence arrives. It has not yet run against the Plural sandbox, and payment execution is off by default.

## What if the buyer is not present when the agent buys?

There is no passkey step. Your stricter not-present limits apply, and without them the purchase is refused.

## Can I check the records myself?

Yes. Export the ledger and run `scripts/verify_ledger.py` on your own computer. It needs no database and no network. See [Safety and trust](/docs/oacp-safety-and-trust#the-ledger-and-offline-checks).

## What is switched on today?

The safety checks are on in production. Every surface that publishes your shop or moves money starts off: the UCP profile and checkout, the ACP feed and checkout, mandates and payment execution. With the defaults no checkout completes and no money moves. See [feature switches](/docs/oacp-feature-switches).

## Where can I read more?

- [What OACP is](/docs/oacp-overview) and [how it works](/docs/oacp-how-it-works)
- The blog post [OACP explained: one trust layer under UCP, ACP and AP2](https://agenticorg.ai/blog/oacp-one-trust-layer-under-ucp-acp-ap2)
- The technical [OACP runtime docs](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/README.md)
