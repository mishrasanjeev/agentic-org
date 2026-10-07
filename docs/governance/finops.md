# FinOps

## Use-case attribution

With `AGENTICORG_FINOPS_ATTRIBUTION_ENABLED` on, every cost a run incurs carries where it came
from (`core/finops/attribution.py`). A run binds its attribution for its whole span:

| Dimension | Taken from, in order |
|---|---|
| `use_case` | the caller's `use_case`, the agent's configuration, the agent's type; `unattributed` otherwise |
| `application` | the surface the run came through: `agents`, `chat`, `voice`, `workflows`, `a2a`, `api`, `console` |
| `business_unit` | the caller's `business_unit`, the agent's configuration, the agent's domain |
| `department_id`, `cost_center_id` | the cost centre the agent is charged to and its department |

The labels are bounded, lower-cased identifiers, never a user, a prompt or a document. While the
switch is on:

- the run's cost write adds one row per day, agent and attribution to `finops_cost_ledger`
  (tokens, cost, calls), beside the per-agent ledger that exists today;
- each model call record carries the business unit and application of the run it was made in,
  beside the use case and agent it already carries;
- each tool call row carries the use case and application.

`GET /finops/attribution?days=30&group_by=use_case` (tenant-admin only) folds the ledger by one
dimension (`use_case`, `application`, `business_unit`, `department_id`, `cost_center_id`,
`agent_id`) over the window: tokens, cost, calls and distinct agents per value, the totals, and the
share of cost that is unattributed, so the gap is visible.

Off, nothing here runs: the ledger is not written, the records and tool calls carry what they
carried before, and the endpoint is not found.

## Thresholds and actions

With `AGENTICORG_FINOPS_THRESHOLDS_ENABLED` on, a tenant administrator sets thresholds
(`core/finops/thresholds.py`, `/finops/thresholds`): a scope (the whole organisation, one
application, one use case or one business unit), a period (`daily` or `monthly`), an amount in USD
and an action. Every run through the agents API is checked before it executes: the attributed
ledger's spend for the period is compared with each threshold that matches the run's attribution,
and the strongest breached action wins.

| Action | What happens to the run |
|---|---|
| `alert` | proceeds; the owner is notified once per period |
| `throttle` | proceeds after a short delay (`throttle_seconds`, at most 30) and says so (`finops_action`) |
| `suspend` | refused (`threshold_suspended`, `E1009`) until the period resets, the threshold is disabled, or an administrator lifts it until a time (`lifted_until`) |

A breach is recorded on the threshold (period, time, spend) and the owner notified once per period
through the threshold's channels: `email` to the tenant's earliest active administrator, `log`. A
notification that fails never touches the run. `GET /finops/thresholds` shows every threshold with
its spend, share and breach state. Off, no run is checked, delayed or refused.

## Cost comparison and forecasting

With `AGENTICORG_FINOPS_FORECAST_ENABLED` on (`core/finops/forecast.py`):

- `GET /finops/forecast?days=90&horizon_days=90&group_by=use_case&growth_monthly_pct=` projects
  tokens and cost per use case (or any attribution dimension) for the next horizon, a quarter by
  default: the attributed ledger's daily history over the window, fitted with a linear trend and
  carried forward, compounded monthly by the growth assumption, never below zero, with a band from
  the day-to-day scatter of the history and the flat baseline beside it. Missing days count as zero
  spend. The answer carries the assumptions it was made under.
- `GET /finops/comparison?days=30&changed_at=` folds the completed model calls over the window per
  use case: the model mix with each model's share of cost, and what the same tokens would cost at
  every priced catalogue model (list or negotiated price), cheapest first, with the saving. With a
  change date, the daily cost and calls per model before and after it say what a deployment did.

Both are read on request and store nothing; the figures are tokens and USD by label. Off, the
endpoints are not found.
