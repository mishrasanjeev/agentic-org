# Regulatory risk tiers

An agent's registry card carries a risk tier: `low`, `medium`, `high` or `critical`. With
`AGENTICORG_GOVERNANCE_RISK_TIERS_ENABLED` on (`core/governance/risk_tiers.py`), the tier decides
what must be true before the agent runs in production, and what its owner may change:

| Tier | Forced before promotion or resume to `active` |
|---|---|
| `low` | nothing beyond the platform's own checks |
| `medium` | the registry has approved the agent (a second person's decision) |
| `high` | the above, plus an evaluation gate declared and passed, a human oversight condition (the HITL condition is set and not `never`), and at least 50 human-scored shadow samples |
| `critical` | the above with at least 200 scored samples, plus maker-checker on prompt changes for the tenant |

The checks run whatever the separate registry, evaluation and maker-checker switches say, so an
agent owner cannot bypass them by leaving a gate unconfigured: a promotion or resume that fails one
is refused (`409`, `risk_tier`, `<requirement>_required`) and names the requirement and what was
found.

Tier changes are a tenant administrator's: an owner who is not an administrator cannot change a
tier (`403`, `admin_only`), and lowering a `high` or `critical` tier needs an administrator other
than the agent's owner (`403`, `second_person`). On a `high` or `critical` agent, an update that
sets the HITL condition to `never` and a request that removes the evaluation gate are refused
(`human_oversight_required`, `eval_gate_required`).

`GET /governance/risk-tiers` (tenant-admin only) returns the policy, every agent with its tier,
registry state, compliance and unmet requirements, the counts by tier and the number of
non-compliant agents, so oversight per tier is one page.

Off, nothing here runs and the switches that exist today decide alone. The policy console is the
last part of this package.
