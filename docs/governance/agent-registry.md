# Agent registry

Each agent has a card and a place in a governance lifecycle, kept apart from its runtime status.
This is the first part of the registry; the approval workflow with environments, the catalogue
with templates, the dependency graph and the reliability metrics are not here yet (see the end of
this page).

Behind `AGENTICORG_AGENT_REGISTRY_ENABLED`, off by default. Off, the endpoints answer 409 and
nothing is written. The runtime is not affected by the registry either way in this release: an
agent's lifecycle state does not change what it may do.

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
(shadow, active, paused), and whether it has been reviewed and approved for what it does. The next
part ties them: an agent will not be promoted to production without passing the approval workflow.

## Storage

`agent_registry` (one row per agent) and `agent_registry_events`, both tenant-scoped under
row-level security (`v6z48_agent_registry`); both are removed with the agent.

## What is not here yet

- **No approval workflow beyond the two rules above**, and no environments (development,
  staging, production) or traffic allocation between versions.
- **The lifecycle state does not gate promotion.** An agent can be promoted to active in any
  state; publishing follows promotion, not the other way round.
- **No catalogue page, templates, dependency graph or ratings.** The list endpoint is the
  catalogue's data only.
- **No console.** The card and the lifecycle are read and changed through the API.
