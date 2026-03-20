"""Agent team endpoints."""
from __future__ import annotations

import uuid as _uuid
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel as PydanticBaseModel, Field
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
