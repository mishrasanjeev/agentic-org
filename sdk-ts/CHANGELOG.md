# agenticorg-sdk changelog

## 0.2.0 — 2026-04-19

Canonical `AgentRunResult` contract.

### Added
- `AgentRunResult` interface with every run-contract field:
  `run_id`, `status`, `output`, `confidence`, `reasoning_trace`,
  `tool_calls`, `runtime`, `performance`, `explanation`,
  `hitl_trigger`, `error`. Returned from `client.agents.run()`.
- `toAgentRunResult()` helper that accepts legacy response shapes
  (`task_id`, `result.output`, `result.confidence`) and normalizes
  them to the canonical field set.

### Changed
- `client.agents.run()` now returns `AgentRunResult` (typed interface)
  instead of the looser shape `0.1.0` exposed.

### Compatibility
- Legacy aliases (`task_id`, `result.output`, `result.confidence`)
  are accepted as inputs and deprecated through v5.0 of the server.
- Field names are snake_case to match the over-the-wire JSON — matches
  Python SDK spelling and reduces translation churn for consumers
  using both SDKs.

## 0.1.0 — initial release

First published to npm.
