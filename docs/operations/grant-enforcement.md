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

In `warn` (and later `deny`) each run resolves a grant token, first match wins:

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

`runtime` says which path made the call: `langgraph` for agent graph tool
calls, `deterministic_tds` for the shadow-sample TDS route on
`POST /agents/{id}/run`.

## Coverage

This release checks tool calls from `POST /agents/{id}/run` and from every
caller of `core.langgraph.runner.run_agent` that does not pass its own grant.
Other run entry points are covered separately; until then they keep the
legacy behaviour.
