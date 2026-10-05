# SPDX-License-Identifier: Apache-2.0
"""Maker-checker for agent prompts: the person who changed a prompt does not put it into production.

An agent's prompt cannot be edited while the agent is active; it is edited on
a shadow or paused agent, or set when an agent is created or cloned, and
reaches production when the agent becomes active. So the second person for an
agent's prompt belongs at activation.

While maker-checker is on for the tenant (``core.prompts.change_requests``):

* An agent whose prompt has changed since it was last active is activated
  (promoted, or resumed to active) only by a signed-in user other than the
  one who last changed the prompt. Creating or cloning an agent records its
  creator as the prompt's first author in the prompt history, so a new
  agent's author is known before anyone has edited it.
* A prompt change that cannot be attributed to a user is not activated: there
  is no one to be different from.
* An agent is not created or cloned straight into ``active``: the person who
  writes a prompt would be activating it in the same step.
* An agent whose prompt has not changed since it was last active (a pause and
  a resume) is activated as before; nothing new reaches production.

Off, activation is as it was.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from core.prompts import change_requests

logger = structlog.get_logger()


class ActivationError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


async def _last_prompt_edit(session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.prompt_template import PromptEditHistory

    return await session.scalar(
        select(PromptEditHistory)
        .where(PromptEditHistory.tenant_id == tenant_id, PromptEditHistory.agent_id == agent_id)
        .order_by(PromptEditHistory.created_at.desc())
        .limit(1)
    )


async def _last_activation(session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.agent import AgentLifecycleEvent

    return await session.scalar(
        select(AgentLifecycleEvent)
        .where(
            AgentLifecycleEvent.tenant_id == tenant_id,
            AgentLifecycleEvent.agent_id == agent_id,
            AgentLifecycleEvent.to_status == "active",
        )
        .order_by(AgentLifecycleEvent.created_at.desc())
        .limit(1)
    )


def record_initial_prompt(session: Any, tenant_id: uuid.UUID, agent: Any, author: uuid.UUID | None) -> None:
    """Write the new agent's prompt into its history with who set it (nothing for a caller with no user id)."""
    if author is None:
        return
    from core.models.prompt_template import PromptEditHistory

    session.add(
        PromptEditHistory(
            tenant_id=tenant_id,
            agent_id=agent.id,
            prompt_before=None,
            prompt_after=getattr(agent, "system_prompt_text", None) or "",
            change_reason="Initial prompt",
            edited_by=author,
        )
    )


async def maker_checker_on(tenant_id: uuid.UUID) -> bool:
    """Whether activation needs a second person; an unreadable flag refuses rather than allows."""
    try:
        return await change_requests.enabled(tenant_id)
    # enterprise-gate: broad-except-ok reason=an-unreadable-maker-checker-flag-fails-closed-and-is-logged
    except Exception as exc:
        logger.error("agent_activation_flag_unreadable", error_type=type(exc).__name__)
        raise ActivationError(503, "The maker-checker setting could not be read; the agent was not activated") from exc


async def check_new_agent_status(tenant_id: uuid.UUID, initial_status: str) -> None:
    """A new or cloned agent does not start active while maker-checker is on."""
    if initial_status != "active":
        return
    if await maker_checker_on(tenant_id):
        raise ActivationError(
            409,
            "Under maker-checker an agent is created in shadow; a second person promotes it to active",
        )


async def check_activation(
    session: Any, tenant_id: uuid.UUID, agent: Any, activator: uuid.UUID | None
) -> uuid.UUID | None:
    """Refuse an activation that would put a prompt into production on its author's word alone.

    Returns the activator to record on the lifecycle event (None while
    maker-checker is off and the caller has no user id).
    """
    if not await maker_checker_on(tenant_id):
        return activator
    edit = await _last_prompt_edit(session, tenant_id, agent.id)
    activation = await _last_activation(session, tenant_id, agent.id)
    edited_at = getattr(edit, "created_at", None)
    activated_at = getattr(activation, "created_at", None)
    if activation is not None and (edit is None or (edited_at and activated_at and edited_at <= activated_at)):
        # Nothing has changed since the agent was last active.
        return activator
    if activator is None:
        raise ActivationError(
            403, "Under maker-checker an agent with a changed prompt is activated by a signed-in user, not an API key"
        )
    author = getattr(edit, "edited_by", None) if edit is not None else None
    if author is None:
        raise ActivationError(
            409,
            "The agent's prompt change has no recorded author, so a second person cannot be shown; "
            "have a signed-in user save the prompt, then have another activate the agent",
        )
    if author == activator:
        raise ActivationError(
            403, "Under maker-checker the person who last changed an agent's prompt cannot activate the agent"
        )
    logger.info("agent_activation_second_person", agent_id=str(agent.id))
    return activator
