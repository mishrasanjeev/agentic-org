# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The hand-off into a governed case: a submitted application becomes a case the checks run on.

A submitted application maps to a ``business_case`` application. ``open_case``
creates the governed case and records its reference on the application in one
transaction, so an application never has two cases and a case never loses its
application. From there the governed-case workflow takes over: the checks
through the configured verification provider, the policy result and memo, the
review queue, decisions recorded as decision grants, the signed case push to
the partner and the case's evidence.

A partner turns this on per tenant with the case settings: ``auto_open``
opens the case when the applicant submits, and ``investigate`` starts the
checks as soon as the case is open. Both are off by default, and governed
cases must also be on for the tenant (``governed_cases.enabled``). Nothing
here ever approves or declines an application; that stays with a person.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from core.onboarding_desk import applications, questions, store
from core.onboarding_desk.store import DeskError

logger = structlog.get_logger()

CASE_PURPOSE = "aml.cdd.onboarding"
WORKFLOW_ACTOR = "workflow:onboarding_desk"


@dataclass(frozen=True)
class CaseSettings:
    #: Open the governed case as soon as the applicant submits.
    auto_open: bool = False
    #: Start the checks as soon as the case is open.
    investigate: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {"auto_open": self.auto_open, "investigate": self.investigate}


def case_settings_from(raw: dict[str, Any] | None) -> CaseSettings:
    raw = dict(raw or {})
    if set(raw) - {"auto_open", "investigate"}:
        raise DeskError(422, "case_settings_invalid", "case settings may only set auto_open and investigate")
    for name in ("auto_open", "investigate"):
        if not isinstance(raw.get(name, False), bool):
            raise DeskError(422, "case_settings_invalid", f"{name} must be true or false")
    return CaseSettings(auto_open=raw.get("auto_open", False), investigate=raw.get("investigate", False))


async def get_case_settings(tenant_id: uuid.UUID) -> CaseSettings:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    async with store._session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
    return case_settings_from((row.settings or {}).get("cases") if row else None)


async def put_case_settings(tenant_id: uuid.UUID, raw: dict[str, Any], *, actor: str) -> CaseSettings:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    case_settings = case_settings_from(raw)
    async with store._session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if row is None:
            session.add(
                OnboardingPolicy(
                    tenant_id=tenant_id, settings={"cases": case_settings.to_dict()}, updated_by=actor[:128]
                )
            )
        else:
            row.settings = {**(row.settings or {}), "cases": case_settings.to_dict()}
            row.updated_by = actor[:128]
            row.updated_at = datetime.now(UTC)
    return case_settings


def _runtime() -> Any:
    from core.cases.runtime import CaseRuntime

    return CaseRuntime()


async def _record_error(tenant_id: uuid.UUID, application_id: uuid.UUID, code: str) -> None:
    async with store._session(tenant_id) as session:
        row = await applications._locked(session, tenant_id, application_id)
        row.case_error = code[:64]


async def open_case(
    tenant_id: uuid.UUID,
    application_id: uuid.UUID,
    *,
    actor: str = WORKFLOW_ACTOR,
    runtime: Any = None,
    now: datetime | None = None,
) -> tuple[str, bool]:
    """Open the governed case for a submitted application: ``(case_ref, created)``; idempotent."""
    from core.cases.runtime import default_policy_id
    from core.cases.states import CaseError
    from core.cases.store import create_case
    from core.config import settings

    moment = now or datetime.now(UTC)
    runtime = runtime or _runtime()
    try:
        await runtime.require_enabled(tenant_id)
    except CaseError as exc:
        raise DeskError(409, "governed_cases_disabled", "governed cases are off for this tenant") from exc
    current = await applications.get_application(tenant_id, application_id)
    if current.case_ref:
        return current.case_ref, False
    if current.status != "submitted":
        raise DeskError(409, "application_not_submitted", "only a submitted application can open a case")
    answers = (await applications._answers(current))["values"]
    try:
        case_application = questions.business_case_application(answers, country=current.country, region=current.region)
        policy_id = default_policy_id(case_application["jurisdiction"])
    except (KeyError, CaseError) as exc:
        code = exc.reason if isinstance(exc, CaseError) else "application_incomplete"
        await _record_error(tenant_id, application_id, code)
        raise DeskError(422, code, "the application cannot open a case") from exc
    refused: CaseError | None = None
    created_ref = ""
    async with store._session(tenant_id) as session:
        row = await applications._locked(session, tenant_id, application_id)
        if row.case_ref:
            return row.case_ref, False
        try:
            case = await create_case(
                session,
                tenant_id=tenant_id,
                application=case_application,
                purpose=CASE_PURPOSE,
                provider=settings.case_provider,
                policy_id=policy_id,
                created_by=actor,
                now=moment,
            )
        except CaseError as exc:
            refused = exc
        else:
            row.case_ref = case.case_ref
            row.case_opened_at = moment
            row.case_error = None
            created_ref = case.case_ref
    if refused is not None:
        await _record_error(tenant_id, application_id, refused.reason)
        raise DeskError(422, refused.reason, refused.detail or "the governed case refused the application")
    logger.info("onboarding_case_opened", policy_id=policy_id)
    return created_ref, True


async def investigate(
    tenant_id: uuid.UUID, case_ref: str, *, actor: str = WORKFLOW_ACTOR, runtime: Any = None
) -> dict[str, Any]:
    """Run the checks on an open case; a refusal is reported, never raised."""
    from core.cases.runtime import investigate_case
    from core.cases.states import CaseError

    try:
        await investigate_case(tenant_id, case_ref, runtime=runtime or _runtime(), actor=actor)
    except CaseError as exc:
        logger.warning("onboarding_case_investigation_refused", reason=exc.reason)
        return {"case_ref": case_ref, "investigated": False, "reason": exc.reason}
    return {"case_ref": case_ref, "investigated": True}


async def open_and_investigate(
    tenant_id: uuid.UUID, application_id: uuid.UUID, *, investigate_now: bool | None = None, runtime: Any = None
) -> dict[str, Any]:
    """Open the case and, when the tenant's settings (or the caller) say so, start the checks."""
    case_ref, created = await open_case(tenant_id, application_id, runtime=runtime)
    run = (await get_case_settings(tenant_id)).investigate if investigate_now is None else investigate_now
    result: dict[str, Any] = {"case_ref": case_ref, "created": created, "investigated": False}
    if run and created:
        result.update(await investigate(tenant_id, case_ref, runtime=runtime))
    return result


async def after_submit(tenant_id: uuid.UUID, application_id: uuid.UUID) -> bool:
    """Queue the case hand-off for a just-submitted application when the tenant asks for it."""
    if not (await get_case_settings(tenant_id)).auto_open:
        return False
    from core.tasks.onboarding_desk_tasks import open_case_task

    try:
        open_case_task.delay(str(tenant_id), str(application_id))
    # enterprise-gate: broad-except-ok reason=queue-failure-logged-and-the-operator-can-open-the-case-by-hand
    except Exception as exc:  # noqa: BLE001 - the broker's own error types vary
        logger.warning("onboarding_case_queue_failed", error=type(exc).__name__)
        return False
    return True


def case_view(case: Any) -> dict[str, Any]:
    """What the desk shows about a governed case: its state, recommendation, tier and decision."""
    memo = case.memo or {}
    decision = case.decision or {}
    return {
        "case_ref": case.case_ref,
        "state": case.state,
        "policy_id": case.policy_id,
        "recommendation": (memo.get("recommendation") or {}).get("proposed"),
        "tier": (case.policy_result or {}).get("tier"),
        "decision": decision.get("outcome"),
        "decided_at": decision.get("decided_at"),
        "failure_reason": case.failure_reason,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


async def get_case(tenant_id: uuid.UUID, case_ref: str) -> Any:
    from sqlalchemy import select

    from core.models.governed_case import GovernedCase

    async with store._session(tenant_id) as session:
        return (
            await session.execute(
                select(GovernedCase).where(GovernedCase.tenant_id == tenant_id, GovernedCase.case_ref == case_ref)
            )
        ).scalar_one_or_none()


async def application_for_case(tenant_id: uuid.UUID, case_ref: str) -> Any:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingApplication

    async with store._session(tenant_id) as session:
        row = (
            await session.execute(
                select(OnboardingApplication).where(
                    OnboardingApplication.tenant_id == tenant_id, OnboardingApplication.case_ref == case_ref
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise DeskError(404, "case_not_found", "no application opened this case")
    return row
