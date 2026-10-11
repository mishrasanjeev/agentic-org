# Customer agent: integration guide for the bank

The customer agent is the bank's own assistant for the bank's own customers. It runs on the platform's conversational services with an autonomy ladder around them, so a customer can ask, be told why, tap once, or set a standing mandate, and the bank keeps every action inside its own controls. This guide is for the bank's engineering team: how to turn it on, how to open customer sessions, how to embed the element, how step-ups and mandates work, and what every route returns.

The feature is off by default. With `AGENTICORG_CUSTOMER_AGENT_ENABLED` unset, every route under `/api/v1/customer-agent/` answers 404 and nothing else in the platform changes.

## What it does differently

| Concern | A general-purpose assistant | This customer agent |
| --- | --- | --- |
| Who the customer signs in to | A third party, which then asks the bank for access | The bank only; the platform never sees a bank credential |
| How far it may go | Fixed by the vendor | Five levels set per task and per customer, bounded by the bank's ceiling |
| Confirming a payment | Re-typing details, or trusting the model | One tap on a card the engine prepared, or the bank's own passkey, biometric or OTP step-up bound to the action id |
| Why a suggestion was made | Not shown | Every answer and card carries data used, rule applied, alternatives, what the bank earns, how to challenge it |
| Seeing and revoking permissions | Scattered | One permissions page: the bank's agent mandates and, when connected, the authority service's dashboard of outside agents |
| Caps and limits | Prompt instructions | Checked in code before an action is prepared, checked again by the bank's rail, never inside the model |

## Configuration

| Setting | Env variable | Default | Meaning |
| --- | --- | --- | --- |
| `customer_agent_enabled` | `AGENTICORG_CUSTOMER_AGENT_ENABLED` | `false` | Master switch |
| `customer_agent_tenant_id` | `AGENTICORG_CUSTOMER_AGENT_TENANT_ID` | empty | The tenant whose data and agent serve customers |
| `customer_agent_company_id` | `AGENTICORG_CUSTOMER_AGENT_COMPANY_ID` | empty | The company under that tenant whose connectors the agent uses |
| `customer_agent_agent_id` | `AGENTICORG_CUSTOMER_AGENT_AGENT_ID` | empty | The configured agent that runs confirmed actions through its bound tools and grant |
| `customer_agent_assertion_issuer` | `AGENTICORG_CUSTOMER_AGENT_ASSERTION_ISSUER` | empty | The `iss` the bank's assertions carry |
| `customer_agent_assertion_audience` | `AGENTICORG_CUSTOMER_AGENT_ASSERTION_AUDIENCE` | empty | The `aud` the bank's assertions carry |
| `customer_agent_assertion_jwks_uri` | `AGENTICORG_CUSTOMER_AGENT_ASSERTION_JWKS_URI` | empty | Where the bank publishes the public keys that sign assertions and approvals |
| `customer_agent_session_ttl_minutes` | `AGENTICORG_CUSTOMER_AGENT_SESSION_TTL_MINUTES` | `30` | Session lifetime |
| `customer_agent_max_autonomy` | `AGENTICORG_CUSTOMER_AGENT_MAX_AUTONOMY` | `2` | The highest level any customer may set |
| `customer_agent_disclosure` | `AGENTICORG_CUSTOMER_AGENT_DISCLOSURE` | built-in text | The AI disclosure shown before the first turn |
| `customer_agent_bank_earns_text` | `AGENTICORG_CUSTOMER_AGENT_BANK_EARNS_TEXT` | built-in text | The "what the bank earns" line in every explanation |
| `customer_agent_outside_agents` | `AGENTICORG_CUSTOMER_AGENT_OUTSIDE_AGENTS` | `false` | Open a principal session on the authority service for the permissions page |

Without an issuer, audience and JWKS URL no session can be opened, in strict and relaxed runtimes alike. There is no path that trusts an unsigned customer identity.

The agent named by `customer_agent_agent_id` is an ordinary platform agent: create it, bind its connectors, give it the tools the intents need (see the conversational services guide for intent to tool bindings), and keep it active. A paused or retired agent, or none configured, makes every confirmed action return the message that the bank has not connected an agent yet, and the action is recorded as failed; reads and the dialogue still work.

## Opening a customer session

The customer authenticates with the bank as they do today. The bank's backend then signs a short-lived assertion and exchanges it for a session token. The browser or app never sees the signing key, and the platform never sees the bank's credential.

The assertion is a JWT signed with RS256 or ES256 by a key in the bank's JWKS:

```json
{
  "iss": "https://id.example-bank.test",
  "aud": "agenticorg-customer-agent",
  "sub": "cust_7f3a",
  "iat": 1760140800,
  "exp": 1760140920,
  "channel": "app"
}
```

`sub` is the bank's pseudonymous customer reference; the platform stores nothing else about the customer. `channel` is optional (`web`, `app`, `whatsapp`, `voice`, `api`). Keep `exp` short: the assertion is used once.

```bash
curl -s -X POST https://api.example-bank.test/api/v1/customer-agent/sessions \
  -H 'Content-Type: application/json' \
  -d '{"assertion": "<signed JWT>", "channel": "app"}'
```

The response carries `session_token` (prefix `cas_`), `expires_at`, the `disclosure` text and `disclosure_acknowledged: false`. The token is returned once and only its hash is stored. Hand it to the channel; every other route takes it as `Authorization: Bearer cas_...`.

The customer must acknowledge the AI disclosure before the first turn (`POST /session/acknowledge`); until then turns answer 409 `disclosure_required`. `DELETE /session` ends the session.

## Embedding the element

The platform's own UI cannot be framed (it sends `X-Frame-Options: DENY` and a strict content security policy), so the customer agent ships as a script the bank's page loads:

```html
<script src="https://app.example-bank.test/customer-agent/widget.js"></script>
<agenticorg-customer-agent
  api-base="https://api.example-bank.test/api/v1"
  session-token="cas_...">
</agenticorg-customer-agent>
```

The element renders inside a shadow root with no dependencies. It shows the disclosure until acknowledged, then the conversation; prepared actions appear as cards with Confirm and Cancel; every answer that prepared or ran something carries a collapsible "Why this?" panel. All text is set with `textContent`, so nothing the agent or the customer typed is interpreted as markup.

Events the host can listen for:

| Event | When | Detail |
| --- | --- | --- |
| `customer-agent:ready` | The session was read | the session view |
| `customer-agent:turn` | A turn was rendered | the turn payload |
| `customer-agent:approval-required` | A card needs the bank's step-up | `{ action_id, card }` |
| `customer-agent:permissions` | The customer pressed Permissions | none |

For a step-up the host runs its own passkey, biometric or OTP flow, has its backend sign an approval (below) and calls `element.approve(actionId, { method, assertion })`. The script also exposes `window.AgenticOrgCustomerAgent.createClient(apiBase, token)` for native apps and server-rendered channels that do not use the element.

## The autonomy ladder

| Level | Name | What happens |
| --- | --- | --- |
| 0 | inform | Cited answers from the bank's data. Reads always run at this level and need no mandate. |
| 1 | recommend | A suggestion with its explanation; nothing is prepared. A transaction at this level is refused with the reason and a pointer to the permissions page. |
| 2 | prepare | The engine collects the details, prepares the action and waits for one tap. The default for every transaction. |
| 3 | execute with approval | As level 2, but the tap is the bank's step-up: a signed approval bound to the action id. |
| 4 | execute within mandate | Inside the caps, counterparties and time window the customer set, the action runs in the same turn. Outside them it falls back to a step-up. |

`AGENTICORG_CUSTOMER_AGENT_MAX_AUTONOMY` bounds every level; a mandate above it is refused when set and clamped when read. A customer with no mandate for a task has level 2 for a transaction and level 0 for a read.

A mandate is one envelope per task class (the transaction intent's name: `fund_transfer`, `bill_payment`, `card_block`, `card_replacement`, `loan_application`, `dispute_transaction`). Setting a new one revokes the active one for the same class. Fields: `autonomy_level`, `per_action_cap`, `period_cap` with `period` (`day`, `week`, `month`), `counterparties` (payee or biller names, up to fifty), `window` (`start_hour`, `end_hour`), `expires_at`. Level 4 needs a `per_action_cap`. The period cap counts executed actions of that class in the period.

## Approvals for a step-up

An approval is a JWT signed with the same bank keys, bound to the action and the customer:

```json
{
  "iss": "https://id.example-bank.test",
  "aud": "agenticorg-customer-agent",
  "sub": "cust_7f3a",
  "action_id": "3c7f...",
  "amount": 50000,
  "iat": 1760141000,
  "exp": 1760141120
}
```

Send it as `{"approval": {"method": "passkey", "assertion": "<JWT>"}}` with the turn that confirms, or with `POST /actions/{id}/confirm`. `method` names the step-up the bank performed (`passkey`, `biometric`, `otp`, `bank_step_up`). An approval for another customer, another action or another amount is refused and nothing runs. Without a valid approval the turn answers `approval_required: true` and the card stays prepared.

## Routes

All routes live under `/api/v1/customer-agent`. Only the first takes no session.

| Method and path | Purpose |
| --- | --- |
| `POST /sessions` | Exchange a bank assertion for a session token |
| `GET /session` | The session view (disclosure, acknowledgement, expiry) |
| `POST /session/acknowledge` | Record the disclosure acknowledgement |
| `DELETE /session` | End the session |
| `GET /intents` | What the assistant can do, with task class and default level |
| `POST /turns` | One turn: `{ text, approval? }` |
| `GET /actions` | The customer's actions, newest first (`status`, `limit`) |
| `POST /actions/{id}/confirm` | The card's tap; `{ approval? }` for a step-up |
| `POST /actions/{id}/cancel` | Cancel a prepared action |
| `GET /permissions` | Mandates, the ceiling, and the outside-agents dashboard |
| `PUT /permissions/mandates` | Set the mandate for one task class |
| `DELETE /permissions/mandates/{id}` | Revoke a mandate |

A turn returns the conversational payload (`answer`, `outcome`, `dialogue`, `session_key`) plus `card` (a prepared action or none), `explanation` (the "why this" panel), `autonomy` (the decision: level, whether it may proceed, what it needs), `refusal` and `approval_required`.

A card whose conversation has moved on since it was prepared is marked superseded when tapped and answers 409 `action_stale`; it is never run. An action already decided answers 409 `action_settled`.

## Records and evidence

| Table | Holds |
| --- | --- |
| `customer_sessions` | Session id, customer reference, channel, token hash, issue and expiry times, disclosure acknowledgement, revocation |
| `customer_mandates` | One envelope per task class with level, caps, period, counterparties, window, expiry, status |
| `customer_actions` | Every action prepared, executed, refused, cancelled, failed or superseded, with parameters, amount, level applied, what it required, the approval given, the explanation shown and the execution summary |

All three are tenant-scoped under row-level security. Executed actions also appear in the conversation session's own action record and in the platform's audit trail through the tool call that ran them, so the customer's actions list and the bank's evidence agree.

## Security notes

- The customer never holds a platform credential and the platform never holds a bank credential. The assertion is the only bridge, and it is signed, short-lived and used once.
- Session tokens are random, prefixed `cas_`, and stored hashed. A leaked database row cannot be replayed.
- Routes under the prefix are exempt from the operator middleware because they carry no operator credential; each one authenticates the session token itself and refuses without it. Session opening is rate-limited per client IP, the rest per session.
- The element holds nothing but the session token the host injected, loads no third-party code and renders no HTML from server data.
- Autonomy, caps and counterparties are enforced before an action is prepared and the step-up is verified before the engine executes. The model never decides whether something may run.
- The disclosure is mandatory and recorded. Every explanation names the AI assistant and says how to reach a person.
