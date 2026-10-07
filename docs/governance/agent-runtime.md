# Agent runtime: limits, memory, tools and debugging

## Visual workflow builder

A workflow definition is a list of steps: each has an id, a type (`agent`, `human_in_loop`,
`condition`, `wait`, `notify`, `collaboration` and the others the engine runs), what it depends
on (`depends_on`), and, by type, an agent and action, a condition with its `true_path` and
`false_path` (rule-based conditions draw, but the engine does not branch on `rules` yet, so
validation refuses them), a human checkpoint with who decides and its
`decision_options`, and a failure directive (`on_failure`: `halt`, `continue`, `retry(N)`,
`retry(N) then continue`, or `fallback(step)`). The console's **Build visually** tab
(`ui/src/components/WorkflowBuilder.tsx`) draws the definition as a graph, one node per step and
one edge per dependency, condition path and fallback, each coloured and labelled by kind; a step
is added from the palette, connected by dragging to the step that follows it, edited in the side
panel, and removed with every reference cleared. **Validate** asks `POST /workflows/validate`,
which names every problem in plain words (`core/workflows/graph.py`): an entry that is not a
step with a text id (the runtime parser needs both), a dependency, path or fallback that points
nowhere, a condition without an expression and paths, a condition with `rules`, a human
checkpoint without decision options or without who decides, an agent step without its agent type, a failure directive outside
the grammar, a cycle (a fallback counts as following its source). **Use these steps** carries the drawn steps into the form that names,
schedules and creates the workflow; `GET /workflows/{id}/graph` draws a stored one, and the
workflow page shows it. The tab and the workflow page graph appear only while
`GET /workflows/builder` reports the flag below on; if the flag cannot be read they stay hidden.

`fallback(step)` is new in the engine: a step that fails with it does not fail the run; the named
step runs instead of what followed, and it is skipped (`fallback_not_needed`) when the step it
falls back from succeeded. The engine orders the fallback after its source and gates it on that
source whether or not the fallback also lists it in `depends_on`. With `AGENTICORG_WORKFLOW_BUILDER_V2_ENABLED` on, `POST /workflows`
refuses a definition with problems (`422`, `workflow_definition`, the problems listed); off,
creation accepts what it accepted before, and the validate and graph endpoints answer regardless.

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
