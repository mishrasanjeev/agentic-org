# Agent registry

Each agent has a card and a place in a governance lifecycle, kept apart from its runtime status;
with the approval workflow on, production follows the lifecycle, and a traffic split can send a
share of an agent's runs to another. The catalogue with templates, the dependency graph and the
reliability metrics are not here yet (see the end of this page).

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
- **A new or cloned agent does not start active.** It has no entry yet, so nobody has approved
  it: `POST /agents` and `POST /agents/{id}/clone` with `initial_status: "active"` are refused
  (`409`, `agent_registry`, `not_approved`) and the agent is created in shadow.
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
`{"split": {"to_agent_id": ..., "percent": 1-100}}`, which requires the target to be active;
`{"split": null}` removes it. `GET /agents/{id}/traffic-split` reads it.

With `AGENTICORG_AGENT_TRAFFIC_SPLIT_ENABLED` on, that share of the runs asked of the agent
through `POST /agents/{id}/run` are served by the target instead. The choice is made from the
run's `thread_id` or `correlation_id` when the request carries one, so a retry lands on the same
agent and the share is reproducible; otherwise it is random, one draw per run. The agent asked
for is held to its own controls first (a paused or retired agent, one below its production floor
or halted by an operator override is refused before any redirection); the target must be active,
visible to the caller and pass the same controls at run time, otherwise the run stays on the
agent asked for and the skip is logged. The response carries `requested_agent_id`, the
`agent_id` that served the run, and
`served_by` (`traffic_split:<percent>` or null). Removing the split is the rollback: one action,
and every run returns to the agent asked for.

The split applies to runs through the agents API only; chat, voice, workflows and A2A pick their
agent as before. It splits between two agents, not between two stored versions of one agent.

## Storage

`agent_registry` (one row per agent) and `agent_registry_events`, both tenant-scoped under
row-level security (`v6z48_agent_registry`); both are removed with the agent.

## What is not here yet

- **Environments are derived, not deployed.** There is one runtime; `staging` and `production`
  name where an agent stands in the lifecycle, not separate infrastructure.
- **A split is between two agents.** Splitting traffic between two stored versions of one agent
  is not available; clone the agent to compare versions.
- **No catalogue page, templates, dependency graph or ratings.** The list endpoint is the
  catalogue's data only.
- **No console.** The card, the lifecycle and the split are read and changed through the API.
