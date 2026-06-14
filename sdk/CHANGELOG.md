# AgenticOrg Python SDK changelog

## 0.3.0 - 2026-06-14

Commerce, A2A/MCP, workflow, knowledge, and CLI release.

### Added
- `client.agents.run("commerce_sales_agent", action="buyer_discovery_preview", ...)`
  coverage through the A2A task endpoint.
- Agent generation, workflow generation/create/run, workflow template listing,
  knowledge search, A2A discovery, and MCP tool calls in the public SDK surface.
- CLI commands for agents, connectors, SOP parsing, workflows, knowledge, A2A,
  and MCP discovery.

### Fixed
- Release metadata now matches the package contents so PyPI can publish the
  previously-unpublished SDK/CLI capabilities without reusing version `0.2.0`.

## 0.2.0 — 2026-04-19

Canonical `AgentRunResult` contract.

### Added
- `AgentRunResult` dataclass with every run-contract field:
  `run_id`, `status`, `output`, `confidence`, `reasoning_trace`,
  `tool_calls`, `runtime`, `performance`, `explanation`,
  `hitl_trigger`, `error`. Returned from `client.agents.run()`.
- `_to_agent_run_result()` normalizer that accepts legacy response
  shapes (`task_id`, `result.output`, `result.confidence`) and
  converts them — kept for compatibility with the backend's
  pre-PR-A response shape.

### Changed
- `AgenticOrg.agents.run()` now returns `AgentRunResult` (dataclass)
  instead of a raw dict. The dataclass is pickle-safe and iterates
  the same way a dict does for most ergonomic callers.

### Compatibility
- Legacy aliases (`task_id`, `result.output`, `result.confidence`)
  are accepted as inputs and deprecated through v5.0 of the server.
- Consumers of the previous 0.1.0 dict-shaped response should access
  fields via attribute (`result.output`) or `dataclasses.asdict(result)`.

## 0.1.0 — initial release

First published to PyPI.
