# Conversational services: banking intents, slot filling and confirmation

With `AGENTICORG_CONVERSATION_V2_ENABLED` on, a chat message that names a banking request is handled
as a **turn** of a dialogue (`core/conversation/`) before any agent runs: the intent is recognised,
the parameters it needs are collected turn by turn, an ambiguous message is clarified, a
transaction is summarised and confirmed, and only then is the bound tool invoked under the run's
grant. Off, every message goes to the agent path exactly as before.

## Intents

| Intent | Risk | Slots (required in bold) | Confirmed |
|---|---|---|---|
| `balance_enquiry` | read | account | no |
| `mini_statement` | read | account, period | no |
| `card_block` | transact | **card**, **reason** (lost, stolen, damaged) | yes |
| `fund_transfer` | transact | **amount**, **payee**, from_account, remarks | yes |
| `bill_payment` | transact | **biller**, **amount**, consumer_id | yes |
| `loan_enquiry` | read | loan_type, amount | no |
| `dispute_transaction` | transact | **amount**, transaction_date, merchant, **reason** | yes |
| `application_status` | read | **reference** | no |
| `talk_to_agent` | hand-off | | |
| `greeting` | small talk | | |

Recognition is deterministic (`core/conversation/intents.py`): each intent has weighted patterns,
the score of a message is the sum of the weights it matches, and the confidence is that score. A
message below `MIN_CONFIDENCE` (0.5) is a fallback that lists what the runtime can do; two intents
within `CLARIFY_MARGIN` (0.15) of each other, or one message carrying two requests ("block my card
and transfer 500 to Ravi"), are a clarification that asks which to do first. Entities (amounts in
rupees with lakh and crore, account and card endings, dates, payee names, reference numbers,
periods, loan types and reasons) are extracted once per message and fill whichever slots they
match, on the first turn and on every later one, so "transfer 500" followed by "to Ravi from the
account ending 1234" fills three slots across two turns.

## The dialogue

`core/conversation/dialogue.py` is the state machine. A turn yields one outcome:

- `ask`: the next missing required slot, with its prompt; an answer that fails validation is asked
  again (three failures escalate).
- `clarify`: a numbered choice between intents.
- `confirm`: a one-sentence summary ("Transfer ₹5,000 to Ravi from the account ending 1234.") that
  the user answers with yes or no; a correction ("make it 6000") re-confirms with the new value.
- `execute`: the action and its slots, returned only after confirmation for transactional intents
  and immediately for read intents.
- `escalate`: a hand-off with the intent tag, the slots so far and the recent turns.
- `cancelled`, `fallback`, `greeting`.

The most a conversational transaction may move is ₹10,00,000; a larger amount is refused at the slot.

## Execution under the grant

A confirmed action runs through the agent's own governed tools (`core/conversation/runtime.py`).
The intent's action is bound to a tool the agent is authorised for: the agent's declared binding
(`config.conversation.bindings`, intent to `connector:tool`) first, else the first authorised tool
whose name is one of the action's aliases (`transfer_funds`, `initiate_transfer`, ... for
`fund_transfer`). The grant is checked first (`direct_tool_call_permitted`, runtime
`conversation`); a refused call never reaches the connector. The tool is built and invoked exactly
as the agent graph would build it (`build_tools_for_agent`), so the gateway's own checks, the tool
registry and audit apply. A read intent with no bound tool is left to the agent.

## Sessions and the API

The dialogue lives in `conversation_sessions` (migration `v6z60_conversation_sessions`) under one
key per channel, company, agent and user; a dialogue idle for 30 minutes starts over.

- `POST /conversation/turns` with `text`, `company_id`, `agent_id` and `channel`: one turn, with
  the answer, the outcome, the dialogue state and the tool call when an action ran.
- `GET /conversation/intents`: the catalogue and the action aliases.
- `GET /conversation/session` and `DELETE /conversation/session`: the caller's own dialogue, and a
  reset.
- `POST /chat/query` handles a banking message the same way and returns the outcome in
  `conversation`; any other message reaches the agent as before.
