# SPDX-License-Identifier: Apache-2.0
"""Which agent statuses may run.

The console's pause sets an agent's status to ``paused``. Routing already skips
paused agents; with ``AGENTICORG_PAUSED_AGENTS_REFUSED`` on, a direct run, a chat
that names the agent and a workflow agent step refuse it too (FINDINGS A-110),
so a pause stops execution rather than only routing. ``retired`` and ``deleted``
agents never run.
"""

from __future__ import annotations

from core.config import settings

NEVER_RUNS: frozenset[str] = frozenset({"deleted", "retired"})


def paused_agents_refused() -> bool:
    return bool(settings.paused_agents_refused)


def inactive_agent_statuses() -> frozenset[str]:
    """Statuses an agent must not run in, for the current setting."""
    if paused_agents_refused():
        return NEVER_RUNS | {"paused"}
    return NEVER_RUNS


def refusal_for(status: str | None) -> str | None:
    """The message a run of an agent in ``status`` is refused with, or None."""
    if status == "retired":
        return "Cannot run a retired agent"
    if status == "paused" and paused_agents_refused():
        return "Agent is paused"
    return None
