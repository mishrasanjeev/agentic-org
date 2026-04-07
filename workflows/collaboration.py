"""Parallel multi-agent collaboration step handler.

Implements the ``"collaboration"`` step type for workflows, allowing
multiple agents to run in parallel with shared context and configurable
aggregation strategies (merge, vote, first_complete).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()

# Default timeout for collaboration steps (seconds)
DEFAULT_TIMEOUT_SECONDS = 300  # 5 minutes


async def execute_collaboration_step(step: dict, state: dict) -> dict[str, Any]:
    """Run multiple agents in parallel and aggregate their results.

    Step config:
        agents: list of agent identifiers to run in parallel
        shared_context: bool — if True, a shared dict is passed to all agents
        aggregation: "merge" | "vote" | "first_complete"
        timeout_seconds: per-step timeout (default 300)

    Aggregation strategies:
        merge: combine all agent outputs into a single dict keyed by agent name
        vote: count output["decision"] values, majority wins
        first_complete: return the first agent's result, cancel the rest

    Error handling:
        An error in one agent does not block others (graceful degradation).
        Failed agents are recorded in the result with status "failed".
    """
    config = step.get("config", step)
    agent_names: list[str] = config.get("agents", [])
    shared_context_enabled: bool = config.get("shared_context", True)
    aggregation: str = config.get("aggregation", "merge")
    timeout_seconds: int = config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)

    if not agent_names:
        return {
            "step_id": step.get("id", ""),
            "type": "collaboration",
            "status": "completed",
            "output": {},
            "agents_run": 0,
        }

    # Shared context dict — agents can read/write during execution
    shared_ctx: dict[str, Any] | None = {} if shared_context_enabled else None

    # Build coroutines for each agent
    agent_tasks = {
        agent_name: _run_agent(agent_name, step, state, shared_ctx)
        for agent_name in agent_names
    }

    # Execute based on aggregation strategy
    if aggregation == "first_complete":
        result = await _first_complete(agent_tasks, timeout_seconds)
    else:
        results = await _run_all_agents(agent_tasks, timeout_seconds)
        if aggregation == "vote":
            result = _aggregate_vote(results)
        else:
            # Default: merge
            result = _aggregate_merge(results)

    result["step_id"] = step.get("id", "")
    result["type"] = "collaboration"
    result["shared_context"] = shared_ctx
    return result


async def _run_agent(
    agent_name: str,
    step: dict,
    state: dict,
    shared_context: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single agent within the collaboration.

    Uses the workflow step_types agent executor if available,
    otherwise returns a stub result.
    """
    try:
        from workflows.step_types import execute_step

        agent_step = {
            "id": f"{step.get('id', 'collab')}_{agent_name}",
            "type": "agent",
            "agent": agent_name,
            "agent_type": agent_name,
            "inputs": {
                **(step.get("inputs", {})),
                "shared_context": shared_context,
            },
        }
        result = await execute_step(agent_step, state)

        # Allow agent to contribute to shared context
        if isinstance(result.get("output"), dict):
            agent_contribution = result["output"].get("shared_context_update")
            if isinstance(agent_contribution, dict):
                shared_context.update(agent_contribution)

        return {
            "agent": agent_name,
            "status": result.get("status", "completed"),
            "output": result.get("output", {}),
        }
    except Exception as exc:
        logger.warning("collaboration_agent_failed", agent=agent_name, error=str(exc))
        return {
            "agent": agent_name,
            "status": "failed",
            "output": {},
            "error": str(exc),
        }


async def _run_all_agents(
    agent_tasks: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, dict[str, Any]]:
    """Run all agents in parallel, collecting results. Failures don't block others."""
    async def _wrapped(name: str, coro: Any) -> tuple[str, dict[str, Any]]:
        return name, await coro

    tasks = [
        asyncio.create_task(_wrapped(name, coro))
        for name, coro in agent_tasks.items()
    ]

    results: dict[str, dict[str, Any]] = {}
    try:
        done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        for task in done:
            try:
                name, result = task.result()
                results[name] = result
            except Exception as exc:
                logger.warning("collaboration_task_result_error", error=str(exc))

        # Cancel timed-out tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: S110
                pass  # Expected during cancellation

        # Record timed-out agents
        completed_names = set(results.keys())
        for name in agent_tasks:
            if name not in completed_names:
                results[name] = {
                    "agent": name,
                    "status": "timed_out",
                    "output": {},
                    "error": f"Agent {name} timed out after {timeout_seconds}s",
                }
    except Exception as exc:
        logger.error("collaboration_run_all_error", error=str(exc))

    return results


async def _first_complete(
    agent_tasks: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Return the result of the first agent to complete, cancel the rest."""
    async def _wrapped(name: str, coro: Any) -> tuple[str, dict[str, Any]]:
        return name, await coro

    tasks = [
        asyncio.create_task(_wrapped(name, coro))
        for name, coro in agent_tasks.items()
    ]

    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: S110
                pass  # Expected during cancellation

        if done:
            first_task = next(iter(done))
            name, result = first_task.result()
            return {
                "status": "completed",
                "aggregation": "first_complete",
                "winner": name,
                "output": result.get("output", {}),
                "agents_run": len(agent_tasks),
                "agents_completed": 1,
            }

        return {
            "status": "timed_out",
            "aggregation": "first_complete",
            "output": {},
            "agents_run": len(agent_tasks),
            "agents_completed": 0,
            "error": f"All agents timed out after {timeout_seconds}s",
        }
    except Exception as exc:
        return {
            "status": "failed",
            "aggregation": "first_complete",
            "output": {},
            "error": str(exc),
        }


def _aggregate_merge(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Merge all agent outputs into a single dict keyed by agent name."""
    merged_output: dict[str, Any] = {}
    agents_succeeded = 0
    agents_failed = 0

    for name, result in results.items():
        merged_output[name] = result.get("output", {})
        if result.get("status") == "completed":
            agents_succeeded += 1
        else:
            agents_failed += 1

    return {
        "status": "completed",
        "aggregation": "merge",
        "output": merged_output,
        "agents_run": len(results),
        "agents_succeeded": agents_succeeded,
        "agents_failed": agents_failed,
    }


def _aggregate_vote(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Count output['decision'] values across agents; majority wins."""
    votes: dict[str, int] = {}
    voters: dict[str, list[str]] = {}

    for name, result in results.items():
        output = result.get("output", {})
        decision = output.get("decision") if isinstance(output, dict) else None
        if decision is not None:
            decision_str = str(decision)
            votes[decision_str] = votes.get(decision_str, 0) + 1
            voters.setdefault(decision_str, []).append(name)

    if not votes:
        return {
            "status": "completed",
            "aggregation": "vote",
            "output": {"decision": None, "reason": "No agents provided a decision"},
            "votes": {},
            "agents_run": len(results),
        }

    # Find majority
    winning_decision = max(votes, key=lambda k: votes[k])
    return {
        "status": "completed",
        "aggregation": "vote",
        "output": {
            "decision": winning_decision,
            "vote_count": votes[winning_decision],
            "total_votes": sum(votes.values()),
            "voters": voters.get(winning_decision, []),
        },
        "votes": votes,
        "agents_run": len(results),
    }
