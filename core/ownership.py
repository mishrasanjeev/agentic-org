"""Per-user ownership rules for agents, connectors, and approvals.

Bug sheet 2026-09-14 rows 17/18/19/22/29/30/52. This module is the single
source of truth for "who may see / change / use what". Route handlers build
a :class:`Caller` from the authenticated request and ask these functions;
they must never re-derive the rules inline.

Rules (all fail closed):

Agents
  * ``visibility='tenant'`` (shared): admins see all; other human callers see
    it when its domain is in their domain list (unchanged behaviour).
  * ``visibility='personal'``: visible to its owner and to tenant admins only.
    An ownerless personal agent (owner deleted) is admin-only.
  * Mutating a tenant agent stays admin-only. The owner may mutate their own
    personal agent. Only an admin may change visibility.
  * Personal agents may be created by roles in :data:`PERSONAL_AGENT_ROLES`,
    inside their own domains; developers may pick any domain.

Connectors
  * ``owner_user_id IS NULL`` is a tenant-shared connector: visible to every
    human caller with connector read access, mutable by admins only.
  * An owned connector is visible to and mutable by its owner and admins.
  * Names stay unique tenant-wide across all owners (row 29): runtime
    credentials are keyed by connector name.
  * A personal connector may only be linked to a personal agent owned by
    the same user, so another user's run can never use its credentials.

Approvals
  * Items for a personal agent: owner and admins only.
  * Items for a tenant agent: today's domain + role hierarchy rules.

Machine credentials (API keys, Grantex agent tokens) have no user id and no
role; they are tenant-scoped and scope-bounded. They see tenant resources
only, never personal ones.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import ColumnElement, and_, false, or_, true

from core.rbac import has_admin_scope

AGENT_VISIBILITY_TENANT = "tenant"
AGENT_VISIBILITY_PERSONAL = "personal"
AGENT_VISIBILITIES = (AGENT_VISIBILITY_TENANT, AGENT_VISIBILITY_PERSONAL)

CONNECTOR_VISIBILITY_SHARED = "shared"
CONNECTOR_VISIBILITY_PERSONAL = "personal"

# Roles that may create and manage their own personal agents and connectors.
PERSONAL_AGENT_ROLES = frozenset({"cfo", "chro", "cmo", "coo", "domain_lead", "developer"})
# Roles whose personal agents are not bound to the caller's domain list.
ANY_DOMAIN_PERSONAL_ROLES = frozenset({"developer"})
# Roles that may decide approvals only for their own personal agents.
OWN_APPROVALS_ONLY_ROLES = frozenset({"developer"})


@dataclass(frozen=True)
class Caller:
    """Authenticated principal, reduced to what ownership rules need."""

    user_id: uuid.UUID | None
    role: str
    domains: list[str] | None
    is_admin: bool
    is_machine: bool

    @property
    def is_human(self) -> bool:
        return not self.is_machine and self.user_id is not None


def caller_from_request(request: Request) -> Caller:
    """Build a :class:`Caller` from ``request.state`` set by the auth middleware."""
    from api.deps import get_user_domains

    state = request.state
    claims: dict[str, Any] = getattr(state, "claims", None) or {}
    scopes = getattr(state, "scopes", None) or claims.get("grantex:scopes") or []
    auth_mode = getattr(state, "auth_mode", None)
    subject = str(claims.get("sub") or "")
    is_machine = (
        auth_mode in {"api_key", "grantex"}
        or subject.startswith("apikey:")
        or bool(claims.get("grantex:grant_id"))
    )
    user_id: uuid.UUID | None = None
    raw_user_id = claims.get("agenticorg:user_id")
    if raw_user_id and not is_machine:
        try:
            user_id = uuid.UUID(str(raw_user_id))
        except (TypeError, ValueError):
            user_id = None
    return Caller(
        user_id=user_id,
        role=str(claims.get("role") or "").lower(),
        domains=get_user_domains(request),
        is_admin=has_admin_scope(scopes),
        is_machine=is_machine,
    )


def machine_caller() -> Caller:
    """Caller for background/system code paths that act for the tenant."""
    return Caller(user_id=None, role="", domains=None, is_admin=False, is_machine=True)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def _agent_domain_allowed(domain: str | None, caller: Caller) -> bool:
    if not isinstance(caller.domains, list):
        return True
    return not domain or domain in caller.domains


def is_personal_agent(agent: Any) -> bool:
    return str(getattr(agent, "visibility", "") or AGENT_VISIBILITY_TENANT) == AGENT_VISIBILITY_PERSONAL


def is_agent_owner(agent: Any, caller: Caller) -> bool:
    owner = getattr(agent, "owner_user_id", None)
    return caller.user_id is not None and owner is not None and str(owner) == str(caller.user_id)


def can_view_agent(agent: Any, caller: Caller) -> bool:
    if agent is None:
        return False
    if is_personal_agent(agent):
        return caller.is_admin or is_agent_owner(agent, caller)
    if caller.is_admin or caller.is_machine:
        return True
    return _agent_domain_allowed(getattr(agent, "domain", None), caller)


def can_mutate_agent(agent: Any, caller: Caller) -> bool:
    if agent is None:
        return False
    if caller.is_admin:
        return True
    return is_personal_agent(agent) and is_agent_owner(agent, caller)


def require_agent_visible(agent: Any, caller: Caller) -> None:
    """404 (never 403) so a caller cannot probe for agents they may not see."""
    if not can_view_agent(agent, caller):
        raise HTTPException(404, "Agent not found")


def require_agent_mutable(agent: Any, caller: Caller) -> None:
    require_agent_visible(agent, caller)
    if not can_mutate_agent(agent, caller):
        raise HTTPException(403, "Only a tenant admin or the agent's owner can change this agent")


def agent_visibility_clause(agent_model: Any, caller: Caller) -> ColumnElement[bool]:
    """SQL filter equivalent to :func:`can_view_agent` for list queries."""
    tenant_rows = agent_model.visibility == AGENT_VISIBILITY_TENANT
    if caller.is_admin:
        return true()
    if isinstance(caller.domains, list) and not caller.is_machine:
        tenant_rows = and_(tenant_rows, agent_model.domain.in_(caller.domains))
    if caller.user_id is None:
        return tenant_rows
    own_rows = and_(
        agent_model.visibility == AGENT_VISIBILITY_PERSONAL,
        agent_model.owner_user_id == caller.user_id,
    )
    return or_(tenant_rows, own_rows)


def shared_agents_only_clause(agent_model: Any) -> ColumnElement[bool]:
    """Filter for automatic agent selection (routing, A2A/MCP, sales, packs).

    Automatic selection must never land on anyone's personal agent.
    """
    return agent_model.visibility == AGENT_VISIBILITY_TENANT


def resolve_new_agent_ownership(
    caller: Caller,
    requested_visibility: str | None,
    domain: str | None,
) -> tuple[str, uuid.UUID | None]:
    """Return ``(visibility, owner_user_id)`` for a newly created agent.

    Raises 403 when the caller may not create an agent of that shape.
    """
    requested = (requested_visibility or "").strip().lower() or None
    if requested is not None and requested not in AGENT_VISIBILITIES:
        raise HTTPException(422, f"visibility must be one of {', '.join(AGENT_VISIBILITIES)}")
    if caller.is_admin:
        if requested == AGENT_VISIBILITY_PERSONAL:
            if caller.user_id is None:
                raise HTTPException(403, "A personal agent needs an authenticated user owner")
            return AGENT_VISIBILITY_PERSONAL, caller.user_id
        return AGENT_VISIBILITY_TENANT, None
    if caller.is_machine or caller.user_id is None:
        raise HTTPException(403, "Only a tenant admin can create shared agents")
    if caller.role not in PERSONAL_AGENT_ROLES:
        raise HTTPException(403, f"Role '{caller.role or 'unknown'}' cannot create agents")
    if requested == AGENT_VISIBILITY_TENANT:
        raise HTTPException(403, "Only a tenant admin can create shared agents")
    if caller.role not in ANY_DOMAIN_PERSONAL_ROLES and not _agent_domain_allowed(domain, caller):
        raise HTTPException(403, f"You do not have access to the '{domain}' domain.")
    return AGENT_VISIBILITY_PERSONAL, caller.user_id


def check_agent_domain_change(agent: Any, new_domain: str | None, caller: Caller) -> None:
    """Domain changes: admins anywhere; owners within their allowed domains."""
    if new_domain is None or caller.is_admin:
        return
    if is_personal_agent(agent) and caller.role in ANY_DOMAIN_PERSONAL_ROLES:
        return
    if not _agent_domain_allowed(new_domain, caller):
        raise HTTPException(403, f"You do not have access to the '{new_domain}' domain.")


def check_agent_visibility_change(agent: Any, new_visibility: str | None, caller: Caller) -> None:
    if new_visibility is None:
        return
    value = new_visibility.strip().lower()
    if value not in AGENT_VISIBILITIES:
        raise HTTPException(422, f"visibility must be one of {', '.join(AGENT_VISIBILITIES)}")
    current = str(getattr(agent, "visibility", "") or AGENT_VISIBILITY_TENANT)
    if value != current and not caller.is_admin:
        raise HTTPException(403, "Only a tenant admin can change an agent's visibility")


def agent_ownership_fields(agent: Any) -> dict[str, Any]:
    owner = getattr(agent, "owner_user_id", None)
    return {
        "visibility": str(getattr(agent, "visibility", "") or AGENT_VISIBILITY_TENANT),
        "owner_user_id": str(owner) if owner else None,
    }


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------


def is_personal_connector(connector: Any) -> bool:
    return getattr(connector, "owner_user_id", None) is not None


def is_connector_owner(connector: Any, caller: Caller) -> bool:
    owner = getattr(connector, "owner_user_id", None)
    return caller.user_id is not None and owner is not None and str(owner) == str(caller.user_id)


def can_view_connector(connector: Any, caller: Caller) -> bool:
    if connector is None:
        return False
    if not is_personal_connector(connector):
        return True
    return caller.is_admin or is_connector_owner(connector, caller)


def can_mutate_connector(connector: Any, caller: Caller) -> bool:
    if connector is None:
        return False
    if caller.is_admin:
        return True
    return is_personal_connector(connector) and is_connector_owner(connector, caller)


def require_connector_visible(connector: Any, caller: Caller) -> None:
    if not can_view_connector(connector, caller):
        raise HTTPException(404, "Connector not found")


def require_connector_mutable(connector: Any, caller: Caller) -> None:
    require_connector_visible(connector, caller)
    if not can_mutate_connector(connector, caller):
        raise HTTPException(403, "Only a tenant admin or the connector's owner can change this connector")


def connector_visibility_clause(connector_model: Any, caller: Caller) -> ColumnElement[bool]:
    shared = connector_model.owner_user_id.is_(None)
    if caller.is_admin:
        return true()
    if caller.user_id is None:
        return shared
    return or_(shared, connector_model.owner_user_id == caller.user_id)


def resolve_new_connector_owner(caller: Caller) -> uuid.UUID | None:
    """Admins create shared connectors; other permitted humans create their own."""
    if caller.is_admin:
        return None
    if caller.is_machine or caller.user_id is None or caller.role not in PERSONAL_AGENT_ROLES:
        raise HTTPException(403, "Only a tenant admin can register shared connectors")
    return caller.user_id


def connector_ownership_fields(connector: Any) -> dict[str, Any]:
    owner = getattr(connector, "owner_user_id", None)
    return {
        "owner_user_id": str(owner) if owner else None,
        "visibility": CONNECTOR_VISIBILITY_PERSONAL if owner else CONNECTOR_VISIBILITY_SHARED,
    }


def connector_link_allowed(connector: Any, agent_visibility: str, agent_owner: uuid.UUID | None) -> bool:
    """May an agent with this visibility/owner use this connector's credentials?"""
    if not is_personal_connector(connector):
        return True
    return (
        agent_visibility == AGENT_VISIBILITY_PERSONAL
        and agent_owner is not None
        and str(connector.owner_user_id) == str(agent_owner)
    )


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def approval_visibility_clause(agent_model: Any, caller: Caller) -> ColumnElement[bool]:
    """Filter on the joined/subqueried Agent for approval listings."""
    if caller.is_admin:
        return true()
    if caller.user_id is None and caller.is_machine:
        return agent_model.visibility == AGENT_VISIBILITY_TENANT
    own = (
        and_(
            agent_model.visibility == AGENT_VISIBILITY_PERSONAL,
            agent_model.owner_user_id == caller.user_id,
        )
        if caller.user_id is not None
        else false()
    )
    if caller.role in OWN_APPROVALS_ONLY_ROLES:
        return own
    shared = agent_model.visibility == AGENT_VISIBILITY_TENANT
    if isinstance(caller.domains, list):
        shared = and_(shared, agent_model.domain.in_(caller.domains))
    return or_(shared, own)


def personal_approval_decision(agent: Any, caller: Caller) -> bool | None:
    """Ownership verdict for deciding an approval.

    ``True``: allowed by ownership (skip the domain/role hierarchy).
    ``False``: denied by ownership.
    ``None``: not a personal agent; apply the normal hierarchy, except for
    roles limited to their own approvals, which are denied.
    """
    if agent is not None and is_personal_agent(agent):
        return caller.is_admin or is_agent_owner(agent, caller)
    if caller.role in OWN_APPROVALS_ONLY_ROLES and not caller.is_admin:
        return False
    return None
