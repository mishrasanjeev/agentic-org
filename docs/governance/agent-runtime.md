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

## Long-term memory with retention

A run already has a short-term memory: its thread, kept by the checkpointer for the span of the
conversation. With `AGENTICORG_RUNTIME_MEMORY_ENABLED` on, there is a long-term store beside it
(`core/memory/long_term.py`, table `agent_memories`): what an agent, or an administrator, chose to
remember about a **subject**, a customer, user, account or case reference the caller names as a
bounded identifier. Every entry has a kind, a bounded content, an importance and an expiry:

| Kind | Default retention |
|---|---|
| `fact`, `preference` | 365 days |
| `summary` | 90 days |
| `event` | 30 days |

A writer may ask for a shorter or longer retention, up to 730 days; nothing is recalled past its
expiry; the nightly task (`core/tasks/memory_tasks.py`) and `POST /memory/prune` remove what
expired. A run whose task input names a subject (`context.subject`) recalls what is remembered
about it into its system prompt, marked as context to verify before acting; what the agent asks to
keep in its output under `remember` (up to five entries) is stored after the run, scoped to the
agent, with the run id as its source. `GET /memory?subject=` recalls (the agent's own entries and
the shared ones, most important and recent first, optionally matching a query), `POST /memory`
remembers (the same content about the same subject refreshes its expiry), and
`DELETE /memory?subject=` erases every entry about a subject, for one agent or for all, answering
with the count, so a request to be forgotten is one audited call. `GET /memory/policy` states the
kinds, retentions and bounds. Off, no run reads or writes memory and the endpoints are not found.

Recall needs `audit:read` and writes need `approvals:write` (the `memory` scope family); an
`agent_id` must name an agent of the caller's tenant that the caller may see, or the request is not
found. The table is under a forced tenant row-level policy. Recalled entries are model input: they
are pseudonymised or redacted with the task before they join the prompt. Memory is a sidecar: a
database error while recalling leaves the run without memory and one while storing leaves the run's
outcome as it was, both logged. The same content about the same subject, for an agent or shared, is
one entry under a unique index (`content_hash`), written by a single atomic upsert.

## Tool registration and the execution envelope

With `AGENTICORG_TOOL_REGISTRY_ENABLED` on, a tenant administrator registers tools
(`core/tool_gateway/registry.py`, `/tools/registry`): a name (`connector:tool`, or a plain tool
name), a JSON Schema for the tool's inputs, optionally one for its outputs, a risk class, and an
execution envelope: the longest a call may take (1 to 300 seconds), the most output it may return,
and whether its output is treated as untrusted content. Schemas are checked at registration (a
valid draft 2020-12 schema, bounded, without `$ref`), so a bad schema never reaches a call.

The gateway checks every call to a registered tool against its input schema **before the call
leaves the gateway**: inputs that fail are refused (`E1012`, `tool_input_invalid`, the errors
named by path), audited as `input_rejected`, and never dispatched. A call that runs is held to
its envelope: a timeout (`tool_timeout`), an output cap (`tool_output_too_large`), the output
checked against its schema when one is declared (`tool_output_invalid`), and the result marked
`_untrusted` so the model's guardrails treat it as retrieved content. With
`AGENTICORG_TOOL_REGISTRY_REQUIRE_REGISTRATION` on, a call to a tool no registration covers is
refused too (`tool_unregistered`). `POST /tools/registry/check?name=` is a dry run of the
gateway's check for a tool and a set of inputs.

What the envelope is not: in-process connector code is not process-isolated by it. The extraction
worker (`core/extraction/sandbox.py`) remains the out-of-process sandbox for untrusted content; the
envelope bounds and screens a connector call and marks its output untrusted. Off, no call is
checked or enveloped and the endpoints are not found.

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
