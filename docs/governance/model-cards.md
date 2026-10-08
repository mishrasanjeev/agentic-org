# Model cards

With `AGENTICORG_GOVERNANCE_MODEL_CARDS_ENABLED` on, every model a tenant uses has one standard
card (`core/governance/model_cards.py`), assembled live from what the platform already knows and
the part an administrator writes:

| Section | What it holds | Where it comes from |
|---|---|---|
| facts | kind (`llm`, `embedding`), context window, output limit, tools, vision, dimensions; `in_catalogue` | the provider catalogue (`core/ai_providers/catalog.py`) |
| use | the roles the model plays (default, fallback, embedding, agent), the agents that call it with their risk tier, the highest tier | the AI inventory |
| governance | the routing and access policies that name it, the limits that apply, the residency decision for its provider | the model gateway and data residency |
| economics | the list or negotiated price per million tokens | model pricing |
| operations | calls, failures, failure rate, latency and cost over the quality window | the routing records |
| evaluation | the newest stored evaluation run of the model (pass rate, accuracy, latency, cost per case, judge scores) | the evaluation framework |
| written | intended use, limitations, data handling, notes, the owner, the approval | the administrator |

`completeness` names what the card lacks: a missing text, an owner (the written owner or whoever
last changed the tenant's AI settings), a catalogue entry, and the approval. A card is complete
only when a second person has approved it: the person who last edited the card cannot approve it,
and any later edit returns the card to draft.

Endpoints (tenant-admin only, audited; model names may contain a slash, such as `BAAI/bge-m3`):

- `GET /governance/model-cards`: one summary per model the tenant uses, with `incomplete` counted.
- `GET /governance/model-cards/{provider}/{model}`: the full card.
- `PUT /governance/model-cards/{provider}/{model}`: the written part (`intended_use`, `limitations`,
  `data_handling`, `notes`, `owner_user_id`; each text at most 2000 characters). Returns the card
  to draft.
- `POST /governance/model-cards/{provider}/{model}/approve`: approval by a second person; refused
  for the last editor (`403`, `second_person`) and for an incomplete card (`409`, `incomplete`).

The written part is stored in `model_cards` (one row per tenant and model, under a row-level
policy); everything else is read on each request. Off, the endpoints are not found and nothing
here reads or writes. Regulatory risk tiers with their forced gates and the policy console are the
next parts of this package.
