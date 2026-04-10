"""Agent team endpoints."""

from __future__ import annotations

import uuid as _uuid
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from sqlalchemy import select

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.models.agent import Agent, AgentTeam, AgentTeamMember

router = APIRouter()


class TeamMemberInput(PydanticBaseModel):
    agent_id: UUID
    role: str
    weight: float = 1.0


class TeamCreateRequest(PydanticBaseModel):
    name: str
    domain: str | None = None
    routing_rules: list[dict] = Field(default_factory=list)
    members: list[TeamMemberInput] = Field(default_factory=list)


def _team_to_dict(team: AgentTeam, members: list[AgentTeamMember] | None = None) -> dict:
    d = {
        "team_id": str(team.id),
        "name": team.name,
        "domain": team.domain,
        "routing_rules": team.routing_rules,
        "status": team.status,
        "created_at": team.created_at.isoformat() if team.created_at else None,
    }
    if members is not None:
        d["members"] = [
            {
                "agent_id": str(m.agent_id),
                "role": m.role,
                "weight": float(m.weight),
                "added_at": m.added_at.isoformat() if m.added_at else None,
            }
            for m in members
        ]
    return d


# ── GET /agent-teams ────────────────────────────────────────────────────────
@router.get("/agent-teams")
async def list_teams(
    domain: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """List all agent teams for the current tenant."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(AgentTeam).where(AgentTeam.tenant_id == tid)
        if domain:
            query = query.where(AgentTeam.domain == domain)
        query = query.order_by(AgentTeam.created_at.desc())
        result = await session.execute(query)
        teams = list(result.scalars().all())

        if not teams:
            return {"items": [], "total": 0}

        # Fix N+1: fetch all members for all teams in ONE query
        team_ids = [t.id for t in teams]
        members_result = await session.execute(
            select(AgentTeamMember).where(AgentTeamMember.team_id.in_(team_ids))
        )
        all_members = list(members_result.scalars().all())
        # Group by team_id
        members_by_team: dict[UUID, list[AgentTeamMember]] = {}
        for m in all_members:
            members_by_team.setdefault(m.team_id, []).append(m)

        items = [_team_to_dict(team, members_by_team.get(team.id, [])) for team in teams]

    return {"items": items, "total": len(items)}


# ── GET /agent-teams/{team_id} ─────────────────────────────────────────────
@router.get("/agent-teams/{team_id}")
async def get_team(
    team_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """Get a single agent team by ID."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(AgentTeam).where(AgentTeam.id == team_id, AgentTeam.tenant_id == tid)
        )
        team = result.scalar_one_or_none()
        if not team:
            raise HTTPException(404, "Agent team not found")

        member_result = await session.execute(
            select(AgentTeamMember).where(AgentTeamMember.team_id == team.id)
        )
        members = member_result.scalars().all()

    return _team_to_dict(team, list(members))


# ── POST /agent-teams ───────────────────────────────────────────────────────
@router.post("/agent-teams", status_code=201)
async def create_team(
    body: TeamCreateRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # Validate all referenced agents exist and belong to this tenant
        if body.members:
            agent_ids = [m.agent_id for m in body.members]
            result = await session.execute(
                select(Agent.id).where(
                    Agent.id.in_(agent_ids),
                    Agent.tenant_id == tid,
                )
            )
            found_ids = {row[0] for row in result.all()}
            missing = set(agent_ids) - found_ids
            if missing:
                raise HTTPException(
                    404,
                    f"Agents not found: {[str(a) for a in sorted(missing)]}",
                )

        team = AgentTeam(
            tenant_id=tid,
            name=body.name,
            domain=body.domain,
            routing_rules=body.routing_rules,
            status="active",
        )
        session.add(team)
        await session.flush()

        member_rows = []
        for m in body.members:
            member = AgentTeamMember(
                team_id=team.id,
                agent_id=m.agent_id,
                role=m.role,
                weight=Decimal(str(m.weight)),
            )
            session.add(member)
            member_rows.append(member)

        await session.flush()

    return _team_to_dict(team, member_rows)
