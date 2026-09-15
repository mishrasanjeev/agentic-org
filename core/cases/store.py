# SPDX-License-Identifier: Apache-2.0
"""Persistence for governed cases: create, read, list, transition and save agent results.

Every function takes a tenant-scoped session (``core.database.get_tenant_session``) *and* filters
by tenant explicitly, so isolation holds even if row-level security were misconfigured. Writes
use the row's ``version`` to refuse a concurrent change (``case_version_conflict``) instead of
overwriting it.
"""

from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.cases.states import CaseError, CaseState, case_transitions_total, check_transition
from core.domain_schemas import DomainSchemaError, validate
from core.models.governed_case import GovernedCase, GovernedCaseTransition

logger = structlog.get_logger()

CASE_REF_RE = re.compile(r"^case_[0-9a-f]{24}$")
PURPOSE_RE = re.compile(r"^([a-z][a-z0-9_]*|x-[a-z0-9][a-z0-9\-]*)(\.[a-z][a-z0-9_]*)+$")
MAX_LIST = 200


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _tenant_uuid(tenant_id: str | uuid.UUID) -> uuid.UUID:
    try:
        return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    except ValueError as exc:
        raise CaseError("tenant_invalid", status=401) from exc


def new_case_ref() -> str:
    return f"case_{secrets.token_hex(12)}"


def _iso(value: datetime | None) -> str:
    moment = value or _utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.isoformat()


def business_case_document(case: GovernedCase) -> dict[str, Any]:
    """The case as a ``business_case`` schema document."""
    return {
        "schema_version": "1.0.0",
        "case_id": case.case_ref,
        "purpose": case.purpose,
        "state": case.state,
        "created_at": _iso(case.created_at),
        "updated_at": _iso(case.updated_at),
        "application": case.application,
        "subject": case.subject,
        "decision": case.decision if case.state == CaseState.DECIDED else None,
    }


async def create_case(
    session: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
    application: Mapping[str, Any],
    purpose: str,
    provider: str,
    policy_id: str,
    created_by: str,
    now: datetime | None = None,
) -> GovernedCase:
    """Create a ``submitted`` case. The application must validate as a ``business_case`` application."""
    tenant = _tenant_uuid(tenant_id)
    at = now or _utc_now()
    if not PURPOSE_RE.match(purpose or ""):
        raise CaseError("purpose_invalid", status=422)
    case = GovernedCase(
        id=uuid.uuid4(),
        tenant_id=tenant,
        case_ref=new_case_ref(),
        purpose=purpose,
        state=CaseState.SUBMITTED.value,
        provider=provider,
        policy_id=policy_id,
        application=dict(application),
        subject=None,
        screening_results=[],
        screening_dispositions=[],
        parties=[],
        agent_records=[],
        information_requests=[],
        version=1,
        created_by=created_by[:256],
        created_at=at,
        updated_at=at,
    )
    try:
        validate("business_case", business_case_document(case))
    except DomainSchemaError as exc:
        raise CaseError("application_invalid", "; ".join(exc.errors[:5]), status=422) from exc
    session.add(case)
    session.add(
        GovernedCaseTransition(
            id=uuid.uuid4(), tenant_id=tenant, case_id=case.id, case_version=1, from_state=None,
            to_state=CaseState.SUBMITTED.value, actor=created_by[:256], reason="case_submitted", created_at=at,
        )
    )  # fmt: skip
    case_transitions_total.labels(from_state="none", to_state=CaseState.SUBMITTED.value).inc()
    await session.flush()
    return case


async def get_case(
    session: AsyncSession, tenant_id: str | uuid.UUID, case_ref: str, *, for_update: bool = False
) -> GovernedCase:
    if not isinstance(case_ref, str) or not CASE_REF_RE.match(case_ref):
        raise CaseError("case_not_found", status=404)
    query = select(GovernedCase).where(
        GovernedCase.tenant_id == _tenant_uuid(tenant_id), GovernedCase.case_ref == case_ref
    )
    if for_update:
        query = query.with_for_update()
    case = (await session.execute(query)).scalar_one_or_none()
    if case is None:
        raise CaseError("case_not_found", status=404)
    return case


async def list_cases(
    session: AsyncSession, tenant_id: str | uuid.UUID, *, state: str | None = None, limit: int = 50
) -> list[GovernedCase]:
    query = select(GovernedCase).where(GovernedCase.tenant_id == _tenant_uuid(tenant_id))
    if state is not None:
        try:
            query = query.where(GovernedCase.state == CaseState(state).value)
        except ValueError as exc:
            raise CaseError("case_state_unknown", state, status=422) from exc
    query = query.order_by(GovernedCase.updated_at.desc(), GovernedCase.case_ref).limit(max(1, min(limit, MAX_LIST)))
    return list((await session.execute(query)).scalars())


async def counts_by_state(session: AsyncSession, tenant_id: str | uuid.UUID) -> dict[str, int]:
    rows = await session.execute(
        select(GovernedCase.state, func.count())
        .where(GovernedCase.tenant_id == _tenant_uuid(tenant_id))
        .group_by(GovernedCase.state)
    )
    counts = {state.value: 0 for state in CaseState}
    for state, count in rows:
        counts[str(state)] = int(count)
    return counts


async def transition(
    session: AsyncSession,
    case: GovernedCase,
    target: CaseState,
    *,
    actor: str,
    reason: str,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> GovernedCase:
    """Move ``case`` to ``target`` after checking the lifecycle and the version, recording the transition."""
    current = check_transition(case.state, target)
    if expected_version is not None and case.version != expected_version:
        raise CaseError("case_version_conflict", f"expected {expected_version}, found {case.version}")
    at = now or _utc_now()
    case.state = target.value
    case.version = case.version + 1
    case.updated_at = at
    if target is CaseState.AWAITING_DECISION:
        case.completed_at = at
    session.add(
        GovernedCaseTransition(
            id=uuid.uuid4(), tenant_id=case.tenant_id, case_id=case.id, case_version=case.version,
            from_state=current.value,
            to_state=target.value, actor=actor[:256], reason=reason[:128], created_at=at,
        )
    )  # fmt: skip
    await session.flush()
    case_transitions_total.labels(from_state=current.value, to_state=target.value).inc()
    logger.info(
        "governed_case_transition",
        tenant_id=str(case.tenant_id),
        case_ref=case.case_ref,
        from_state=current.value,
        to_state=target.value,
        reason=reason,
    )
    return case


async def transitions_for(session: AsyncSession, case: GovernedCase) -> list[GovernedCaseTransition]:
    rows = await session.execute(
        select(GovernedCaseTransition)
        .where(GovernedCaseTransition.tenant_id == case.tenant_id, GovernedCaseTransition.case_id == case.id)
        .order_by(GovernedCaseTransition.case_version)
    )
    return list(rows.scalars())
