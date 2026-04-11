# ADR 0001: Use LangGraph for agent orchestration

- **Status**: Accepted
- **Date**: 2025-10-12
- **Deciders**: Sanjeev (tech lead), Engineering team

## Context

We needed an orchestration layer to run LLM agents with:
1. Tool calls (HTTP APIs, databases, third-party systems)
2. Human-in-the-loop checkpoints (approvals)
3. Deterministic state transitions we can resume after a crash
4. A graph structure we can reason about in tests

Candidates considered:
- **LangChain agents** — simpler API, no state machine. Retry-after-crash
  story was weak and lacked HITL primitives.
- **Temporal** — excellent durability, but designed for long-running
  workflows rather than LLM thinking-loops. Heavy operational footprint
  (separate Temporal cluster).
- **LangGraph** — graph-based state machine, first-class HITL support
  via `interrupt_before`, integrates cleanly with LangChain tool
  definitions.
- **Prefect** — data-pipeline focus, not agent-focus.
- **Home-grown** — expensive and risky.

## Decision

Adopt **LangGraph** as the primary orchestration layer. Wrap it in
`core/langgraph/runner.py` so callers don't take a direct dependency
and we can swap it later if needed.

## Consequences

- **Good**: state persists in checkpoints, HITL interruption works out
  of the box, tool calls integrate with our gateway.
- **Good**: the graph structure is testable — we can simulate edges.
- **Bad**: LangGraph is young and breaking changes happen. We pin the
  version and upgrade deliberately.
- **Bad**: The default checkpointer is in-memory; we had to build our
  own Postgres-backed checkpointer for durability (see ADR 0003).

## Alternatives if LangGraph dies

The wrapper in `core/langgraph/runner.py` is the only module that
imports LangGraph symbols. If needed we can re-implement it against
Temporal or a hand-rolled state machine.
