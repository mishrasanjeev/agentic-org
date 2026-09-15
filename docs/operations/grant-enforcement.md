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

The mode is resolved per tenant at the start of each run as the stricter of:

- the deployment default `AGENTICORG_GRANTS_ENFORCE_CLOSED` (`off`, `warn` or
  `deny`; default `off`; any other value fails startup), and
- the tenant's flags: `grants.enforce_closed.deny` enabled → `deny`, otherwise
  `grants.enforce_closed.warn` enabled → `warn`.

Both flags live in the existing `feature_flags` table and are managed with
`POST /api/v1/feature-flags` (tenant admins) or a global row. A tenant row wins
over the global row, and a rollout percentage is evaluated against the tenant
id, as for any other flag. Deny wins when both flags are on. Because tenant
admins manage their own flags, flags only ever make enforcement stricter than
the deployment default; a tenant cannot opt out of a `warn` or `deny`
deployment default.

For example, to put one tenant in warn mode while the deployment stays `off`:

```
POST /api/v1/feature-flags
{"flag_key": "grants.enforce_closed.warn", "enabled": true, "rollout_percentage": 100}
```

If the flag table cannot be read, the run uses the stricter of the deployment
default and the tenant's last successfully resolved mode (remembered for an
hour per process) and logs `grant_enforcement_mode_lookup_failed`.

## Where the run's grant comes from

In `warn` and `deny` each run resolves a grant token, first match wins:

1. `supplied` — a token the caller already holds;
2. `agent_config` — the agent's legacy `config.grantex.grant_token`;
3. `pool_cache` / `minted` — a per-run grant from `auth/token_pool.py`: the
   pool delegates a grant from the platform root grant to the agent's
   registered Grantex agent (`config.grantex.grantex_agent_id`), limited to its
   registered scopes (`config.grantex.grantex_scopes`), and caches it per
   tenant, agent and scope set (when the pool's Redis is initialised) until a
   minute before it expires.

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
| `token_invalid` | The token does not verify (`sub_reason=expired` or `verification_failed`) |
| `grant_revoked` | The grant has been revoked |
| `tool_not_granted` | No scope grants the tool's connector (`sub_reason=unclassified` when Grantex gave an unrecognised reason) |
| `permission_insufficient` | The granted permission does not cover the tool (for example `read` for a write tool) |
| `cap_exceeded` | The call's amount exceeds the granted cap |
| `manifest_unknown_tool` | No manifest for the connector (`connector_unknown`) or the tool is not in it (`tool_unknown`) |
| `enforcement_unavailable` | The check itself could not run (`sub_reason` is the error type, for example `ValueError` when `GRANTEX_API_KEY` is missing) |

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

## What a denial looks like (deny)

A refused tool call stops the run before the connector is called.

- **LangGraph runs** (`POST /agents/{id}/run`, chat, A2A, MCP, voice): status
  `failed`, `error` `grant_denied: <reason>`, and a `grant_denial` object in
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
| `grant_missing` / `no_agent` | Workflow connector step or type with no stored agent (FINDINGS A-16) | Give the step a stored agent or accept it fails in deny |
| `tool_not_granted` on every call of an agent | Registered scopes the grant cannot satisfy (FINDINGS A-13) | Fix the registration scopes |
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
