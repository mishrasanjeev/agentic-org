# Agent registry

Each agent has a card and a place in a governance lifecycle, kept apart from its runtime status;
with the approval workflow on, production follows the lifecycle, and a traffic split can send a
share of an agent's runs to another. The catalogue lists every agent by what it is for, and the
industry packs offer templates. The dependency graph and the reliability metrics are not here yet
(see the end of this page).

Behind `AGENTICORG_AGENT_REGISTRY_ENABLED`, off by default. Off, the endpoints answer 409 and
nothing is written, and the runtime is not affected by the registry.

## The card

`GET /agents/{id}/card` assembles what a reviewer, an auditor or another team needs to know about
an agent (`core/agent_registry/lifecycle.py`):

| Section | Contents |
|---|---|
| identity | id, name, type, domain, description, version, runtime status, maturity, visibility, owner, tags |
| registry | purpose, risk tier, use case, channels, lifecycle state with when and by whom, the next states |
| models | the model, provider, fallback and routing policy |
| tools, permissions | the authorised tools; the Grantex scopes and route scopes it carries and its connectors |
| schemas | the registered output schema name and whether the agent has its own schema |
| prompt | the template reference, the SHA-256 of the prompt text, the number of variables and amendments; never the text |
| controls | confidence floor, review condition, cost controls |
| evaluation_gate | the declared gate and its verdict (`docs/governance/evaluation-datasets.md`) |

The fields an administrator writes come from `PUT /agents/{id}/card`: `purpose` (at most 2000
characters), `risk_tier` (`low`, `medium`, `high`, `critical`, the guardrail tiers), `use_case`
and `channels` (`api`, `chat`, `voice`, `email`, `workflow`, `a2a`). Only the fields sent are
changed; the registry entry is created as `draft` on first use. The agent's edit rules apply.
Unknown card, lifecycle and traffic-split fields are rejected, rather than ignored. Card writes
lock the parent agent so two first-time updates cannot race to create the same registry entry.

## Lifecycle

The governance state of an agent:

```
draft -> review -> approved -> published -> deprecated -> retired
```

with `review -> draft` (withdrawn or sent back), `approved -> draft` (a change after approval
starts again), `approved -> review` and `published -> deprecated`. `retired` is final.

`POST /agents/{id}/lifecycle` with `to` and an optional `note` moves the agent under that table,
and records who moved it. Two rules hold at the transition:

- **The submitter cannot approve.** The person who moved the agent into `review` is recorded, and
  `approved` is refused to that person (`same_person`).
  The lifecycle API requires an authenticated human identity, not an API key or agent token.
  A review with no recorded human submitter must be resubmitted before approval. Supplying a
  user-id claim on a machine credential does not turn it into a human approval.
- **Only an active agent is published.** `published` says the agent is in production; it is
  refused while the agent's runtime status is not `active` (`not_active`). Promotion to active has
  its own checks (shadow evidence, maker-checker on the prompt, the evaluation gate).

`GET /agents/{id}/lifecycle` returns the state, the transitions newest first, and the table.
`GET /agent-registry?state=&risk_tier=` lists the entries the caller may see with each agent's
name, type, domain and runtime status.

The runtime status and the lifecycle state answer different questions: whether the agent runs
(shadow, active, paused), and whether it has been reviewed and approved for what it does. The
approval workflow below ties them.

## Approval workflow and environments

With `AGENTICORG_AGENT_REGISTRY_GATES_PROMOTION` on (beside the registry switch), the two
follow each other (`core/agent_registry/approval.py`):

- **Promotion and resume to `active` need an `approved` or `published` entry.** The check runs
  after the shadow evidence, the maker-checker check on the prompt and the evaluation gate, so a
  refusal (`409`, `agent_registry`, `not_approved`) names the first thing that is missing. An
  agent with no entry is a draft and is refused.
- **Creation and cloning cannot start active.** With the registry gate on, create in shadow,
  review the new agent independently, then promote. Clones do not inherit the source approval.
- **Promotion publishes.** When an `approved` agent becomes active, its entry moves to
  `published` with a recorded transition.
- **Retirement retires.** When a `published` or `deprecated` agent is retired at runtime, its
  entry moves to `retired` (through `deprecated` when it was published). A draft that is retired
  keeps its state: it was never in production.

Off, the registry neither gates nor follows; the lifecycle is what administrators make of it.

**Environments** are read from the state and not stored: `draft` and `review` are
`development`, `approved` is `staging`, `published` and `deprecated` are `production`, `retired`
has none. The card, the lifecycle and the list carry `environment`.

## Traffic split

An agent may send a share of its runs to another agent of the tenant
(`core/agent_registry/traffic.py`): `PUT /agents/{id}/traffic-split` with
`{"split": {"to_agent_id": ..., "percent": 1-100}}`, which requires both agents to be active
and the target to be visible to the caller;
`{"split": null}` removes it. `GET /agents/{id}/traffic-split` reads it.

With `AGENTICORG_AGENT_TRAFFIC_SPLIT_ENABLED` on, that share of the runs asked of the agent
through `POST /agents/{id}/run` are served by the target instead. The choice is made from the
run's `thread_id` or `correlation_id` when the request carries one, so a retry lands on the same
agent and the share is reproducible; otherwise one random draw is made per request. The target must be active and
visible to the caller at run time; otherwise the run stays on the agent asked for and the skip is
logged. The response carries `requested_agent_id`, the `agent_id` that served the run, and
`served_by` (`traffic_split:<percent>` or null). Removing the split is the rollback: one action,
and every run returns to the agent asked for.

The requested agent's status, production accuracy floor and operator override are checked
before allocation. A selected target gets the same controls before execution. A shadow source
never redirects to a live target; a malformed stored split is ignored with a warning. Paused
agent refusal still follows `AGENTICORG_PAUSED_AGENTS_REFUSED`; splitting does not override it.
Use explicit `{"split": null}` to remove a split; an empty or misspelled request is rejected.

The split applies to runs through the agents API only; chat, voice, workflows and A2A pick their
agent as before. It splits between two agents, not between two stored versions of one agent.

## Catalogue and templates

`GET /agent-registry` is the catalogue: the registry entries the caller may see, each with the
agent's name, type, domain and runtime status beside its card fields, state and environment.
Filters: `state`, `risk_tier`, `use_case`, `channel` (stored on the entry), `domain` (on the
agent) and `q`, a search term matched against the name, type, description, purpose and use
case. The console page **Agent catalogue** (`/dashboard/agent-catalogue`) shows the table with
those filters and opens an agent's page from its keyboard-accessible name link.
Visibility and search filters apply before the 500-result limit. Search treats
percent signs and underscores literally. Superseded requests cannot overwrite
the current search, and a failed request clears the previous results.

`GET /agent-registry/templates?pack=` lists the agent templates the industry packs offer, in the
card's terms: pack, type, domain, model, tools, review condition, confidence floor and the pack's
compliance markers, with whether the pack can be installed. Installing a pack (Industry Packs)
creates its agents in shadow mode. A card must first be saved through
`PUT /agents/{agent_id}/card` for its registry entry to appear in the catalogue
as a draft; installing a pack alone does not create registry entries or approvals.

**Banking pack.** Five templates for retail and SME banking operations, each with a review
condition and a confidence floor of at least 85%, using only tools the platform has:

| Template | Domain | What it does | Goes to a human when |
|---|---|---|---|
| Loan underwriting analyst | finance | credit assessment against the credit policy in the knowledge base | any approval recommendation, exposure above the limit, any policy exception |
| KYC reviewer | ops | document checks against the KYC checklist with a risk rating | any outcome other than a clear, low-risk file |
| Collections agent | finance | reminders under the fair practices code with a payment link | over 60 days past due, above the amount limit, hardship indicated |
| Complaint handler | ops | classification, acknowledgement within the redressal timelines, a draft resolution | fraud, unauthorised transactions, regulatory, an escalation request |
| Bank reconciliation analyst | finance | statement-to-ledger matching with proposed adjustments | any unmatched item or variance |

Every prompt is synthetic, names its tools, returns one JSON object and leaves the decision to a
human. The pack is a starting point: a bank's own policies come from its knowledge base, and the
thresholds are for an authorized administrator to review and configure. Compliance
markers are template topics, not evidence of certification or regulatory approval.
The collections-review and bank-reconciliation workflows install with manual
triggers. Their names describe a potential daily process, not an enabled schedule.
Installation does not run tools, provision credentials, approve an agent, or create
a payment. Existing connector, runtime, shadow-mode and human-review controls apply.

## Storage

`agent_registry` (one row per agent) and `agent_registry_events`, both tenant-scoped under
row-level security (`v6z48_agent_registry`); both are removed with the agent.

`v6z49_registry_from_state` adds the event source-state constraint even when a
database already applied v6z48. It is repeatable and never rewrites audit rows.
If legacy rows contain invalid source states, new writes are still constrained
but the constraint remains `NOT VALID` until an operator reviews that history.
Rollout checks must inspect constraint validation, not just the Alembic version.
Downgrading v6z49 removes only this check; it does not remove events or registry data.

## What is not here yet

- **Environments are derived, not deployed.** There is one runtime; `staging` and `production`
  name where an agent stands in the lifecycle, not separate infrastructure.
- **A split is between two agents.** Splitting traffic between two stored versions of one agent
  is not available; clone the agent to compare versions.
- **No dependency graph or ratings.**
- **The card, the lifecycle and the split are read and changed through the API**; the console
  has the catalogue only.
