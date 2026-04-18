# Agent Run Response Contract

Status: **canonical as of 2026-04-18 (PR-A, Enterprise Readiness P2)**

## Canonical `AgentRunResult`

Every agent-execution endpoint returns the same JSON shape:

```json
{
  "run_id": "string (UUID/opaque id)",
  "agent_id": "string | null (UUID, set when invoked by id)",
  "agent_type": "string | null (agent type, set when invoked by type)",
  "correlation_id": "string | null",
  "status": "completed | failed | hitl_triggered | budget_exceeded",
  "output": { "any": "json" },
  "confidence": 0.0,
  "reasoning_trace": ["string", "..."],
  "tool_calls": [{"tool": "name", "args": {}, "result": null}],
  "runtime": "langgraph | a2a",
  "performance": {
    "total_latency_ms": 0,
    "llm_tokens_used": 0,
    "llm_cost_usd": 0.0
  },
  "explanation": { "any": "json" },
  "hitl_trigger": "string | null",
  "error": "string | null"
}
```

### Field semantics

| Field | Required | Notes |
|---|---|---|
| `run_id` | yes | Opaque identifier for the individual run. Replaces the older `task_id` (from `/agents/{id}/run`) and `id` (from `/a2a/tasks`). Both legacy names remain as deprecated aliases through v4.9.x. |
| `agent_id` | one-of | Populated when the caller invoked an agent by UUID (e.g. `/agents/{uuid}/run`). |
| `agent_type` | one-of | Populated when the caller invoked an agent by type (e.g. `/a2a/tasks` with `agent_type`). Exactly one of `agent_id`/`agent_type` is non-null. |
| `correlation_id` | yes | Stable across the entire run (LLM calls, tool calls, HITL gates) for tracing. |
| `status` | yes | One of the four enum values above. `hitl_triggered` means a human decision is queued; `budget_exceeded` means the run was halted by a budget/scope gate. |
| `output` | yes | Free-form JSON object — agent-specific. Always present; `{}` when no output. |
| `confidence` | yes | `0.0`–`1.0`. Agents that do not compute confidence return `0.0`. |
| `reasoning_trace` | yes | Human-readable step log. Empty list when trace is disabled. |
| `tool_calls` | yes | List of tool invocations (name, args, result). Empty list when no tools called. |
| `runtime` | yes | Execution engine — currently `langgraph` or `a2a`. |
| `performance` | no | Present when the runtime instruments latency/tokens/cost; `null` otherwise. |
| `explanation` | no | Present when the agent produces a structured explanation; `null` otherwise. |
| `hitl_trigger` | no | The human-gate reason string when `status == "hitl_triggered"`; `null` otherwise. |
| `error` | no | Human-readable error string when `status == "failed"`; `null` otherwise. |

## Legacy aliases (deprecated, removed in v5.0)

The following fields are still emitted for backwards compatibility with clients on v4.8.x and earlier, but should not be used in new code:

| Legacy | Canonical | Source endpoint |
|---|---|---|
| `task_id` | `run_id` | `/agents/{id}/run` |
| `id` | `run_id` | `/a2a/tasks` |
| `result: { output, confidence }` | `output` + `confidence` at top level | `/a2a/tasks` |

## Endpoints

Both endpoints MUST produce the canonical shape:

- `POST /api/v1/agents/{id}/run` — invoke an agent by UUID.
- `POST /api/v1/a2a/tasks` — invoke an agent by type (A2A/MCP path).

Tests: `tests/regression/test_agent_run_contract.py` asserts every field is present (with correct null-ness) on both endpoints.

## SDKs

- **Python** (`sdk/agenticorg/client.py`): `run()` returns `AgentRunResult` dataclass. Raw response is normalized in `_to_agent_run_result()`; both endpoint shapes (canonical + legacy-shaped) produce identical `AgentRunResult`.
- **TypeScript** (`sdk-ts/src/index.ts`): `AgentRunResult` interface matches this doc. Runtime helper `toAgentRunResult()` normalizes.
- **In-product snippet** (`ui/src/pages/Integrations.tsx`): the example on the Integrations page mirrors the SDK signature. Drift is caught by `ui/e2e/sdk-examples.spec.ts`.
