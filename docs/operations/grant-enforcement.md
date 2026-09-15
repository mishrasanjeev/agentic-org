# Grant enforcement (`grants.enforce_closed`)

Every tool call an agent makes can be checked against a Grantex grant: the
grant token must verify, the tool's connector must be granted, and the granted
permission must cover the tool. Until this setting existed that check only ran
when an agent happened to have a token in `config.grantex.grant_token`, which
registration never set, so on the common path it did not run at all. This page
covers how the check is switched on, what it records and how to read it.

## Modes

| Mode | A call the grant does not cover | Recorded as |
|---|---|---|
| `off` (default) | Legacy behaviour: no check without a token; a configured token is enforced as before | nothing new |
| `warn` | Runs, unless the token was supplied by the caller or configured on the agent (those were already enforced in `off`, so they stay enforced) | `grant_enforcement_would_deny` log event, counter with `mode="warn"`; a refused call is `grant_enforcement_denied` with `mode="deny"` |
| `deny` | Not available yet — a requested `deny` runs as `warn` and logs `grant_enforcement_deny_unavailable` | — |

## Choosing the mode

The mode is resolved per tenant at the start of each run as the strictest of:

- the deployment default `AGENTICORG_GRANTS_ENFORCE_CLOSED` (`off`, `warn` or
  `deny`; default `off`; any other value fails startup),
- the global rows of `grants.enforce_closed.warn` and `grants.enforce_closed.deny`, and
- the tenant's rows of the same two flags.

Each row is evaluated on its own (enabled and rollout percentage, bucketed by
tenant id), so a tenant row never hides a global row: a global
`grants.enforce_closed.deny` with a disabled tenant row still resolves to
`deny`. Deny wins over warn.

**These keys are reserved for platform operators.** `POST` and `DELETE
/api/v1/feature-flags` refuse them with `403 flag_key_reserved`, even for a
tenant admin, so nobody inside a tenant can switch enforcement off or delete a
mode an operator set. The same applies to the programme's other authority
flags (`pseudonymisation.pre_model`, `approvals.resume_agent_runs`,
`decisions.required`, `caps.enforce`; see `RESERVED_FLAG_KEYS` in
`core/feature_flags.py`). Operators manage them with database access:

```
python scripts/authority_flags.py set grants.enforce_closed.warn --tenant <tenant id>
python scripts/authority_flags.py set grants.enforce_closed.deny --global
python scripts/authority_flags.py clear grants.enforce_closed.deny --tenant <tenant id>
python scripts/authority_flags.py list --tenant <tenant id>
```

Changes reach running processes within 30 seconds (the flag cache TTL).

### When the flag table cannot be read

The run uses the stricter of the deployment default and the tenant's last mode
resolved by that process within the past hour. If the process has no such
mode (a restart, a new worker, or more than an hour since the last successful
read) the run resolves to **`deny`**. Either way it logs
`grant_enforcement_mode_lookup_failed` (`reason_code=flag_store_unreadable`)
and increments `agenticorg_grant_enforcement_mode_fallbacks_total{outcome}`
(`last_known` or `deny`). Failed reads are never cached, so the next run
retries the table.

## Where the run's grant comes from

In `warn` (and later `deny`) each run resolves a grant token, first match wins:

1. `supplied` — a token the caller already holds;
2. `agent_config` — the agent's legacy `config.grantex.grant_token`;
3. `pool_cache` / `minted` — a per-run grant from `auth/token_pool.py`: the
   pool delegates a grant from the platform root grant to the agent's
   registered Grantex agent (`config.grantex.grantex_agent_id`), limited to its
   registered scopes (`config.grantex.grantex_scopes`), and caches it per
   tenant, agent and scope set.

Lifetime and bounds of pool grants:

- A cached grant is handed out only while it has at least the larger of 120
  seconds and 10% of its requested lifetime left. Long runs (LangGraph and
  `BaseAgent`) ask the pool again before each tool call once the grant they hold
  falls below that, so a run never carries an expiring grant into a call.
- The pool does not revoke the grants it mints; they expire (default 15
  minutes, `AGENTICORG_GRANTS_RUN_TOKEN_TTL_SECONDS`). Revoking the root grant
  on Grantex revokes every grant delegated from it.
- At most one grant is minted per tenant, agent and scope set per process while
  a usable one exists: Redis shares grants across API and worker processes, a
  bounded in-process cache (1,024 entries) covers Redis being unavailable, and a
  per-key lock stops concurrent runs from minting in parallel.
- The API creates the pool's Redis client at startup; Celery workers create
  theirs lazily on first use, so worker start-up never waits on Redis.

The grant token is part of the agent graph state. Checkpoints written by the
sealed checkpoint store are encrypted as a whole, and when LangSmith tracing is
enabled through the environment the platform installs a client that replaces
`grant_token` (and other token keys) with `[redacted]` in traced inputs and
outputs.

Minting needs:

| Setting | Meaning |
|---|---|
| `GRANTEX_API_KEY` | Grantex SDK key (already required) |
| `GRANTEX_ROOT_GRANT_TOKEN` | Root grant the per-run grants are delegated from. A credential: inject it from the secret manager, never commit it |
| `AGENTICORG_GRANTS_RUN_TOKEN_TTL_SECONDS` | Lifetime requested for a per-run grant (default 900, 60–86400; Grantex caps it at the root grant's expiry) |

When no token can be resolved the run still starts and each tool call it makes
is recorded as `grant_missing` with a sub-reason:

| Sub-reason | Meaning |
|---|---|
| `agent_not_registered` | The agent has no Grantex agent id or no registered scopes |
| `minting_unconfigured` | No root grant or no Grantex client configured |
| `mint_failed` | Grantex refused or failed the delegation (for example an expired root grant) |
| `lookup_failed` | The agent's Grantex registration could not be read |
| `no_agent` | The call is not made by a stored agent, so there is nothing to resolve a grant for (see Coverage) |

The token itself is never logged or put in a metric.

## What is recorded

Every call the grant would deny produces one structured log event:

```
event=grant_enforcement_would_deny mode=warn reason=tool_not_granted sub_reason=
grant_id=grnt_... tenant_id=... agent_id=... agent_type=ap_processor
runtime=langgraph grant_source=minted connector=hubspot tool=create_contact
```

and increments `agenticorg_grant_enforcement_denials_total{mode, reason}`.
`reason` is one of:

| Reason | Meaning |
|---|---|
| `grant_missing` | The run has no grant token (see sub-reasons above) |
| `token_invalid` | The token does not verify (signature, expiry, issuer, shape) |
| `grant_revoked` | The grant has been revoked |
| `tool_not_granted` | No scope grants the tool's connector or tool |
| `permission_insufficient` | The granted permission does not cover the tool (for example `read` for a write tool) |
| `cap_exceeded` | A call cap, budget or amount cap would be exceeded |
| `purpose_not_allowed`, `decision_required`, `decision_invalid`, `region_mismatch` | Purpose, decision-grant and region checks (Grantex manifest 0.6) |
| `manifest_unknown_tool` | No manifest for the connector (`unknown_connector`) or the tool is not in it (`unknown_tool`) |
| `enforcement_unavailable` | The check itself could not run (`sub_reason` is the error type, for example `ValueError` when `GRANTEX_API_KEY` is missing) |
| `unclassified` | Grantex denied without a reason code this platform knows (`no_reason_code` for an SDK older than reason codes, `unknown_reason_code` otherwise). Still a denial; the event carries Grantex's text as `sdk_reason` |

Reasons are the Grantex SDK's `reason_code` values, used exactly; the
platform never infers a reason from the SDK's message text. The pinned SDK
(`grantex==0.5.0`) predates reason codes, so until it is upgraded every
Grantex denial is recorded as `unclassified`.

`runtime` says which path made the call (see Coverage).

## Coverage

Every path that starts an agent run resolves its grant and checks each tool
call in the tenant's mode:

| Entry point | How the grant is resolved | `runtime` |
|---|---|---|
| `POST /agents/{id}/run` | the agent's grant, resolved once for the run | `langgraph`; `deterministic_tds` for the shadow TDS route |
| Chat (`POST /chat/query`) | the caller's Grantex token, else the routed agent's grant | `langgraph`; `deterministic_tds` for the TDS route |
| A2A (`POST /a2a/tasks`), MCP (`POST /mcp/call`) | the caller's Grantex token, else the grant of the shared agent of that type the route takes connector bindings from | `langgraph` |
| Voice, per-type wrappers (`core/langgraph/agents/*`) and any other caller of `core.langgraph.runner.run_agent` | the runner resolves it from the agent id, with the caller's token first | `langgraph` |
| `core.langgraph.runner.resume_agent` | resolved again on resume; in warn/deny the fresh token replaces the checkpointed one | `langgraph` |
| Workflow agent steps, collaboration steps, workflow resume (Celery `resume_workflow_wait`, HITL resume), sales pipeline | `BaseAgent` resolves the agent's grant on its first tool call; calls go through `execute_agent_tool` or the `ToolGateway` | `base_agent`, `tool_gateway` |
| Workflow `connector_tool` steps | none — the step is not made by an agent | `workflow_connector_tool` |

Paths that cannot resolve a grant are not exempt: a workflow `connector_tool`
step, a workflow agent step whose agent is not stored, and an A2A/MCP call for
a type with no shared agent all have no grant, so each tool call is recorded as
`grant_missing` (`no_agent`) in warn and refused in deny. Check the warn-mode
report for these before moving a tenant to deny.

In the `ToolGateway`, a call warn allows without the grant covering it still
goes through the gateway's legacy scope checks, so warn never skips a check
`off` makes.
