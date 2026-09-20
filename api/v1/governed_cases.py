# SPDX-License-Identifier: Apache-2.0
"""Governed business cases: submit, investigate, retrieve, review dispositions and record decisions.

Every route needs an authenticated tenant user and is hidden (404 ``governed_cases_disabled``)
unless the tenant's ``governed_cases.enabled`` flag is on. Scopes use the ``approvals`` family:
reads need ``approvals:read``, writes ``approvals:write``. Identities written into a case (who
submitted it, who reviewed a disposition, who approved an information request) always come from
the authenticated session, never from the request body.

A decision is recorded only with a verified decision grant; until decision grants are wired in,
``POST /governed-cases/{case_ref}/decision`` answers 403 ``decision_required``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from api.deps import get_current_tenant, get_current_user
from api.route_metadata import route_meta
from core.agents.business_underwriter.information_request import InformationRequestError, propose, render
from core.agents.screening_disposition import DispositionReviewError, DispositionReviewRequest, apply_review
from core.cases.runtime import CaseRuntime, decide_case, default_policy_id, investigate_case
from core.cases.states import CaseError, CaseState
from core.cases.store import (
    business_case_document,
    counts_by_state,
    create_case,
    get_case,
    list_cases,
    record_update,
    transition,
)
from core.cases.store import transitions_for as case_transitions

logger = structlog.get_logger()

router = APIRouter()

DEFAULT_PURPOSE = "aml.cdd.onboarding"


def get_case_runtime() -> CaseRuntime:
    """Overridden in tests; production builds the default runtime per request."""
    return CaseRuntime()


def _actor(user: dict[str, Any]) -> str:
    user_id = user.get("agenticorg:user_id")
    if user_id:
        return f"user:{user_id}"
    subject = str(user.get("sub") or "")
    if not subject:
        raise CaseError("actor_unknown", status=401)
    return f"user:{subject}"[:256]


def _error(exc: CaseError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"reason": exc.reason, "detail": exc.detail}})


def _session(tenant_id: str) -> Any:
    from core.database import get_tenant_session

    return get_tenant_session(uuid.UUID(tenant_id))


class SubmitCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application: dict[str, Any]
    purpose: str = Field(default=DEFAULT_PURPOSE, max_length=128)
    policy_id: str | None = Field(default=None, max_length=128, pattern=r"^[a-z][a-z0-9_]{1,127}$")


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approve", "decline"]
    decision_grants: Annotated[list[Annotated[str, Field(min_length=1, max_length=8192)]], Field(max_length=2)] = []
    #: Record the decision with the grants of this request; the server fetches them from the
    #: issuer, so a decision grant never reaches the browser.
    decision_request_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    #: Advisory only: milliseconds from the case screen rendering to this submission. The
    #: authoritative dwell is the one the approval page measured (``dwell_source: server``).
    client_dwell_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class NewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approve", "decline"]
    #: Why a decision other than the memo's recommendation is being asked for; shown to the approver.
    override_reason: str = Field(default="", max_length=4000)
    client_dwell_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class InformationRequestProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    template_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _summary(case: Any) -> dict[str, Any]:
    memo = case.memo or {}
    return {
        "case_ref": case.case_ref,
        "state": case.state,
        "purpose": case.purpose,
        "legal_name": (case.application or {}).get("legal_name"),
        "jurisdiction": (case.application or {}).get("jurisdiction"),
        "recommendation": (memo.get("recommendation") or {}).get("proposed"),
        "tier": (case.policy_result or {}).get("tier"),
        "failure_reason": case.failure_reason,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


@router.post("/governed-cases", status_code=201)
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.governed_cases.write", rate_limit="standard",
    idempotency="not-idempotent-case-creation", audit_event="governed_cases.create",
)  # fmt: skip
async def submit_case(
    body: SubmitCaseRequest,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    from core.config import settings

    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        jurisdiction = str(body.application.get("jurisdiction") or "")
        policy_id = body.policy_id or default_policy_id(jurisdiction)
        async with _session(tenant_id) as session:
            case = await create_case(
                session,
                tenant_id=tenant_id,
                application=body.application,
                purpose=body.purpose,
                provider=settings.case_provider,
                policy_id=policy_id,
                created_by=_actor(user),
                now=runtime.clock(),
            )
            return JSONResponse(status_code=201, content=_summary(case))
    except CaseError as exc:
        return _error(exc)


@router.get("/governed-cases")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.governed_cases.read", rate_limit="standard")
async def list_governed_cases(
    state: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            cases = await list_cases(session, tenant_id, state=state, limit=limit)
            return {"cases": [_summary(case) for case in cases]}
    except CaseError as exc:
        return _error(exc)


@router.get("/governed-cases/stats")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.governed_cases.read", rate_limit="standard")
async def governed_case_stats(
    tenant_id: str = Depends(get_current_tenant), runtime: CaseRuntime = Depends(get_case_runtime)
) -> Any:
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            return {"cases_by_state": await counts_by_state(session, tenant_id)}
    except CaseError as exc:
        return _error(exc)


@router.get("/governed-cases/{case_ref}")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.governed_cases.read", rate_limit="standard")
async def get_governed_case(
    case_ref: str, tenant_id: str = Depends(get_current_tenant), runtime: CaseRuntime = Depends(get_case_runtime)
) -> Any:
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            history = await case_transitions(session, case)
            return {
                "case": business_case_document(case),
                "memo": case.memo,
                "policy_result": case.policy_result,
                "ownership_graph": case.ownership_graph,
                "screening_results": case.screening_results,
                "screening_dispositions": case.screening_dispositions,
                "parties": case.parties,
                "information_requests": case.information_requests,
                "decision_requests": case.decision_requests,
                "decision": case.decision,
                "failure_reason": case.failure_reason,
                "transitions": [
                    {
                        "from_state": t.from_state,
                        "to_state": t.to_state,
                        "actor": t.actor,
                        "reason": t.reason,
                        "at": t.created_at.isoformat() if t.created_at else None,
                    }
                    for t in history
                ],  # fmt: skip
            }
    except CaseError as exc:
        return _error(exc)


@router.get("/governed-cases/{case_ref}/case-record")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.governed_cases.read", rate_limit="standard")
async def get_governed_case_record(
    case_ref: str, tenant_id: str = Depends(get_current_tenant), runtime: CaseRuntime = Depends(get_case_runtime)
) -> Any:
    """Agent case records for the evidence package: prompt digests, policy inputs, tool-call hashes."""
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            return {"case_ref": case.case_ref, "agent_records": case.agent_records, "decision": case.decision}
    except CaseError as exc:
        return _error(exc)


async def _investigate_in_background(tenant_id: str, case_ref: str, runtime: CaseRuntime, actor: str) -> None:
    try:
        await investigate_case(tenant_id, case_ref, runtime=runtime, actor=actor)
    except CaseError as exc:
        logger.warning("governed_case_investigation_refused", case_ref=case_ref, reason=exc.reason)


@router.post("/governed-cases/{case_ref}/investigate", status_code=202)
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.governed_cases.write", rate_limit="standard",
    idempotency="state-machine-refuses-a-second-start", audit_event="governed_cases.investigate",
)  # fmt: skip
async def start_investigation(
    case_ref: str,
    background: BackgroundTasks,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            if CaseState.IN_PROGRESS not in {CaseState(s) for s in _next_states(case.state)}:
                raise CaseError("transition_not_allowed", f"{case.state} -> in_progress")
        background.add_task(_investigate_in_background, tenant_id, case_ref, runtime, _actor(user))
        return JSONResponse(status_code=202, content={"case_ref": case_ref, "status": "investigation_scheduled"})
    except CaseError as exc:
        return _error(exc)


def _next_states(state: str) -> set[str]:
    from core.cases.states import TRANSITIONS

    try:
        return {s.value for s in TRANSITIONS[CaseState(state)]}
    except ValueError:
        return set()


@router.post("/governed-cases/{case_ref}/withdraw")
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.governed_cases.write", rate_limit="standard",
    idempotency="state-machine-refuses-a-second-withdrawal", audit_event="governed_cases.withdraw",
)  # fmt: skip
async def withdraw_case(
    case_ref: str,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref, for_update=True)
            await transition(
                session, case, CaseState.WITHDRAWN, actor=_actor(user), reason="withdrawn", now=runtime.clock()
            )
            result = {"case_ref": case_ref, "state": case.state}
        runtime.push_kick(uuid.UUID(tenant_id))
        return result
    except CaseError as exc:
        return _error(exc)


def _stored_request(case: Any, request_id: str) -> dict[str, Any]:
    """The case's own record of a decision request, so no other case's request can be read."""
    for record in case.decision_requests or []:
        if record.get("request_id") == request_id:
            return dict(record)
    raise CaseError("decision_request_not_found", status=404)


def _decision_service(runtime: CaseRuntime) -> Any:
    service = runtime.decision_service()
    if service is None:
        raise CaseError("decision_service_not_configured", "no decision-grant issuer is configured", status=503)
    return service


def _record_console_dwell(stage: str, dwell_ms: int | None, case_ref: str) -> None:
    """Advisory telemetry only: the authoritative dwell is measured by the approval page."""
    if dwell_ms is None:
        return
    from core.cases.decision_requests import console_dwell_seconds

    console_dwell_seconds.labels(stage=stage).observe(dwell_ms / 1000)
    logger.info("case_console_dwell", case_ref=case_ref, stage=stage, dwell_ms=dwell_ms, dwell_source="console")


@router.post("/governed-cases/{case_ref}/decision-requests", status_code=201)
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.governed_cases.write", rate_limit="standard",
    idempotency="issuer-returns-the-open-request-for-the-same-action", audit_event="governed_cases.decision_requested",
)  # fmt: skip
async def request_case_decision(
    case_ref: str,
    body: NewDecisionRequest,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    """Ask a named person to decide this case on the issuer's own approval page.

    The console never collects the approval: the answer carries the approval page's URL, and the
    approver signs in there, steps up, reads the memo and the policy score and approves. This route
    only creates the request and records it on the case.
    """
    from core.cases.decision_requests import (
        DecisionServiceError,
        decision_requests_total,
        four_eyes_on,
        policy_score_for_approval,
        render_memo_for_approval,
    )
    from core.cases.decisions import semantic_action

    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        actor = _actor(user)
        service = _decision_service(runtime)
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            if case.state != CaseState.AWAITING_DECISION:
                raise CaseError("transition_not_allowed", f"a decision needs awaiting_decision, case is {case.state}")
            if not case.memo or not case.policy_result:
                raise CaseError("memo_not_ready", "the case has no memo and policy result to approve against")
            action = semantic_action(case, body.outcome)
            case_version = str(case.version)
            memo_id = str((case.memo or {}).get("memo_id", ""))
            memo_text = render_memo_for_approval(case, body.outcome, body.override_reason.strip())
            policy_score = policy_score_for_approval(case)

        # The issuer is called with no database session and no row lock held.
        try:
            view = await service.create_request(
                action=action,
                case_version=case_version,
                memo=memo_text,
                policy_score=policy_score,
                four_eyes_on=four_eyes_on(),
                memo_ref=f"{case_ref}:memo:{memo_id}",
                policy_score_ref=f"{case_ref}:policy:{policy_score.get('inputs_digest', '')}",
            )
        except DecisionServiceError as exc:
            decision_requests_total.labels(outcome=body.outcome, result="refused").inc()
            raise CaseError(exc.reason, exc.detail, status=exc.status) from exc

        record = {
            "request_id": view.request_id,
            "outcome": body.outcome,
            "case_version": case_version,
            "approval_page": view.approval_page,
            "approvals_required": view.approvals_required,
            "action_hash": view.action_hash,
            "override_reason": body.override_reason.strip() or None,
            "requested_by": actor,
            "requested_at": runtime.clock().isoformat(),
            "console_dwell_ms": body.client_dwell_ms,
        }
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref, for_update=True)
            if str(case.version) != case_version:
                # The case changed while the request was being created, and the issuer bound the
                # request to the old version, so it can never be consumed. Say so.
                raise CaseError("case_version_conflict", f"expected {case_version}, found {case.version}")
            existing = [r for r in case.decision_requests or [] if r.get("request_id") != view.request_id]
            # Recording the request must not bump the case version: the request is bound to it.
            case.decision_requests = [*existing, record]
        decision_requests_total.labels(outcome=body.outcome, result="created").inc()
        _record_console_dwell("request", body.client_dwell_ms, case_ref)
        logger.info(
            "case_decision_requested", case_ref=case_ref, outcome=body.outcome,
            approvals_required=view.approvals_required,
        )  # fmt: skip
        return JSONResponse(status_code=201, content={**view.as_dict(), **record})
    except CaseError as exc:
        return _error(exc)


@router.get("/governed-cases/{case_ref}/decision-requests/{request_id}")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.governed_cases.read", rate_limit="standard")
async def get_case_decision_request(
    case_ref: str,
    request_id: str,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    """Live status of one decision request: who approved, the dwell the issuer measured, what is left."""
    from core.cases.decision_requests import DecisionServiceError

    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        service = _decision_service(runtime)
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            record = _stored_request(case, request_id)
            case_version = str(case.version)
        try:
            view = await service.get_request(request_id)
        except DecisionServiceError as exc:
            raise CaseError(exc.reason, exc.detail, status=exc.status) from exc
        return {
            **view.as_dict(),
            **record,
            "case_version_now": case_version,
            # A case that changed since the request can no longer be decided on it.
            "case_changed": record.get("case_version") != case_version,
        }
    except CaseError as exc:
        return _error(exc)


@router.post("/governed-cases/{case_ref}/decision")
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.governed_cases.write", rate_limit="standard",
    idempotency="state-machine-refuses-a-second-decision", audit_event="governed_cases.decision",
)  # fmt: skip
async def decide_governed_case(
    case_ref: str,
    body: DecisionRequest,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    from core.cases.decision_requests import DecisionServiceError

    try:
        grants = list(body.decision_grants)
        if body.decision_request_id:
            service = _decision_service(runtime)
            async with _session(tenant_id) as session:
                case = await get_case(session, tenant_id, case_ref)
                record = _stored_request(case, body.decision_request_id)
            if record.get("outcome") != body.outcome:
                raise CaseError("decision_outcome_mismatch", "the request was made for another outcome", status=409)
            try:
                # The grants stay on the server: a decision grant never reaches the browser.
                grants = await service.grants(body.decision_request_id)
            except DecisionServiceError as exc:
                raise CaseError(exc.reason, exc.detail, status=exc.status) from exc
            if not grants:
                raise CaseError("decision_not_approved", "the decision request has no usable grants yet", status=409)
        result = await decide_case(
            tenant_id, case_ref, runtime=runtime, actor=_actor(user), outcome=body.outcome, grants=grants,
        )  # fmt: skip
        _record_console_dwell("record", body.client_dwell_ms, case_ref)
        return result
    except CaseError as exc:
        return _error(exc)


@router.post("/governed-cases/{case_ref}/screening-dispositions/{hit_id}/review")
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.governed_cases.write", rate_limit="standard",
    idempotency="write-once-review", audit_event="governed_cases.disposition_review",
)  # fmt: skip
async def review_screening_disposition(
    case_ref: str,
    hit_id: str,
    body: DispositionReviewRequest,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref, for_update=True)
            dispositions = list(case.screening_dispositions or [])
            index = next((i for i, d in enumerate(dispositions) if d.get("hit_id") == hit_id), None)
            if index is None:
                raise CaseError("disposition_not_found", status=404)
            try:
                dispositions[index] = apply_review(
                    dispositions[index], body, analyst_id=_actor(user), reviewed_at=runtime.clock()
                )
            except DispositionReviewError as exc:
                raise CaseError(exc.reason, status=409 if exc.reason == "already_reviewed" else 422) from exc
            case.screening_dispositions = dispositions
            await record_update(session, case, now=runtime.clock())
            result = dispositions[index]
        runtime.push_kick(uuid.UUID(tenant_id))
        return result
    except CaseError as exc:
        return _error(exc)


@router.post("/governed-cases/{case_ref}/information-requests", status_code=201)
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.governed_cases.write", rate_limit="standard",
    idempotency="proposal-digest-deduplicates", audit_event="governed_cases.information_request_proposed",
)  # fmt: skip
async def propose_information_request(
    case_ref: str,
    body: InformationRequestProposal,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref, for_update=True)
            if case.state != CaseState.AWAITING_DECISION or not case.memo:
                raise CaseError("transition_not_allowed", "information requests need a memo awaiting decision")
            try:
                proposal = propose(case.memo, template_id=body.template_id, version=body.template_version)
            except InformationRequestError as exc:
                raise CaseError(exc.reason, status=422) from exc
            existing = [
                r for r in case.information_requests or [] if r.get("proposal_sha256") != proposal["proposal_sha256"]
            ]
            case.information_requests = [*existing, {**proposal, "status": "awaiting_approval"}]
            await record_update(session, case, now=runtime.clock())
            return JSONResponse(status_code=201, content={**proposal, "status": "awaiting_approval"})
    except CaseError as exc:
        return _error(exc)


@router.post("/governed-cases/{case_ref}/information-requests/{proposal_sha256}/approve")
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.governed_cases.write", rate_limit="standard",
    idempotency="write-once-approval", audit_event="governed_cases.information_request_approved",
)  # fmt: skip
async def approve_information_request(
    case_ref: str,
    proposal_sha256: str,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    """The human gate: the authenticated user approves one exact proposal digest; the text is rendered then."""
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref, for_update=True)
            requests = list(case.information_requests or [])
            index = next((i for i, r in enumerate(requests) if r.get("proposal_sha256") == proposal_sha256), None)
            if index is None:
                raise CaseError("information_request_not_found", status=404)
            proposal = requests[index]
            if proposal.get("status") != "awaiting_approval":
                raise CaseError("information_request_not_pending")
            try:
                rendered = render(
                    proposal, {"action": "approve", "approver_id": _actor(user), "proposal_sha256": proposal_sha256}
                )
            except InformationRequestError as exc:
                raise CaseError(exc.reason, status=422) from exc
            requests[index] = {
                **proposal, "status": "approved", "request": rendered, "approved_at": datetime.now(UTC).isoformat(),
            }  # fmt: skip
            case.information_requests = requests
            await record_update(session, case, now=runtime.clock())
            result = requests[index]
        runtime.push_kick(uuid.UUID(tenant_id))
        return result
    except CaseError as exc:
        return _error(exc)
