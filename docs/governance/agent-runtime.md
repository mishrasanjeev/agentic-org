# Agent runtime: limits, memory, tools and debugging

## Execution limits and loop detection

Every run is held to the platform's maxima: `AGENTICORG_MAX_AGENT_STEPS` graph steps (200 by
default) and `AGENTICORG_MAX_AGENT_DURATION_SEC` seconds (30 minutes by default). With
`AGENTICORG_RUNTIME_LIMITS_ENABLED` on, a run that reaches the step ceiling or the duration is
stopped with `status` `failed`, an `error` that begins with `stopped:` (or `timeout:` for the
duration) and a `limit` block naming the reason (`step_limit`, `duration_limit`) and the detail;
the run response (`POST /agents/{id}/run`) and its audit entry carry the block and the
`agenticorg_agent_runs_stopped_total` series counts it by reason. Off, such a run fails as it
always has, without the block or the counter.

With `AGENTICORG_RUNTIME_LIMITS_ENABLED` on, an agent's own limits apply as well
(`core/langgraph/limits.py`, `PUT /agents/{id}/limits`), each bounded by the platform's maxima:

| Limit | Meaning | Bounds |
|---|---|---|
| `max_steps` | the most model answers a run may take | 1 to the platform's step ceiling |
| `max_duration_seconds` | the longest a run may take; the runner's timeout | 1 to the platform's duration |
| `max_tool_calls` | the most tool calls a run may make: a round that would take it over the limit does not run | 1 to 500 |
| `max_repeats` | identical tool calls (same tool, same arguments) in a row that count as a loop | 2 to 20, 3 by default |
| `loop_window` | the longest pattern of tool calls whose repetition counts as a loop | 2 to 10, 4 by default |

The graph checks the step limit before every model call (after a round of tools and on an
output-schema correction alike), and all the limits before every round of tool execution, reading
the tool calls the model has asked for so far as a sequence of signatures (the tool name and a hash of its arguments,
never the arguments): the same signature `max_repeats` times in a row, or a pattern of up to
`loop_window` calls repeated twice back to back, is a loop (`loop_detected`); a run over
`max_steps` or `max_tool_calls` stops too. The reason and the detail travel in the run's `error`
and `limit` block to the run response, the audit entry and the counter. The limits apply on every
path that runs a stored agent: `POST /agents/{id}/run` and chat (`POST /chat/query`). `GET /agents/{id}/limits` shows the agent's
declared limits, the limits a run is held to, the platform's maxima and whether they are enforced.

Off, an agent's limits are kept and shown but the platform maxima alone apply, as before.
