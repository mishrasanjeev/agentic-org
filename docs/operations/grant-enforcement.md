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
| `deny` | Refused with a reason code: the run fails, the result carries `grant_denial`, the run's audit row records it | `grant_enforcement_denied` log event, counter with `mode="deny"` |

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

In `warn` and `deny` each run resolves a grant token, first match wins:

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
| Chat (`POST /chat/query`) | the routed agent's grant; a caller Grantex token issued to that agent is used as it, a caller token for any other agent must **also** allow every call | `langgraph`; `deterministic_tds` for the TDS route |
| A2A (`POST /a2a/tasks`), MCP (`POST /mcp/call`) | the grant of the shared agent of that type the route takes connector bindings from; a caller token is bound the same way as for chat | `langgraph` |
| Voice, per-type wrappers (`core/langgraph/agents/*`) and any other caller of `core.langgraph.runner.run_agent` | the runner resolves it from the agent id, with the caller's token first | `langgraph` |
| `core.langgraph.runner.resume_agent` | resolved again on resume; in warn/deny the fresh token replaces the checkpointed one | `langgraph` |
| Workflow agent steps, collaboration steps, workflow resume (Celery `resume_workflow_wait`, HITL resume), sales pipeline | `BaseAgent` resolves the agent's grant on its first tool call; calls go through `execute_agent_tool` or the `ToolGateway` | `base_agent`, `tool_gateway` |
| Workflow `connector_tool` steps | none — the step is not made by an agent | `workflow_connector_tool` |

Paths that cannot resolve a grant are not exempt: a workflow `connector_tool`
step, a workflow agent step whose agent is not stored, and an A2A/MCP call for
a type with no shared agent all have no grant, so each tool call is recorded as
`grant_missing` (`no_agent`) in warn and refused in deny. Check the warn-mode
report for these before moving a tenant to deny.

**Caller tokens never stand in for the run agent.** A Grantex token a request
authenticated with belongs to the agent it was issued to
(`agenticorg:agent_id`). When that is the agent the run executes as, it is the
run grant. Otherwise the run agent's own grant is resolved as usual and every
tool call must be allowed by both: the caller token strictly (as before), the
run agent's grant in the tenant's mode. A caller can therefore never run
another agent - or an A2A/MCP agent type with its default tools - on the
strength of its own scopes. A denial by the caller token is logged with
`grant_source=caller`.

In the `ToolGateway` the grant check runs first and then every legacy check
runs exactly as in `off`, including strict enforcement of a token passed to the
gateway, so enforcement never skips or downgrades a check `off` makes.

## What a denial looks like (deny)

A refused tool call stops the run before the connector is called.

- **LangGraph runs** (`POST /agents/{id}/run`, chat, A2A, MCP, voice): status
  `failed` (never routed to human review, whatever the confidence floor),
  `error` `grant_denied: <reason>`, and a `grant_denial` object in
  the run result — `reason`, `sub_reason`, `grant_id`, `connector`, `tool`.
  `POST /agents/{id}/run` returns `grant_denial` in its response and writes it
  into the details of the run's `agent.run` audit row.
- **`BaseAgent` runs** (workflow steps, collaboration, sales) and the tool
  gateway: the tool result is
  `{"error": {"code": "E1007", "message": "grant_denied: <reason>", "reason": ..., "sub_reason": ...}}`
  and the step fails. The gateway also writes a `scope_denied` audit row with
  the reason, sub-reason and grant id.

## Runbook

### 1. Put a tenant in warn and read the warnings

Enable `grants.enforce_closed.warn` for the tenant (see "Choosing the mode").
Every tool call the grant would deny is logged. In Cloud Logging:

```
jsonPayload.event="grant_enforcement_would_deny"
jsonPayload.tenant_id="<tenant id>"
```

Watch `sum by (reason) (rate(agenticorg_grant_enforcement_denials_total{mode="warn"}[1h]))`
for the whole deployment. Common patterns:

| You see | Usually means | Do |
|---|---|---|
| `grant_missing` / `minting_unconfigured` | `GRANTEX_ROOT_GRANT_TOKEN` or `GRANTEX_API_KEY` not set | Configure the root grant |
| `grant_missing` / `agent_not_registered` | Agent created without a Grantex registration | Re-register the agent |
| `grant_missing` / `mint_failed` | Root grant expired or revoked, or Grantex unreachable | Rotate the root grant; check Grantex |
| `grant_missing` / `no_agent` | Workflow connector step or type with no stored agent (FINDINGS A-35) | Give the step a stored agent or accept it fails in deny |
| `tool_not_granted` on every call of an agent | Registered scopes the grant cannot satisfy (FINDINGS A-32) | Fix the registration scopes |
| `permission_insufficient` | The agent is registered for `read` but calls a write tool | Decide whether the agent should have the permission |
| `manifest_unknown_tool` | No Grantex manifest for the connector or tool | Add a manifest (`GRANTEX_MANIFESTS_DIR`) |
| `enforcement_unavailable` | The check could not run | Fix the error named in `sub_reason` |

### 2. Produce the per-tenant report

`scripts/grant_enforcement_report.py` counts `grant_enforcement_would_deny` and
`grant_enforcement_denied` events per tenant, by reason and by
(connector, tool, reason, agent type), with first and last seen times. It reads
raw JSON log lines or a Cloud Logging export:

```
gcloud logging read 'jsonPayload.event=~"^grant_enforcement_"' --freshness=7d --format=json   | python scripts/grant_enforcement_report.py --tenant <tenant id> -
python scripts/grant_enforcement_report.py --format json api.log
```

### 3. Flip the tenant to deny

Flip only when, over at least a week of representative traffic in warn:

- the report shows no `grant_missing` for agents the tenant relies on, and
- every remaining `would_deny` row is a call that *should* be refused.

Then enable `grants.enforce_closed.deny` for the tenant (leave the warn flag
as it is; deny wins). The change applies to runs that start after the flag
cache expires (up to 30 seconds per process). Watch
`agenticorg_grant_enforcement_denials_total{mode="deny"}` and failed runs for
the tenant.

### 4. Roll back

- One tenant: disable (or delete) the tenant's `grants.enforce_closed.deny`
  flag. With the warn flag still on the tenant returns to warn; delete both to
  return to `off`.
- The whole deployment: set `AGENTICORG_GRANTS_ENFORCE_CLOSED` back to `warn`
  or `off` and redeploy. Tenant `deny` flags still apply, because flags can only
  make a tenant stricter; disable them too for a full rollback.
- Runs already refused are not retried automatically; re-run them after the
  rollback.
