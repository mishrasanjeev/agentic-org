## What the customer agent is

The customer agent is the bank's own assistant for its own customers. It sits in the bank's app, web, WhatsApp and voice channels, sees every product the customer holds and every product the bank can offer, answers with citations, recommends with a "why this" explanation, prepares actions for one-tap confirmation and, once a customer has set a mandate, executes inside that mandate. It runs on AgenticOrg, with Grantex as the authority service that holds every grant the customer gives, to the bank's agent and to any outside agent the customer connects.

The programme is called "own the agent, open the agent". Own the agent: the bank's assistant must be the best agent for the bank's services, so a customer has no reason to hand their finances to a general-purpose assistant. Open the agent: the bank is also the easiest institution for a customer's outside agent to connect to safely, through a governed MCP surface, Agent Passports and scoped grants the customer can see and revoke from one place. The full requirement set, market evidence, control catalogue and architecture are in the [product requirements document](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/customer-agent/PRD.md).

## Why it is easier than an in-app assistant

Early bank assistants on the market execute a short list of actions inside one app, ask for a biometric approval on each sensitive step, and see only what the customer sees on screen. The customer agent is designed to be easier to use and to go much further, while staying inside the bank's controls:

| What the customer gets | How it works |
|---|---|
| One agent across every service | Deposits, cards, lending, mortgages, investments, insurance, payments, remittances and SME banking share one session, one memory and one permissions page. |
| Any channel, same conversation | A conversation started in the app continues on WhatsApp or by voice; the agent keeps the context, not the customer. |
| Cards instead of chat | Balances, due dates, prepared transfers and offers arrive as inline cards with one-tap confirm or cancel, never as a wall of text. |
| Approval only where it matters | An autonomy ladder from inform (L0) to execute within mandate (L4) is set per task and per customer. Routine actions inside a mandate run without a prompt; new payees and high values always step up. |
| "Why this" on every recommendation | Data used, rule or goal applied, alternatives considered, what the bank earns, and how to challenge the suggestion. |
| Whole-wallet view | With consent through Account Aggregator or open banking, the customer sees accounts held elsewhere inside the bank's agent, so comparison no longer needs an outside app. |
| One place to see and stop every agent | The permissions page lists the bank's agent and every outside agent, with scopes, caps and last action, and revokes with one tap. The revoked agent's next action is refused within seconds. |
| Open to the customer's own agent | Outside agents connect through the bank's MCP server with a verified Agent Passport and a scoped grant; the bank, not the agent provider, stays accountable for what executes. |
| Evidence for every action | Signed, hash-chained audit rows, run timelines and a per-interaction evidence package the bank can hand to an auditor. |

## How a request moves

The same path applies in every channel. The bank keeps identity and the step-up; the agent keeps the conversation, the explanation and the record.

```flow
Sign in with the bank | The customer signs in to the bank's own app or site; the bank's backend signs a short-lived assertion and opens a customer session. | Owner: Bank identity team | If blocked: Without the bank's assertion there is no session; the platform never accepts a customer the bank has not asserted.
Disclosure | The customer reads that this is the bank's AI assistant and acknowledges it before the first turn. | Owner: Bank channel owner | If blocked: Turns are refused until the disclosure is acknowledged.
Ask | The customer asks in their own words; the dialogue recognises the intent, collects the details across turns and explains why. | Owner: Customer agent | If blocked: An unrecognised request gets a clarifying question or an offer of a person, never a guess.
Prepare | For a transaction the agent prepares a card with the amount, the payee and a "why this" panel at the customer's autonomy level. | Owner: Customer agent | Human decision: The customer confirms with one tap, cancels, or asks for a change. | If blocked: Below level 2 the action is explained and refused; the customer can raise the level on the permissions page.
Step up | Above the customer's limits, or at level 3, the bank's own passkey, biometric or one-time-code check approves the specific action. | Owner: Bank identity team | Human decision: The customer approves the exact action and amount in the bank's step-up. | If blocked: An approval for another action, customer or amount is refused and nothing runs.
Execute and record | The confirmed action runs through the bank's governed agent under its grant and is recorded with the explanation shown and the approval given. | Owner: Bank operations and system of record | If blocked: No connected agent, a paused agent or a failed rail records the action as failed and tells the customer nothing was done.
```

## What ships today

The customer agent ships off by default behind `AGENTICORG_CUSTOMER_AGENT_ENABLED`. With the flag off every route under `/api/v1/customer-agent/` answers 404 and nothing else changes. On, a bank gets:

| Capability | How it works |
|---|---|
| Customer sessions without a platform credential | The bank authenticates the customer as today, its backend signs a short-lived assertion (RS256 or ES256, checked against the bank's published keys, issuer and audience, single-use by its id) and exchanges it for an opaque session token. Only the token's hash is stored. |
| The autonomy ladder per task and per customer | Level 0 inform, 1 recommend, 2 prepare and wait for one tap (the default for every transaction), 3 execute after the bank's step-up of the specific action, 4 execute within a mandate with caps, counterparties and a time window. `AGENTICORG_CUSTOMER_AGENT_MAX_AUTONOMY` (2 by default) bounds every level. |
| Prepared-action cards | The dialogue collects the details and prepares a card; Confirm sends the same yes the dialogue expects, Cancel the same no. A card confirms only the action it shows: if the conversation moved on or the amount changed, it is superseded and nothing runs. |
| The bank's own step-up | Level 3 and out-of-mandate level 4 need a bank-signed approval bound to the action id, customer and amount, verified before the engine executes. |
| "Why this" on every answer | Data used, the rule or level applied, the mandate, alternatives, what the bank earns, how to challenge it, and the AI disclosure, assembled from the dialogue and the decision rather than generated. |
| A mandatory disclosure | Recorded on the session; no turn runs before the customer acknowledges it. |
| One permissions page | Every mandate the bank's agent holds, revocable with one call, and the authority service's dashboard of outside agents when the deployment is connected to it. |
| An embeddable element | `ui/public/customer-agent/widget.js` registers `<agenticorg-customer-agent>` for the bank's page: the disclosure gate, cards, the "why this" panel and a host event for step-ups, with no dependencies and no markup from server text. |
| Records and evidence | Sessions, mandates and every action prepared, executed, refused, cancelled or superseded, with the explanation shown and the approval given, in tenant-scoped tables under row-level security; executed actions also appear in the conversation session's record and the audit trail. |

Confirmed actions run through the bank's configured agent, its bound tools and its grant exactly as the [conversational services](/docs/bfsi-customer-service) do. The intent catalogue is deterministic and generic: no institution's products or names. Grant revocation for paused and retired agents, and an opt-in revocation check before a token is honoured (`AGENTICORG_GRANTEX_REVOCATION_CHECK=feed`), ship alongside.

The step-by-step setup for the bank's engineers, the assertion and approval formats, the element's events and every route are in the [integration guide](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/customer-agent/integration.md).

## Try it

1. In a non-production deployment, set `AGENTICORG_CUSTOMER_AGENT_ENABLED=true`, the tenant, company and agent ids, and the assertion issuer, audience and JWKS URL of a test identity provider you control. Create the agent as described in [Customer service](/docs/bfsi-customer-service) and bind the tools the intents need.
2. From your backend, sign a test assertion for a synthetic customer reference and exchange it at `POST /api/v1/customer-agent/sessions`; keep the `session_token` from the response.
3. Read `GET /session`, acknowledge the disclosure with `POST /session/acknowledge`, and send "what is my balance" to `POST /turns`: the answer carries the recognised intent and its "why this" panel.
4. Send "transfer 5000 to Ravi": the turn returns a prepared card at level 2. Confirm it with `POST /actions/{id}/confirm` (or reply yes) and inspect the run in [Run observability](/docs/run-observability); then read `GET /actions` to see the recorded action with its explanation.
5. Set a mandate with `PUT /permissions/mandates` (level 4 with a per-action cap for `fund_transfer`), repeat a transfer inside the cap and watch it run in the same turn; repeat one above the cap and watch the card ask for the bank's approval.
6. Load `widget.js` on a test page with the session token and walk the same path in the element.

Point no real customer at a deployment until the bank's identity provider, step-up and agent bindings are in place and the controls in the PRD are on.

## What is planned

| Phase | Outcome |
|---|---|
| 0: foundations (in progress) | Shipped: grant revocation for paused and retired agents, revocation checks before a token is honoured. Next: durable conversation state by default, banking pack tools bound at creation, spend caps on every run path, and a bank deployment profile that forces the strict configuration. |
| 1: inform and recommend (in progress) | Shipped: customer sessions bound to the bank's identity, the embedded element, the autonomy ladder, "why this", the disclosure, the permissions page. Next: WhatsApp and voice in Hindi and English, L0 and L1 across the whole service range, the whole-wallet view, human hand-off with the supervisor view, nudges with control groups. |
| 2: execute with approval and open MCP | Shipped: L2 and L3 with the bank's step-up for the transaction intents in the catalogue. Next: signed intent records, the bank's MCP server and scope registry, outside agents on the permissions page with one-tap disconnect. |
| 3: mandates and agentic payments | Shipped: mandates with caps, counterparties and windows, and L4 execution inside them. Next: 90-day re-confirmation, network agent-token mirroring, Agent Passport gating. |
| 4: ecosystem | Trust-registry issuer onboarding, relying-party onboarding, signed-request verification, more Indian languages and voice providers. |

## Design rules

- The model never holds credentials and never decides authority: grants and mandates are checked at the tool boundary and again at the bank's rail.
- Every action the agent takes is attributable to a customer, a grant and a run, and every grant can be revoked instantly.
- The agent informs and recommends by default; execution is earned per task and per customer and is demoted on the first irregularity.
- No PIN, OTP, password or card number is ever requested or accepted in conversation.
- AI involvement is disclosed at first use and on every interaction, and a human is reachable at every turn.

Next: [Capability status and gaps](/docs/bfsi-capability-status), [Customer service](/docs/bfsi-customer-service), [Approvals](/docs/approvals).
