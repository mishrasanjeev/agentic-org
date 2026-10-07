# SPDX-License-Identifier: Apache-2.0
"""The approval workflow: promotion to production follows the registry, and the registry follows the runtime.

While ``AGENTICORG_AGENT_REGISTRY_GATES_PROMOTION`` is on (and the registry is
on), an agent is promoted or resumed to ``active`` only when its registry
entry is ``approved`` or ``published``: the lifecycle's two rules (the
submitter cannot approve; approval is a second person's decision) then stand
between any prompt and production, beside the shadow evidence, the
maker-checker check on the prompt and the evaluation gate. The check runs
after those, so the refusal names the first thing that is missing.

The registry follows the runtime so the two never disagree about production:

* promotion to ``active`` moves an ``approved`` entry to ``published``;
* retiring the agent moves a ``published`` or ``deprecated`` entry to
  ``retired`` (through ``deprecated`` when it was published).

Each of these is a recorded transition with the actor and a note. Off, the
registry neither gates nor follows; the lifecycle is what administrators
make of it.

**Environments** are read from the state, not stored: ``draft`` and
``review`` are development, ``approved`` is staging, ``published`` and
``deprecated`` are production, ``retired`` is none.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from core.agent_registry import lifecycle
from core.config import settings

logger = structlog.get_logger()

PROMOTABLE_STATES: tuple[str, ...] = ("approved", "published")
ENVIRONMENTS = lifecycle.ENVIRONMENTS
TRIGGER = "agent_registry"


class ApprovalError(Exception):
    def __init__(self, code: str, message: str, state: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.state = state


def gates_promotion() -> bool:
    return bool(settings.agent_registry_gates_promotion) and lifecycle.enabled()


def environment_of(state: str) -> str | None:
    return ENVIRONMENTS.get(state)


def check_new_agent_status(status: str) -> None:
    """New agents and clones have no independent registry approval to inherit."""
    if gates_promotion() and status == "active":
        raise ApprovalError(
            "not_approved",
            "Create the agent in shadow, submit it for independent registry review, then promote it.",
            "draft",
        )


async def check_promotion(session: Any, tenant_id: uuid.UUID, agent: Any) -> str | None:
    """The registry state the promotion is allowed under, or ``None`` when the registry does not gate.

    Raises ``ApprovalError`` when the gate is on and the agent is not approved.
    """
    if not gates_promotion():
        return None
    entry = await lifecycle.get_entry(session, tenant_id, agent.id)
    state = entry.state if entry is not None else "draft"
    if state not in PROMOTABLE_STATES:
        logger.warning("agent_registry_promotion_refused", agent_id=str(agent.id), state=state)
        raise ApprovalError(
            "not_approved",
            f"The agent has not been approved in the registry: it is in {state}. "
            "Submit it for review and have another person approve it.",
            state,
        )
    return state


async def follow_promotion(session: Any, tenant_id: uuid.UUID, agent: Any, *, actor: uuid.UUID | None) -> None:
    """After a promotion to active: an approved entry is now published."""
    if not gates_promotion():
        return
    entry = await lifecycle.get_entry(session, tenant_id, agent.id)
    if entry is not None and entry.state == "approved":
        await lifecycle.transition(session, tenant_id, agent, "published", actor=actor, note="Promoted to active")


async def follow_retirement(session: Any, tenant_id: uuid.UUID, agent: Any, *, actor: uuid.UUID | None) -> None:
    """After the agent is retired: a published or deprecated entry is retired, through deprecated when needed."""
    if not gates_promotion():
        return
    entry = await lifecycle.get_entry(session, tenant_id, agent.id)
    if entry is None or entry.state not in ("published", "deprecated"):
        return
    if entry.state == "published":
        await lifecycle.transition(session, tenant_id, agent, "deprecated", actor=actor, note="Agent retired")
    await lifecycle.transition(session, tenant_id, agent, "retired", actor=actor, note="Agent retired")
