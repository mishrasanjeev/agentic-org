```summary
AI shopping agents use several standards: UCP, ACP, AP2, Visa TAP, Mastercard Agent Pay, Web Bot Auth, MCP and A2A.
Wiring each one on its own leaves you with several copies of your prices, rules and logs.
OACP is one trust layer underneath them. You state your facts once, Grantex signs them, and AgenticOrg checks, decides and records.
Each standard then receives the same facts in its own format. Revoke a fact once and every channel stops using it.
OACP feeds the standards. It does not replace them, and it is not a certification.
```

## The short version

OACP stands for Open Agentic Commerce Protocol.

It is a safety layer for selling to AI agents. It sits under the agent standards you already hear about. It does not compete with them.

Think of it as one source of truth for your shop. Every agent channel reads from it. Every purchase passes through the same rules. Every decision lands in the same record.

```figure
oacp-five-copies | Five copies versus one layer | On the left, each standard is wired on its own, so the shop keeps five sets of facts, keys and logs. On the right, the shop feeds one OACP layer, and adapters render it for every buyer agent.
```

## Why sellers need it

AI agents now shop for people. One agent compares prices. Another fills a cart. A third pays.

Each of those steps has its own standard. If you connect to each standard separately, four problems appear:

- **Many sources of truth.** A price changes in one feed but not another.
- **Many trust set-ups.** Each integration has its own keys and its own checks.
- **Many logs.** Nobody can show one clean record of what happened.
- **No common off switch.** Pull a product in one place and an agent can still buy it somewhere else.

OACP fixes these by putting one layer underneath. You keep your facts in one place. Every channel reads the same signed copy.

## The standards, in one line each

You do not need to learn these. OACP renders into them for you.

| Standard | What it answers | What OACP gives it |
| --- | --- | --- |
| UCP (Universal Commerce Protocol) | Where is the shop, what does it sell, how do I check out? | Your business profile, catalog, checkout and orders |
| ACP (Agentic Commerce Protocol) | Which products can an agent buy, and how? | A product feed and agent checkout sessions |
| AP2 (Agent Payments Protocol) | What did the buyer allow the agent to spend? | Intent, Cart and Payment mandates, mapped to AP2 field names |
| Visa TAP and Web Bot Auth | Which agent is calling, and is it really that agent? | Signature checks on agent requests |
| Mastercard Agent Pay | Which card-network agent id does this agent have? | The scheme label, recorded from the registry (its wire details are not public) |
| MCP and A2A | How do agents and tools talk to each other? | Checkout tools for MCP clients and a seller agent for A2A |

```figure
oacp-standards-map | Each standard answers one question | Discovery, checkout, payment permission and agent identity each have their own standard. OACP sits underneath and feeds all of them from the same signed facts.
```

## What OACP actually is

OACP is three things working together:

1. **Signed facts.** Your catalog, prices, stock and policies become small signed records. Grantex signs them. Each one has an expiry date and a revocation slot.
2. **One decision before money moves.** A fixed rule set, called the arbiter, says allow or refuse. No allow, no payment.
3. **One record.** Every decision and every commerce write is recorded in a signed ledger you can export and check.

Adapters then render those facts into each standard's own format.

## Who does what

```flow
Your shop | Stays the source of truth for products, prices, stock and orders. Shopify is the supported source today.
Grantex | Signs your facts, publishes the signing keys, runs the revocation list and issues buyer mandates.
AgenticOrg | Checks every signature, decides each purchase with fixed rules, records it and speaks each standard.
Payment provider | Moves money, and only after an allow decision. Plural is the rail built today.
```

## What OACP is not

Honesty matters here, so here is the plain list.

- **Not a replacement.** OACP renders into UCP, ACP and AP2. Buyer agents still speak those standards.
- **Not a certification.** The groups behind UCP, ACP, AP2 and TAP have not certified or endorsed OACP.
- **Not regulatory approval.** OACP helps you keep evidence. It does not establish regulatory compliance on its own.
- **Not AP2's own format.** Mandates are signed JWTs that use AP2 field names, not AP2's SD-JWT format. Passkey signing of the cart is a Grantex extension.
- **Not switched on by default.** Checkout, payment, mandates and public discovery each sit behind a switch that starts off. See [feature switches](/docs/oacp-feature-switches).

## Read next

- [Example: Nilgiri Tea House, end to end](/docs/oacp-example-nilgiri-tea), a live shop you can try in two minutes
- [How OACP works, end to end](/docs/oacp-how-it-works)
- [A purchase, step by step](/docs/oacp-buyer-journey)
- [Merchant setup checklist](/docs/oacp-merchant-setup)
- The blog post [OACP explained: one trust layer under UCP, ACP and AP2](https://agenticorg.ai/blog/oacp-one-trust-layer-under-ucp-acp-ap2)
- The technical reference: [OACP runtime docs](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/README.md)
