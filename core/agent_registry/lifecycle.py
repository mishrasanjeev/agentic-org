# SPDX-License-Identifier: Apache-2.0
"""The agent registry: an agent's card and its governance lifecycle.

**The card** is what a reviewer, an auditor or another team needs to know
about an agent, assembled from the agent's configuration and the registry's
own fields: identity and owner, purpose, risk tier, use case and channels,
the models it may call, the tools and connectors it may use, the permissions
it carries, the schema it is held to, its prompt (as a hash, a template
reference and counts, never the text), its evaluation gate and the verdict,
and where it is in the lifecycle.

**The lifecycle** is the governance state of the agent, apart from its
runtime status (shadow, active, paused):

    draft -> review -> approved -> published -> deprecated -> retired

with ``review -> draft`` (withdrawn or sent back), ``approved -> draft`` (a
change after approval starts again), ``approved -> review`` and
``published -> deprecated``. ``retired`` is final. Moving to ``published``
requires the agent to be active: the registry says an agent is in
production only when it is. Approval may not come from the person who
submitted the agent for review.

Every transition is recorded with who made it and a note. Behind
``AGENTICORG_AGENT_REGISTRY_ENABLED`` (off by default): off, the endpoints
answer 409 and nothing is written. The separate, default-off promotion
gate in ``approval.py`` links registry approval to runtime activation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select

from core.config import settings
from core.evals import gates as eval_gates
from core.evals import runs as eval_runs
from core.governance.guardrails.schema import RISK_TIERS
from core.models.agent import Agent
from core.models.agent_registry import AgentRegistryEntry, AgentRegistryEvent
from core.ownership import Caller, agent_visibility_clause

logger = structlog.get_logger()

STATES: tuple[str, ...] = ("draft", "review", "approved", "published", "deprecated", "retired")
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("review",),
    "review": ("draft", "approved"),
    "approved": ("draft", "review", "published"),
    "published": ("deprecated",),
    "deprecated": ("retired",),
    "retired": (),
}
CHANNELS: tuple[str, ...] = ("api", "chat", "voice", "email", "workflow", "a2a")
# The environment an agent is in, read from its state and not stored.
ENVIRONMENTS: dict[str, str | None] = {
    "draft": "development",
    "review": "development",
    "approved": "staging",
    "published": "production",
    "deprecated": "production",
    "retired": None,
}
MAX_PURPOSE = 2000
MAX_USE_CASE = 120
MAX_NOTE = 500
MAX_LISTED = 500


class RegistryError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(settings.agent_registry_enabled)


# ---------------------------------------------------------------------------
# Card fields
# ---------------------------------------------------------------------------


def _text(value: Any, label: str, limit: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise RegistryError(422, "invalid", f"{label} is text of at most {limit} characters")
    return value.strip()


def parse_card_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """The card fields an administrator writes, checked."""
    unknown = sorted(set(raw) - {"purpose", "risk_tier", "use_case", "channels"})
    if unknown:
        raise RegistryError(422, "invalid", f"unknown card fields: {', '.join(unknown)}")
    fields: dict[str, Any] = {}
    if "purpose" in raw:
        fields["purpose"] = _text(raw.get("purpose"), "purpose", MAX_PURPOSE)
    if "use_case" in raw:
        fields["use_case"] = _text(raw.get("use_case"), "use_case", MAX_USE_CASE)
    if "risk_tier" in raw:
        tier = raw.get("risk_tier")
        if tier is not None and tier not in RISK_TIERS:
            raise RegistryError(422, "invalid", f"risk_tier must be one of {', '.join(RISK_TIERS)}")
        fields["risk_tier"] = tier
    if "channels" in raw:
        channels = raw.get("channels") or []
        if not isinstance(channels, list) or any(channel not in CHANNELS for channel in channels):
            raise RegistryError(422, "invalid", f"channels is a list of {', '.join(CHANNELS)}")
        fields["channels"] = sorted(set(channels), key=CHANNELS.index)
    return fields


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


async def get_entry(
    session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID, *, lock: bool = False
) -> AgentRegistryEntry | None:
    statement = select(AgentRegistryEntry).where(
        AgentRegistryEntry.agent_id == agent_id, AgentRegistryEntry.tenant_id == tenant_id
    )
    if lock:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


def new_entry(tenant_id: uuid.UUID, agent_id: uuid.UUID) -> AgentRegistryEntry:
    return AgentRegistryEntry(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        channels=[],
        state="draft",
        state_changed_at=datetime.now(UTC),
    )


async def ensure_entry(
    session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID, *, lock: bool = False
) -> AgentRegistryEntry:
    """The agent's registry entry, created as ``draft`` on first use."""
    entry = await get_entry(session, tenant_id, agent_id, lock=lock)
    if entry is None:
        entry = new_entry(tenant_id, agent_id)
        session.add(entry)
        await session.flush()
    return entry


async def set_card_fields(
    session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID, fields: dict[str, Any]
) -> AgentRegistryEntry:
    entry = await ensure_entry(session, tenant_id, agent_id, lock=True)
    for key, value in fields.items():
        setattr(entry, key, value)
    entry.updated_at = datetime.now(UTC)
    return entry


async def transition(
    session: Any,
    tenant_id: uuid.UUID,
    agent: Any,
    to_state: str,
    *,
    actor: uuid.UUID | None,
    note: Any = None,
    require_actor: bool = True,
) -> tuple[AgentRegistryEntry, AgentRegistryEvent]:
    """Move the agent to ``to_state`` under the transition table; the entry row is locked.

    A governance transition is made by a signed-in person: without a local
    user id (an API key, a delegated credential, a malformed claim) it is
    refused, so the same-person rule can never be stepped around by an
    unattributed request. ``require_actor=False`` is for transitions the
    runtime makes as a consequence of its own, authorised changes.
    """
    if to_state not in STATES:
        raise RegistryError(422, "invalid", f"state must be one of {', '.join(STATES)}")
    if (require_actor or to_state == "review") and actor is None:
        raise RegistryError(403, "no_actor", "A lifecycle transition needs a signed-in user")
    clean_note = _text(note, "note", MAX_NOTE)
    entry = await ensure_entry(session, tenant_id, agent.id, lock=True)
    if to_state not in TRANSITIONS[entry.state]:
        allowed = ", ".join(TRANSITIONS[entry.state]) or "none"
        raise RegistryError(409, "transition", f"An agent in {entry.state} can move to: {allowed}")
    if to_state == "approved":
        if actor is None or entry.submitted_by == actor:
            raise RegistryError(409, "same_person", "An unidentified user or the submitter cannot approve the agent")
        if entry.submitted_by is None:
            raise RegistryError(409, "submitter_required", "Resubmit the agent for review with an identified human")
    if to_state == "published" and str(getattr(agent, "status", "")) != "active":
        raise RegistryError(409, "not_active", "Only an active agent can be published; promote it first")
    event = AgentRegistryEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent.id,
        from_state=entry.state,
        to_state=to_state,
        actor_user_id=actor,
        note=clean_note,
        created_at=datetime.now(UTC),
    )
    if to_state == "review":
        entry.submitted_by = actor
    entry.state = to_state
    entry.state_changed_at = event.created_at
    entry.state_changed_by = actor
    entry.updated_at = event.created_at
    session.add(event)
    logger.info("agent_registry_transition", agent_id=str(agent.id), from_state=event.from_state, to_state=to_state)
    return entry, event


async def events(session: Any, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> list[AgentRegistryEvent]:
    statement = (
        select(AgentRegistryEvent)
        .where(AgentRegistryEvent.agent_id == agent_id, AgentRegistryEvent.tenant_id == tenant_id)
        .order_by(AgentRegistryEvent.created_at.desc())
        .limit(MAX_LISTED)
    )
    return list((await session.execute(statement)).scalars().all())


async def list_catalogue(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    state: str | None = None,
    risk_tier: str | None = None,
    use_case: str | None = None,
    channel: str | None = None,
    domain: str | None = None,
    q: str | None = None,
    caller: Caller | None = None,
) -> list[tuple[Agent, AgentRegistryEntry | None]]:
    statement = (
        select(Agent, AgentRegistryEntry)
        .outerjoin(
            AgentRegistryEntry,
            (Agent.id == AgentRegistryEntry.agent_id) & (Agent.tenant_id == AgentRegistryEntry.tenant_id),
        )
        .where(Agent.tenant_id == tenant_id, Agent.status != "deleted")
    )
    # Filtering after LIMIT hides valid matches behind invisible or unrelated rows.
    if caller is not None:
        statement = statement.where(agent_visibility_clause(Agent, caller))
    if domain:
        statement = statement.where(Agent.domain == domain)
    if q and q.strip():
        searchable = func.concat(
            func.coalesce(Agent.name, ""),
            " ",
            func.coalesce(Agent.agent_type, ""),
            " ",
            func.coalesce(Agent.description, ""),
            " ",
            func.coalesce(AgentRegistryEntry.purpose, ""),
            " ",
            func.coalesce(AgentRegistryEntry.use_case, ""),
        )
        statement = statement.where(func.lower(searchable).contains(q.strip().lower(), autoescape=True))
    if state:
        statement = statement.where(func.coalesce(AgentRegistryEntry.state, "draft") == state)
    if risk_tier:
        statement = statement.where(AgentRegistryEntry.risk_tier == risk_tier)
    if use_case:
        statement = statement.where(AgentRegistryEntry.use_case == use_case)
    if channel:
        statement = statement.where(AgentRegistryEntry.channels.contains([channel]))
    statement = statement.order_by(
        func.coalesce(AgentRegistryEntry.updated_at, Agent.updated_at).desc(), Agent.id
    ).limit(MAX_LISTED)
    return [(agent, entry) for agent, entry in (await session.execute(statement)).all()]


def matches_search(agent: Any, entry: AgentRegistryEntry | None, q: str | None) -> bool:
    """Whether a catalogue search term appears in the agent's name, type, description, purpose or use case."""
    if not q:
        return True
    needle = q.strip().lower()
    if not needle:
        return True
    haystack = " ".join(
        str(part or "")
        for part in (
            getattr(agent, "name", None),
            getattr(agent, "agent_type", None),
            getattr(agent, "description", None),
            entry.purpose if entry else None,
            entry.use_case if entry else None,
        )
    ).lower()
    return needle in haystack


def templates() -> list[dict[str, Any]]:
    """The agent templates the industry packs offer, in the card's terms; nothing tenant-specific."""
    from core.agents.packs.installer import list_packs

    rows: list[dict[str, Any]] = []
    for pack in list_packs():
        for agent in pack.get("agents") or []:
            rows.append(
                {
                    "pack": pack["name"],
                    "pack_display_name": pack.get("display_name") or pack["name"],
                    "installable": bool(pack.get("installable", True)),
                    "install_disabled_reason": pack.get("install_disabled_reason") or "",
                    "agent_type": agent.get("type") or agent.get("agent_type") or "",
                    "name": agent.get("name") or str(agent.get("type") or "").replace("_", " ").title(),
                    "domain": agent.get("domain"),
                    "description": agent.get("description") or agent.get("system_prompt_suffix") or "",
                    "model": agent.get("llm_model"),
                    "tools": list(agent.get("tools") or []),
                    "hitl_condition": agent.get("hitl_condition"),
                    "confidence_floor": agent.get("confidence_floor"),
                    "compliance": list(pack.get("compliance") or []),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


def entry_dict(entry: AgentRegistryEntry | None) -> dict[str, Any]:
    if entry is None:
        return {
            "purpose": None,
            "risk_tier": None,
            "use_case": None,
            "channels": [],
            "state": "draft",
            "state_changed_at": None,
            "state_changed_by": None,
            "submitted_by": None,
            "environment": ENVIRONMENTS["draft"],
            "next_states": list(TRANSITIONS["draft"]),
        }
    return {
        "purpose": entry.purpose,
        "risk_tier": entry.risk_tier,
        "use_case": entry.use_case,
        "channels": list(entry.channels or []),
        "state": entry.state,
        "state_changed_at": entry.state_changed_at.isoformat() if entry.state_changed_at else None,
        "state_changed_by": str(entry.state_changed_by) if entry.state_changed_by else None,
        "submitted_by": str(entry.submitted_by) if entry.submitted_by else None,
        "environment": ENVIRONMENTS.get(entry.state),
        "next_states": list(TRANSITIONS[entry.state]),
    }


def event_dict(event: AgentRegistryEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "from_state": event.from_state,
        "to_state": event.to_state,
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
        "note": event.note,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _permissions(agent: Any) -> dict[str, Any]:
    config = getattr(agent, "config", None) or {}
    grantex = config.get("grantex") or {}
    return {
        "grantex_scopes": list(grantex.get("grantex_scopes") or []),
        "route_scopes": list(grantex.get("route_scopes") or []),
        "connector_ids": [str(item) for item in (getattr(agent, "connector_ids", None) or [])],
    }


async def card(session: Any, tenant_id: uuid.UUID, agent: Any) -> dict[str, Any]:
    """The agent's card: configuration, registry fields, prompt summary and the evaluation verdict."""
    from core.prompts import output_schema as prompt_output_schema

    entry = await get_entry(session, tenant_id, agent.id)
    text = str(getattr(agent, "system_prompt_text", None) or "")
    config = getattr(agent, "config", None) or {}
    llm_config = getattr(agent, "llm_config", None) or {}
    verdict = await eval_gates.evaluate(session, tenant_id, agent)
    return {
        "id": str(agent.id),
        "name": agent.name,
        "agent_type": agent.agent_type,
        "domain": agent.domain,
        "description": getattr(agent, "description", None),
        "version": getattr(agent, "version", None),
        "status": agent.status,
        "maturity": getattr(agent, "maturity", None),
        "visibility": getattr(agent, "visibility", None),
        "owner_user_id": str(agent.owner_user_id) if getattr(agent, "owner_user_id", None) else None,
        "is_builtin": bool(getattr(agent, "is_builtin", False)),
        "tags": list(getattr(agent, "tags", None) or []),
        "registry": entry_dict(entry),
        "models": {
            "model": getattr(agent, "llm_model", None),
            "provider": getattr(agent, "llm_provider", None),
            "fallback": getattr(agent, "llm_fallback", None),
            "routing": llm_config.get("routing"),
        },
        "tools": list(getattr(agent, "authorized_tools", None) or []),
        "permissions": _permissions(agent),
        "schemas": {
            "output_schema": getattr(agent, "output_schema", None),
            "own_schema": bool(config.get(prompt_output_schema.INLINE_KEY)),
        },
        "prompt": {
            "ref": getattr(agent, "system_prompt_ref", None),
            "hash": eval_runs.prompt_hash(text) if text.strip() else None,
            "variables": len(getattr(agent, "prompt_variables", None) or {}),
            "amendments": len(getattr(agent, "prompt_amendments", None) or []),
        },
        "controls": {
            "confidence_floor": float(agent.confidence_floor)
            if getattr(agent, "confidence_floor", None) is not None
            else None,
            "hitl_condition": getattr(agent, "hitl_condition", None),
            "cost_controls": dict(getattr(agent, "cost_controls", None) or {}),
        },
        "evaluation_gate": {"gate": eval_gates.declared(agent), "verdict": verdict.to_dict()},
        "created_at": agent.created_at.isoformat() if getattr(agent, "created_at", None) else None,
        "updated_at": agent.updated_at.isoformat() if getattr(agent, "updated_at", None) else None,
    }
