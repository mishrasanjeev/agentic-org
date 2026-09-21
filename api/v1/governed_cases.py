# SPDX-License-Identifier: Apache-2.0
"""Governed business cases: submit, investigate, retrieve, review dispositions and record decisions.

Every route needs an authenticated tenant user and is hidden (404 ``governed_cases_disabled``)
unless the tenant's ``governed_cases.enabled`` flag is on. Scopes use the ``approvals`` family:
reads need ``approvals:read``, writes ``approvals:write``. Identities written into a case (who
submitted it, who reviewed a disposition, who approved an information request) always come from
the authenticated session, never from the request body, and they say what kind of credential
acted: ``user:``, ``api_key:`` or ``agent:`` (:func:`actor_for`).

The actions a person is accountable for - deciding, withdrawing, asking for a decision, reviewing
a screening disposition and approving an information request - additionally need a human session
(:func:`human_actor_for`): an API key or an agent token is refused with 403
``human_session_required``, because RBAC scope families are not applied to agent tokens and an
API key's scopes say nothing about who is behind it.

A decision is recorded only with a verified decision grant; until decision grants are wired in,
``POST /governed-cases/{case_ref}/decision`` answers 403 ``decision_required``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from api.deps import get_current_tenant, get_current_user
from api.route_metadata import route_meta
from core.agents.business_underwriter.information_request import InformationRequestError, propose, render
from core.agents.screening_disposition import DispositionReviewError, DispositionReviewRequest, apply_review
from core.cases import excerpts as case_excerpts
from core.cases.runtime import (
    CaseRuntime,
    announce_case_version,
    decide_case,
    default_policy_id,
    investigate_case,
)
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


#: The only authentication mode that carries a person: a user session verified by
#: ``auth.grantex_middleware``. Anything else - including a request whose mode was never set -
#: is a machine, so a middleware that forgets to name its mode fails closed.
HUMAN_AUTH_MODES: frozenset[str] = frozenset({"legacy"})


def _machine_actor(request: Request, claims: dict[str, Any]) -> str | None:
    """The caller's label when it is not a human session, else ``None``.

    Agent tokens and API keys are named as what they are, so nothing a machine did is ever
    recorded on a case as if a person had done it.
    """
    auth_mode = getattr(request.state, "auth_mode", None)
    subject = str(claims.get("sub") or "")
    agent_id = str(claims.get("agenticorg:agent_id") or claims.get("grantex:agent_id") or "")
    if auth_mode == "grantex" or claims.get("grantex:grant_id"):
        return f"agent:{agent_id or subject or 'unknown'}"[:256]
    if auth_mode == "api_key" or subject.startswith("apikey:"):
        return f"api_key:{subject.removeprefix('apikey:') or 'unknown'}"[:256]
    if auth_mode not in HUMAN_AUTH_MODES:
        return f"machine:{auth_mode or 'unset'}"[:256]
    if agent_id:
        # A token minted for an agent, not a person: no token a person signs in with carries an
        # agent id.
        return f"agent:{agent_id}"[:256]
    return None


def actor_for(request: Request) -> str:
    """Who is acting, derived from the session alone."""
    claims = get_current_user(request)
    machine = _machine_actor(request, claims)
    if machine is not None:
        return machine
    user_id = claims.get("agenticorg:user_id")
    if user_id:
        return f"user:{user_id}"[:256]
    subject = str(claims.get("sub") or "")
    if not subject:
        raise CaseError("actor_unknown", status=401)
    return f"user:{subject}"[:256]


def human_actor_for(request: Request) -> str:
    """Who is acting, refusing anything that is not a human session (fails closed)."""
    claims = get_current_user(request)
    machine = _machine_actor(request, claims)
    if machine is not None:
        logger.warning("governed_case_human_action_refused", actor=machine)
        raise CaseError("human_session_required", "this action is reserved for a signed-in person", status=403)
    return actor_for(request)


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
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    from core.config import settings

    try:
        actor = actor_for(request)
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
                created_by=actor,
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
                # What the run actually fetched, so a citation can be checked against it, and the
                # excerpts the case holds by reference (the passages have their own route).
                "tool_calls": _tool_calls(case),
                "excerpts": [case_excerpts.reference(excerpt) for excerpt in case.excerpts_encrypted or []],
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


def _tool_calls(case: Any) -> list[dict[str, Any]]:
    """Every provider call the case's agent runs made: the tool, its outcome and the records it returned."""
    calls: list[dict[str, Any]] = []
    for record in case.agent_records or []:
        agent = str(record.get("agent") or "")
        run_id = str(record.get("run_id") or "")
        for call in record.get("tool_calls") or []:
            calls.append(
                {
                    "agent": agent,
                    "run_id": run_id,
                    "provider": call.get("provider"),
                    "tool": call.get("tool"),
                    "outcome": call.get("outcome"),
                    "reason": call.get("reason"),
                    "started_at": call.get("started_at"),
                    "record_ids": call.get("record_ids") or [],
                    "output_sha256": call.get("output_sha256"),
                }
            )
    return calls


@router.get("/governed-cases/{case_ref}/excerpts/{excerpt_ref}")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.governed_cases.read", rate_limit="standard")
async def get_case_excerpt(
    case_ref: str,
    excerpt_ref: str,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    """The passage behind one citation, decrypted and re-hashed before it is returned.

    Untrusted provider content: returned as data for a human to read, never interpreted here and
    never near a prompt. The console prints the digest beside the passage, so the passage has to
    match it: one that does not is refused (``excerpt_integrity_failed``), not shown with a digest
    that would make it look verified.
    """
    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            entry = next((e for e in case.excerpts_encrypted or [] if str(e.get("excerpt_ref")) == excerpt_ref), None)
        if entry is None:
            raise CaseError("excerpt_not_found", status=404)
        try:
            text = case_excerpts.read(entry)
        except case_excerpts.ExcerptError as exc:
            status = 404 if exc.reason == "excerpt_not_held" else 409
            raise CaseError(exc.reason, exc.detail, status=status) from exc
        return {**case_excerpts.reference(entry), "text": text, "verified": True}
    except CaseError as exc:
        return _error(exc)


@router.delete("/governed-cases/{case_ref}/excerpts")
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.governed_cases.write", rate_limit="standard",
    idempotency="clearing-twice-changes-nothing", audit_event="governed_cases.excerpts_forgotten",
)  # fmt: skip
async def forget_case_excerpts(
    case_ref: str,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    """Drop the stored passages, keeping what the memo cites.

    Data minimisation: the passages are provider records about people. Forgetting them leaves the
    references, digests and the memo untouched - the case still says what it cited - and the
    console then says the passage is no longer held.
    """
    try:
        actor = human_actor_for(request)
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref, for_update=True)
            held = sum(1 for e in case.excerpts_encrypted or [] if case_excerpts.CIPHERTEXT_KEY in e)
            case.excerpts_encrypted = case_excerpts.forget(case.excerpts_encrypted)
            await record_update(session, case, now=runtime.clock())
            version, had_requests = case.version, bool(case.decision_requests)
        await announce_case_version(runtime, case_ref, version, only_if=had_requests)
        logger.info("case_excerpts_forgotten", case_ref=case_ref, actor=actor, forgotten=held)
        runtime.push_kick(uuid.UUID(tenant_id))
        return {"case_ref": case_ref, "forgotten": held}
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
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        actor = actor_for(request)
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            if CaseState.IN_PROGRESS not in {CaseState(s) for s in _next_states(case.state)}:
                raise CaseError("transition_not_allowed", f"{case.state} -> in_progress")
        background.add_task(_investigate_in_background, tenant_id, case_ref, runtime, actor)
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
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        actor = human_actor_for(request)
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref, for_update=True)
            await transition(session, case, CaseState.WITHDRAWN, actor=actor, reason="withdrawn", now=runtime.clock())
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


async def _record_authoritative_dwell(runtime: CaseRuntime, request_id: str, case_ref: str) -> None:
    """Record the dwell the approval page measured, once, after the decision is recorded.

    Telemetry, after the fact: the decision is already recorded and the grants already consumed, so
    an issuer that cannot answer here costs a data point and nothing else. It is never allowed to
    turn a recorded decision into an error.
    """
    from core.cases.decision_requests import record_decision_dwell

    try:
        service = _decision_service(runtime)
        record_decision_dwell(await service.get_request(request_id), case_ref)
    # enterprise-gate: broad-except-ok reason=post-decision-telemetry-never-fails-a-recorded-decision
    except Exception as exc:
        # The decision is already recorded and the grants already consumed. Anything that happens
        # here - a refusal, a timeout, a response shape nobody expected - costs a data point. A
        # narrower except would let the next unexpected error turn a decision that succeeded into
        # a 500 for the analyst who took it.
        logger.warning(
            "case_decision_dwell_unavailable",
            case_ref=case_ref,
            reason=getattr(exc, "reason", "") or type(exc).__name__,
        )


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
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    """Ask a named person to decide this case on the issuer's own approval page.

    The console never collects the approval: the answer carries the approval page's URL, and the
    approver signs in there, steps up, reads the memo and the policy score and approves. This route
    only creates the request and records it on the case.
    """
    from core.cases.decision_requests import (
        DecisionServiceError,
        case_action,
        decision_requests_total,
        four_eyes_on,
        policy_score_for_approval,
        render_memo_for_approval,
    )

    try:
        actor = human_actor_for(request)
        await runtime.require_enabled(uuid.UUID(tenant_id))
        service = _decision_service(runtime)
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            if case.state != CaseState.AWAITING_DECISION:
                raise CaseError("transition_not_allowed", f"a decision needs awaiting_decision, case is {case.state}")
            if not case.memo or not case.policy_result:
                raise CaseError("memo_not_ready", "the case has no memo and policy result to approve against")
            action = case_action(case, body.outcome)
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
            # The issuer returns the approval page only when it creates the request, so the case's
            # own record is the source here. Stated explicitly: this must not depend on key order.
            "approval_page": view.approval_page or str(record.get("approval_page") or ""),
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
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    from core.cases.decision_requests import DecisionServiceError

    try:
        actor = human_actor_for(request)
        grants = list(body.decision_grants)
        if body.decision_request_id:
            service = _decision_service(runtime)
            async with _session(tenant_id) as session:
                case = await get_case(session, tenant_id, case_ref)
                record = _stored_request(case, body.decision_request_id)
            if record.get("outcome") != body.outcome:
                raise CaseError("decision_outcome_mismatch", "the request was made for another outcome", status=409)
            if str(record.get("case_version")) != str(case.version):
                # The case moved on since the request. The issuer has superseded it and revoked
                # any unused grants; say which it is rather than "no usable grants yet".
                raise CaseError(
                    "case_changed", f"the request was made for version {record.get('case_version')}", status=409
                )
            try:
                # The grants stay on the server: a decision grant never reaches the browser.
                grants = await service.grants(body.decision_request_id)
            except DecisionServiceError as exc:
                raise CaseError(exc.reason, exc.detail, status=exc.status) from exc
            if not grants:
                raise CaseError("decision_not_approved", "the decision request has no usable grants yet", status=409)
        result = await decide_case(
            tenant_id, case_ref, runtime=runtime, actor=actor, outcome=body.outcome, grants=grants,
        )  # fmt: skip
        _record_console_dwell("record", body.client_dwell_ms, case_ref)
        if body.decision_request_id:
            await _record_authoritative_dwell(runtime, body.decision_request_id, case_ref)
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
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        analyst = human_actor_for(request)
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref, for_update=True)
            if case.state != CaseState.AWAITING_DECISION:
                raise CaseError("transition_not_allowed", f"a review needs awaiting_decision, case is {case.state}")
            dispositions = list(case.screening_dispositions or [])
            index = next((i for i, d in enumerate(dispositions) if d.get("hit_id") == hit_id), None)
            if index is None:
                raise CaseError("disposition_not_found", status=404)
            try:
                dispositions[index] = apply_review(
                    dispositions[index], body, analyst_id=analyst, reviewed_at=runtime.clock()
                )
            except DispositionReviewError as exc:
                raise CaseError(exc.reason, status=409 if exc.reason == "already_reviewed" else 422) from exc
            case.screening_dispositions = dispositions
            await record_update(session, case, now=runtime.clock())
            result = dispositions[index]
            version, had_requests = case.version, bool(case.decision_requests)
        await announce_case_version(runtime, case_ref, version, only_if=had_requests)
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
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    """The human gate: a signed-in person approves one exact proposal digest; the text is rendered then."""
    try:
        approver = human_actor_for(request)
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref, for_update=True)
            if case.state != CaseState.AWAITING_DECISION:
                raise CaseError("transition_not_allowed", f"an approval needs awaiting_decision, case is {case.state}")
            requests = list(case.information_requests or [])
            index = next((i for i, r in enumerate(requests) if r.get("proposal_sha256") == proposal_sha256), None)
            if index is None:
                raise CaseError("information_request_not_found", status=404)
            proposal = requests[index]
            if proposal.get("status") != "awaiting_approval":
                raise CaseError("information_request_not_pending")
            try:
                rendered = render(
                    proposal, {"action": "approve", "approver_id": approver, "proposal_sha256": proposal_sha256}
                )
            except InformationRequestError as exc:
                raise CaseError(exc.reason, status=422) from exc
            requests[index] = {
                **proposal, "status": "approved", "request": rendered, "approved_at": datetime.now(UTC).isoformat(),
            }  # fmt: skip
            case.information_requests = requests
            await record_update(session, case, now=runtime.clock())
            result = requests[index]
            version, had_requests = case.version, bool(case.decision_requests)
        await announce_case_version(runtime, case_ref, version, only_if=had_requests)
        runtime.push_kick(uuid.UUID(tenant_id))
        return result
    except CaseError as exc:
        return _error(exc)
