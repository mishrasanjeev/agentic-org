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

The conversation services are the foundation and are off by default (`AGENTICORG_CONVERSATION_V2_ENABLED`). On, `POST /api/v1/conversation/turns` recognises a banking intent from a synthetic catalogue, fills slots across turns (amounts, account and card endings, payees, dates, references), asks a clarifying question when two intents are close, confirms before anything runs, and runs the confirmed action through a governed agent under the caller's grant. Supervisors can list sessions, take over, reply and release through the supervisor endpoints. Intent recognition is deterministic and the catalogue is generic: no institution's products or names.

Around it, the platform already provides the controls the customer agent needs: approvals with a fail-closed condition grammar, four-stage guardrails, the model gateway with routing, access policies, limits and cost-aware selection, operator overrides, residency enforcement, run timelines and the hash-chained audit log. Most of these ship off by default and are turned on per deployment; the PRD lists which must be on before a customer touches the agent.

## Try the foundation

1. Turn on conversational services in a non-production deployment and create a reviewed service agent with a knowledge set, as described in [Customer service](/docs/bfsi-customer-service).
2. Send a turn such as "what is my balance" to `POST /api/v1/conversation/turns` with your operator credentials and the agent id; read the recognised intent, its confidence and the answer.
3. Send a transaction such as "transfer 5000 to Ravi", watch the dialogue collect the details and stop at a confirmation, confirm, and inspect the run in [Run observability](/docs/run-observability).
4. Open the supervisor sessions endpoint, take over the session, reply as a human and release it.
5. Review the audit rows and the run timeline the turn produced.

No customer-facing channel exists yet: every entry point is operator-authenticated. Do not point a real customer at these endpoints.

## What is planned

| Phase | Outcome |
|---|---|
| 0: foundations | Grant revocation for paused and retired agents, revocation checks before a token is honoured, durable conversation state by default, banking pack tools bound at creation, spend caps on every run path, and a bank deployment profile that forces the strict configuration. |
| 1: inform and recommend | Customer session bound to the bank's identity, the embedded widget, WhatsApp and voice in Hindi and English, L0 and L1 across the service range, "why this", disclosure, whole-wallet view, human hand-off with the supervisor view, nudges with control groups. |
| 2: execute with approval and open MCP | L2 and L3 for transfers, bills, card controls, disputes and SME payments; signed intent records; the bank's MCP server and scope registry; the permissions page with outside agents and one-tap disconnect. |
| 3: mandates and agentic payments | Mandates with caps, allow-lists and 90-day re-confirmation; L4 for sweeps, recurring bills and SME payables; network agent-token mirroring; Agent Passport gating. |
| 4: ecosystem | Trust-registry issuer onboarding, relying-party onboarding, signed-request verification, more Indian languages and voice providers. |

## Design rules

- The model never holds credentials and never decides authority: grants and mandates are checked at the tool boundary and again at the bank's rail.
- Every action the agent takes is attributable to a customer, a grant and a run, and every grant can be revoked instantly.
- The agent informs and recommends by default; execution is earned per task and per customer and is demoted on the first irregularity.
- No PIN, OTP, password or card number is ever requested or accepted in conversation.
- AI involvement is disclosed at first use and on every interaction, and a human is reachable at every turn.

Next: [Capability status and gaps](/docs/bfsi-capability-status), [Customer service](/docs/bfsi-customer-service), [Approvals](/docs/approvals).
