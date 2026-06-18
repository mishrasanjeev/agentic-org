# agenticorg-sdk changelog

## 0.3.0 - 2026-06-14

Commerce, A2A/MCP, workflow, and knowledge release.

### Added
- `client.agents.run("commerce_sales_agent", { action: "buyer_discovery_preview", ... })`
  coverage through the A2A task endpoint.
- Agent generation, workflow generation/create/run, workflow template listing,
  knowledge search, A2A discovery, and MCP tool calls in the public SDK surface.

### Fixed
- Source examples now import the published npm package name `agenticorg-sdk`
  instead of the unpublished scoped name `@agenticorg/sdk`.
- Release metadata now matches the package contents so npm can publish the
  previously-unpublished SDK capabilities without reusing version `0.2.0`.

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
