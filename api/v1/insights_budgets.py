# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights budgets (``/insights/budgets``): list, create, change and delete the organisation's spend limits.

Administrators and the CFO change budgets (a person, never an API key: the
change is audited to them); auditors and both of those read them. Each row
carries this period's ledger spend, the percent used and the state, as the
budget evaluator and the ``cost.budgets`` metric compute them
(core/insights/budgets.py).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1.insights import _company, _require_enabled, _tenant
from core.database import get_tenant_session
from core.insights import budgets
from core.ownership import Caller, caller_from_request

router = APIRouter(prefix="/insights/budgets", tags=["Insights"])


class BudgetIn(BaseModel):
    name: str = Field(min_length=1, max_length=budgets.MAX_NAME)
    period: str = Field(min_length=1, max_length=20)
    threshold_usd: Decimal
    warn_at_percent: int = 80
    company_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None
    notify_channels: list[str] | None = None


class BudgetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=budgets.MAX_NAME)
    period: str | None = Field(default=None, min_length=1, max_length=20)
    threshold_usd: Decimal | None = None
    warn_at_percent: int | None = None
    company_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None
    notify_channels: list[str] | None = None


def _refused(exc: budgets.BudgetError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.error, "message": exc.message})


def _reader(request: Request) -> Caller:
    caller = caller_from_request(request)
    if not budgets.may_read(caller.role, is_admin=caller.is_admin):
        raise HTTPException(403, "insights_budgets_forbidden")
    return caller


def _manager(request: Request) -> uuid.UUID:
    caller = caller_from_request(request)
    if not budgets.may_manage(caller.role, is_admin=caller.is_admin):
        raise HTTPException(403, "insights_budgets_forbidden")
    if caller.is_machine or caller.user_id is None:
        raise HTTPException(403, "insights_budget_change_needs_a_person")
    return caller.user_id


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.budgets.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.budgets.read",
)
async def list_budgets(
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    company_id: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    """The organisation's budgets (one company's with ``company_id``), each with this period's spend and state."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _reader(request)
    company = await _company(tid, company_id)
    async with get_tenant_session(tid) as session:
        rows = await budgets.list_budgets(session, tid, company_id=company)
    return {
        "budgets": rows,
        "can_edit": budgets.may_manage(caller.role, is_admin=caller.is_admin) and not caller.is_machine,
        "periods": list(budgets.PERIODS),
        "channels": list(budgets.CHANNELS),
        "max_threshold_usd": format(budgets.MAX_THRESHOLD, "f"),
    }


@router.post("", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.budgets.write",
    rate_limit="reporting-admin",
    idempotency="conflict-on-duplicate-name",
    audit_event="insights.budget.created",
)
async def create_budget(
    body: BudgetIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Add a budget: a USD limit for a day, week or month, for the organisation or one company or cost centre."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    actor = _manager(request)
    async with get_tenant_session(tid) as session:
        try:
            return {"budget": await budgets.create_budget(session, tid, actor, body.model_dump())}
        except budgets.BudgetError as exc:
            raise _refused(exc) from None


@router.patch("/{budget_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.budgets.write",
    rate_limit="reporting-admin",
    idempotency="idempotent-partial-update",
    audit_event="insights.budget.updated",
)
async def update_budget(
    body: BudgetPatch,
    request: Request,
    budget_id: Annotated[uuid.UUID, Path()],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Change a budget's name, period, limit, warn-at percent, scope or channels. A new limit warns afresh."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    actor = _manager(request)
    changes = body.model_dump(exclude_unset=True)
    async with get_tenant_session(tid) as session:
        try:
            return {"budget": await budgets.update_budget(session, tid, budget_id, actor, changes)}
        except budgets.BudgetError as exc:
            raise _refused(exc) from None


@router.delete("/{budget_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.budgets.write",
    rate_limit="reporting-admin",
    idempotency="idempotent-delete",
    audit_event="insights.budget.deleted",
)
async def delete_budget(
    request: Request,
    budget_id: Annotated[uuid.UUID, Path()],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Delete a budget; the audit row keeps what it was."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    actor = _manager(request)
    async with get_tenant_session(tid) as session:
        try:
            await budgets.delete_budget(session, tid, budget_id, actor)
        except budgets.BudgetError as exc:
            raise _refused(exc) from None
    return {"deleted": str(budget_id)}
