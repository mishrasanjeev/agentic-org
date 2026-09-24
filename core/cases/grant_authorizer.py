# SPDX-License-Identifier: Apache-2.0
"""Grant checks for provider calls made by governed-case agents."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select

from auth.grant_enforcement import EnforcementMode, GrantCallContext
from auth.run_grants import RunGrant, check_run_grant, resolve_run_grant
from core.cases.store import PURPOSE_RE
from core.database import get_tenant_session
from core.models.agent import Agent
from core.tool_gateway.provider_gateway import ToolDecision

logger = structlog.get_logger()

CASE_AGENT_ROLES = frozenset({"business_underwriter", "screening_disposition"})


def validate_case_purposes(values: Any) -> list[str]:
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 16
        or any(not isinstance(value, str) or len(value) > 128 or not PURPOSE_RE.fullmatch(value) for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError("case_purposes must contain 1 to 16 distinct, valid purpose names")
    return list(values)


async def _active_agent(tenant_id: str, role: str) -> tuple[str, dict[str, Any]] | None:
    """Require one active, shared tenant agent for the reference role."""
    tenant = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant) as session:
        rows = (
            await session.execute(
                select(Agent.id, Agent.config)
                .where(
                    Agent.tenant_id == tenant,
                    Agent.agent_type == role,
                    Agent.status == "active",
                    Agent.visibility == "tenant",
                    Agent.owner_user_id.is_(None),
                    Agent.company_id.is_(None),
                )
                .limit(2)
            )
        ).all()
    if len(rows) != 1:
        return None
    agent_id, raw_config = rows[0]
    config = raw_config.get("grantex") if isinstance(raw_config, dict) else None
    grantex_config = config if isinstance(config, dict) else {}
    # A case agent may only receive a delegated token for its registered identity.
    # A legacy token in its config could belong to a different agent.
    return str(agent_id), {
        "grantex_agent_id": grantex_config.get("grantex_agent_id"),
        "grantex_scopes": grantex_config.get("grantex_scopes"),
        "case_purposes": grantex_config.get("case_purposes"),
    }


@dataclass(frozen=True, slots=True)
class CaseGrantAuthorizer:
    tenant_id: str
    case_ref: str
    role: str
    purpose: str

    async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
        if self.role not in CASE_AGENT_ROLES:
            return ToolDecision(allowed=False, reason="authorization_unavailable", sub_reason="case_role_unknown")
        missing_reason = "case_agent_not_configured"
        try:
            agent = await _active_agent(self.tenant_id, self.role)
        except asyncio.CancelledError:
            raise
        # enterprise-gate: broad-except-ok reason=agent-lookup-failure-is-converted-to-a-missing-grant-denial
        except Exception as exc:
            logger.error("case_agent_lookup_failed", role=self.role, error_type=type(exc).__name__)
            agent = None
            missing_reason = "agent_lookup_failed"
        if agent is None:
            agent_id = ""
            grant = RunGrant(mode=EnforcementMode.DENY, source="none", missing_sub_reason=missing_reason)
        else:
            agent_id, config = agent
            try:
                allowed_purposes = validate_case_purposes(config.get("case_purposes"))
            except ValueError:
                allowed_purposes = []
            if self.purpose not in allowed_purposes:
                return ToolDecision(
                    allowed=False,
                    reason="purpose_not_allowed",
                    sub_reason="case_purpose_not_registered",
                )
            grant = await resolve_run_grant(
                tenant_id=self.tenant_id,
                agent_id=agent_id,
                grantex_config=config,
                mode=EnforcementMode.DENY,
                runtime="governed_case",
            )
        check = await check_run_grant(
            grant,
            connector=connector,
            tool=tool,
            context=GrantCallContext(
                tenant_id=self.tenant_id,
                agent_id=agent_id,
                agent_type=self.role,
                runtime="governed_case",
                grant_source=grant.source,
                extra={"case_ref": self.case_ref, "purpose": self.purpose},
            ),
        )
        denial = check.denial
        return ToolDecision(
            allowed=check.dispatch_allowed,
            reason=denial.reason.value if denial is not None else "",
            sub_reason=denial.sub_reason if denial is not None else "",
        )


def case_authorizer(tenant_id: str, case_ref: str, role: str, purpose: str) -> CaseGrantAuthorizer:
    return CaseGrantAuthorizer(tenant_id=tenant_id, case_ref=case_ref, role=role, purpose=purpose)
