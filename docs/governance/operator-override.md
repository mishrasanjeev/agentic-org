# Operator override

An operator override lets an administrator halt or throttle, in real time, a
model provider, a model, one agent, every agent, a workflow definition, a
connector, one tool or the whole tool pipeline. It is the platform's emergency
control for AI workloads: when something must stop now, the override stops it
at every point where work is dispatched, records who stopped it and why, and
keeps the stop in force until it is released or expires.

## Turning it on

The control ships off. Two switches turn it on:

- `AGENTICORG_OPERATOR_OVERRIDE_ENABLED=true` for the whole deployment, or
- the authority flag `operator_override.enabled` for one tenant, set by a
  platform operator:

```bash
python scripts/authority_flags.py set operator_override.enabled --tenant <tenant uuid> --operator <name>
```

With the control off `check` allows everything and reads nothing. With it on,
every enforcement point reads the tenant's active overrides (Redis cache of
five seconds, then the database) before dispatching work.

## Placing and releasing an override

Tenant administrators (`agenticorg:admin`) use the API; every change writes a
signed audit row (`operator_override.set`, `operator_override.released`).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/operator-overrides` | Active overrides (`?include_released=true` for history). |
| `GET` | `/api/v1/operator-overrides/status` | Whether the control is on for the tenant and what is active. |
| `POST` | `/api/v1/operator-overrides` | Place an override. |
| `POST` | `/api/v1/operator-overrides/{id}/release` | Release it. |

Request body for `POST`:

```json
{
  "target_kind": "provider",
  "target_id": "gemini",
  "mode": "throttle",
  "limit_per_minute": 30,
  "reason": "Provider incident INC-2041",
  "expires_at": "2026-10-02T18:00:00Z"
}
```

`target_kind` is one of `provider`, `model`, `agent`, `all_agents`, `workflow`,
`connector`, `tool` or `tool_pipeline`. `all_agents` and `tool_pipeline` take no
`target_id`. A `tool` target is `<tool>` or `<connector>:<tool>`. `mode` is
`halt` or `throttle`; a throttle needs `limit_per_minute` (0 blocks everything).
`expires_at` is optional; an expired override stops applying without a release.

## What each override stops

| Target | Enforced at |
|---|---|
| `provider`, `model` | `LLMRouter._call_model` (agents and generators) and the LangGraph reason node before every model call. A block never falls back to another model. |
| `agent`, `all_agents` | The HTTP run endpoint (423), the LangGraph runner and resume path (result status `operator_override`), `BaseAgent.execute` (failed result, code `E1012`), and the agent's tool calls. |
| `workflow` | The HTTP run endpoint (423) and the workflow engine before every step: the run keeps status `running` and retries the step every five seconds until the override is released or the run is cancelled. |
| `connector`, `tool`, `tool_pipeline` | The connector dispatch boundary, `ToolGateway.execute` and the agent tool path. The refusal is audited (`action=operator_override`, `outcome=blocked`). |

Provider names are normalised, so `claude` and `anthropic`, or `gpt`, `openai`
and `azure_openai`, name the same provider.

## Decision rules

- A halt beats a throttle when several overrides match one call, and halts are
  re-checked at every enforcement point.
- A throttle is a fixed one-minute window per override, counted in Redis across
  replicas, and counts one logical dispatch: an agent throttle once per agent run,
  a provider or model throttle once per model call, a connector, tool or
  pipeline throttle once per tool call, a workflow throttle once per workflow
  step. The early HTTP refusals apply halts only. In a strict runtime a Redis
  failure blocks the call; a relaxed runtime falls back to an in-memory window.
- The authority flag is read strictly: with the deployment switch off, a flag
  store that cannot be read blocks the call in a strict runtime rather than
  reading as "control off".
- Every change is attributed to the authenticated principal (`user:<id>` for a
  human administrator, `<auth mode>:<subject>` for an API key or agent grant);
  a request with no attributable caller is refused.
- In a strict runtime a failure to read the overrides blocks the call (fail
  closed); a relaxed runtime allows it and logs `operator_override_read_failed`.
- Blocks are counted in `agenticorg_operator_override_blocks_total{target_kind,mode}`.

## Relationship to other controls

- The per-agent pause (`POST /agents/{id}/pause`) changes the agent's status; an
  override leaves status alone and is lifted by a release, so it suits incidents
  and drills. Both are audited.
- Grantex emergency stops revoke the authority an agent acts under; the tool
  gateway honours them through grant enforcement. An override is the platform's
  own stop and works without a grant check.
- Budget caps and the provider daily spend cap block on cost; an override blocks
  on an administrator's decision.

## Runbook: halt everything

1. `POST /api/v1/operator-overrides` with `{"target_kind": "all_agents", "mode": "halt", "reason": "..."}` and again with `{"target_kind": "tool_pipeline", "mode": "halt", "reason": "..."}`.
2. Confirm with `GET /api/v1/operator-overrides/status`; watch `agenticorg_operator_override_blocks_total` rise.
3. Running workflows report `halted` and wait; cancel the ones that must not resume.
4. Release each override when the incident is over; the audit rows record the window.
