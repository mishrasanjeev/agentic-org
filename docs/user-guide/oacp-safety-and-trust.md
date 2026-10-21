```summary
Revoke a fact once and every channel stops using it. If the revocation list cannot be reached, everything refuses.
The arbiter checks up to eight fixed rules before money moves. The first rule that fails is the reason, and it is recorded.
Every decision is in the commerce ledger. You can export it and check it on your own computer, with no database and no network.
Agent requests can carry a signature (Visa TAP or Web Bot Auth). AgenticOrg checks and logs them today; refusing unsigned calls is a separate switch.
A refusal is never a mystery: it names the rule, the reason and how to fix it.
```

## Revocation everywhere

Sometimes a fact must stop counting at once. A price was wrong. A product was recalled. A buyer withdrew consent.

With OACP you revoke it once. Grantex marks its slot on a public revocation list as invalid. Every place that relies on it checks that list.

```figure
oacp-revocation | One revoke, every channel | The merchant or operator revokes one fact. Grantex marks it invalid on the status list. AgenticOrg refuses the cached fact, the UCP profile stops offering what relied on it, the arbiter refuses new purchases, and revoking a mandate revokes the mandates built on it. If the list cannot be reached, everything refuses.
```

Good to know:

- AgenticOrg keeps a copy of the list only briefly: never longer than the list itself allows, and never more than 10 minutes.
- Revoking a mandate also revokes the mandates built on it.
- A revocation stops new decisions. It does not undo a payment that already ran.

## What the arbiter checks

Before a checkout completes, the arbiter runs these rules in a fixed order. Every rule runs. The first one that fails decides the refusal.

```figure
oacp-arbiter-rules | Eight rules, one answer | A purchase passes eight gates in order: mandate, consent, spending cap, verified buyer authority, valid facts, merchant limits, not-present limits and RBI AFA. Passing all gives allow. The first failure gives refuse, with that rule as the reason.
```

| # | Rule | In plain words | Example refusal |
| --- | --- | --- | --- |
| 1 | Mandate present | There is a buyer mandate, and it has not expired. | mandate_expired |
| 2 | Consent active | The buyer's consent is still active. | consent_missing |
| 3 | Spending cap | The buyer's Commerce Passport allows this amount, in this currency. | passport_amount_cap_exceeded |
| 4 | Verified buyer authority | AgenticOrg verified the mandates itself. A claim in the request is not enough in production. | buyer_authority_caller_asserted |
| 5 | Facts still valid | Catalog, price, stock, policy and other facts are signed, fresh, unexpired and not revoked. | artifact_revoked:price |
| 6 | Merchant limits | The order is within your amount, currency and quantity limits. | order_amount_cap_exceeded |
| 7 | Not-present limits | If the buyer is away, your stricter limits and the Intent cap cover the order. | not_present_limits_missing |
| 8 | RBI AFA | A UPI AutoPay payment has verified AFA evidence when RBI rules need it. | afa_evidence_missing |

A refund has its own single rule: the refund must be positive, in the order's currency, and within what was captured.

## Nobody waits for a person

The arbiter never answers "pending". It decides at once.

- An allow is single use, bound to the exact cart, and expires after 10 minutes.
- In production an allow is usable only with a Grantex countersignature. If that is missing, ask for a new decision.
- You can choose to copy refusals into your review queue. That copy never changes or delays the decision.
- An operator's note on a decision is only a note. It can never turn a refusal into an allow.

## The ledger and offline checks

Every commerce write and every decision is recorded in the commerce ledger, in the same transaction as the change itself.

```figure
oacp-ledger-verify | Check the record yourself | Export the ledger, run the verifier on your own computer, and read one of two answers: verified, or the first break, such as a missing, reordered, edited or truncated row.
```

```flow
Export | A tenant administrator downloads the ledger export from the ledger routes. | Owner: Tenant administrator | If blocked: Only administrators of that tenant can export.
Verify offline | Run python scripts/verify_ledger.py export.json. It uses no database and no network. | Owner: Anyone holding the file | If blocked: Exit code 2 means the file is not a readable export.
Read the answer | Exit code 0 means verified. Exit code 1 reports the first break, such as sequence_gap or link_hash. | Owner: Auditor or merchant | If blocked: Treat a break as an irregularity, re-export and compare.
```

Three honest details:

- Rows are signed with a platform secret (HMAC). The offline check verifies the chain links and the witnessed heads. It checks row signatures only when you supply the secret.
- Rows are chained only for tenants where audit-chain sealing is on. That switch starts off.
- To catch someone who rewrites the whole file, compare with a head kept outside it: a Grantex witness (its own switch, off by default) or a head you saved yourself with --expected-head.

## Request signing: which agent is calling?

Agents can sign their HTTP requests (RFC 9421). AgenticOrg understands two public profiles:

- **Visa Trusted Agent Protocol (TAP):** browsing and payment tags, a short window and a one-time nonce.
- **Web Bot Auth:** the agent's key is found through its published signature agent.

```figure
oacp-httpsig-modes | Log mode and enforce mode | In log mode, the default, every request is answered and its signature status is logged. In enforce mode, unsigned or invalid requests to the commerce API get 401. Public pages are never refused in either mode.
```

| Mode | Unsigned or bad signature | Good signature |
| --- | --- | --- |
| log (default) | Answered, and the status is logged | Answered, logged, and recorded on write routes |
| enforce | Refused with 401 on the commerce API surfaces | Answered, logged, and recorded on write routes |

Enforce mode covers only the catalog and product JSON reads and the A2A routes. Your public pages, structured data, sitemap and llms.txt are never refused for a missing signature.

## What a refusal looks like

A refusal always tells you what failed and why. Here is a shortened, synthetic example for a purchase over the buyer's cap:

```json
{
  "code": "arbiter_refused",
  "failing_rule": "passport_amount_cap",
  "reason": "passport_amount_cap_exceeded",
  "decision_id": "arbdec_5b0e1c7d2a9f",
  "action": "The commerce arbiter refused: passport_amount_cap (passport_amount_cap_exceeded).",
  "required_config": "buyer_authority.passport_ref, passport_max_amount_minor_units, passport_currency, passport_expires_at"
}
```

Read it like this:

1. **failing_rule** is the rule from the table above.
2. **reason** is the exact cause.
3. **decision_id** finds the sealed decision in the ledger.
4. **required_config** says what must change before a new attempt.

In a UCP or ACP checkout the decision comes back inside the checkout answer, as a business message. Nothing is charged.

## Related guides

- [Feature switches reference](/docs/oacp-feature-switches)
- [OACP FAQ](/docs/oacp-faq)
- Technical references: [arbiter and ledger](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/arbiter-ledger.md), [agent request signing](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/agent-request-signing.md) and [OACP troubleshooting](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/troubleshooting.md)
