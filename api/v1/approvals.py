"""HITL approval endpoints."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func, select

from api.deps import get_current_tenant, get_current_user, get_user_domains, get_user_role
from api.route_metadata import route_meta
from core.approvals.agent_run_resume import public_context, resume_approved_agent_run, should_resume
from core.database import get_tenant_session
from core.models.agent import Agent
from core.models.audit import AuditLog
from core.models.hitl import HITLQueue
from core.ownership import (
    approval_visibility_clause,
    caller_from_request,
    can_view_agent,
    is_personal_agent,
    personal_approval_decision,
)
from core.schemas.api import HITLDecision, PaginatedResponse

router = APIRouter()
_log = structlog.get_logger()

# RBAC role hierarchy — higher number = more authority.
# A user can decide on HITL items where their level >= the assignee_role's level.
_ROLE_HIERARCHY: dict[str, int] = {
    "staff": 10,
    "manager": 20,
    "auditor": 25,
    "cfo": 30,
    "chro": 30,
    "cmo": 30,
    "coo": 30,
    "cbo": 30,
    "ceo": 50,
    "admin": 100,  # admin can VIEW all but DECIDE only on assigned (see decide endpoint)
    # Roles provisioned by core/rbac.py (invite / SSO defaults) that the
    # original map omitted (QA sheet 2026-09-14 #25/#33). They resolved to
    # level 0, so every decision by these users failed with "unknown role".
    # analyst/developer hold approvals:read only and are still stopped by
    # scope enforcement; the level exists so the denial reason is honest.
    "merchant": 30,
    "domain_lead": 30,
    "analyst": 10,
    "developer": 10,
}


def _role_level(role: str) -> int:
    return _ROLE_HIERARCHY.get((role or "").lower(), 0)


def _can_decide(
    user_role: str,
    user_domains: list[str] | None,
    assignee_role: str,
    agent_domain: str | None,
) -> tuple[bool, str]:
    """Check if user can decide on a HITL item.

    Rules:
      - admin can DECIDE only on items where role matches (not blanket override)
      - For other roles: user role level must be >= assignee_role level
      - Domain match required if user has domain restriction
    """
    if not user_role:
        return False, "user has no role"
    if not assignee_role:
        return False, "HITL item has no assignee_role"

    user_lvl = _role_level(user_role)
    required_lvl = _role_level(assignee_role)

    if user_lvl == 0:
        return False, f"unknown role '{user_role}'"

    # P3.2: admin must still match the assignee role to DECIDE (not just by being admin)
    if user_role.lower() == "admin":
        # Admin can decide if they share the same level/domain as assignee
        if user_lvl < required_lvl:
            return False, f"admin level {user_lvl} insufficient for {assignee_role} ({required_lvl})"
    elif user_lvl < required_lvl:
        return False, f"role '{user_role}' (level {user_lvl}) cannot approve '{assignee_role}' (level {required_lvl})"

    # Domain check (if user has domain restriction)
    if user_domains is not None and agent_domain and agent_domain not in user_domains:
        return False, f"user not authorized for domain '{agent_domain}'"

    return True, ""


def _effective_status(item: HITLQueue, now: datetime | None = None) -> str:
    """Report a pending item whose deadline passed as ``expired``.

    The Celery ``timeout_workflow_hitl`` task flips the row durably; until it
    fires (or if it could not be queued) the list must not show the item as
    still decidable, and ``/decide`` already rejects it with 410.
    """
    if item.status == "pending" and item.expires_at and (now or datetime.now(UTC)) > item.expires_at:
        return "expired"
    return item.status


def _hitl_to_dict(item: HITLQueue) -> dict:
    return {
        "id": str(item.id),
        "workflow_run_id": str(item.workflow_run_id) if item.workflow_run_id else None,
        "agent_id": str(item.agent_id),
        "title": item.title,
        "trigger_type": item.trigger_type,
        "priority": item.priority,
        "status": _effective_status(item),
        "assignee_role": item.assignee_role,
        "decision_options": item.decision_options,
        # Without the server-only parameters kept for resuming a paused run.
        "context": public_context(item.context),
        "decision": item.decision,
        "decision_by": str(item.decision_by) if item.decision_by else None,
        "requested_by_user_id": (
            str(item.requested_by_user_id) if getattr(item, "requested_by_user_id", None) else None
        ),
        "decision_at": item.decision_at.isoformat() if item.decision_at else None,
        "decision_notes": item.decision_notes,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


# ── GET /approvals ───────────────────────────────────────────────────────────
@router.get("/approvals", response_model=PaginatedResponse)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="approvals.sensitive.list",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="approvals.list",
)
async def list_approvals(
    request: Request,
    domain: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    include_expired: bool = False,
    page: int = 1,
    per_page: int = 20,
    tenant_id: str = Depends(get_current_tenant),
):
    """List HITL items.

    Default: pending, undecided, not past their deadline. ``status=expired``
    lists items the timeout task closed; ``include_expired=true`` adds both
    those and pending items whose ``expires_at`` already passed (reported
    with ``status: "expired"``) so a timed-out approval never vanishes.
    """
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 100)
    tid = _uuid.UUID(tenant_id)
    caller = caller_from_request(request)
    async with get_tenant_session(tid) as session:
        base = select(HITLQueue).where(HITLQueue.tenant_id == tid)
        count_base = select(func.count()).select_from(HITLQueue).where(HITLQueue.tenant_id == tid)

        # RBAC domain + ownership filtering via Agent subquery (bug sheet
        # 2026-09-14 row 30): shared-agent items keep the domain filter,
        # personal-agent items reach only their owner. Admins see all.
        if not caller.is_admin:
            visible_agent_ids = (
                select(Agent.id)
                .where(Agent.tenant_id == tid, approval_visibility_clause(Agent, caller))
                .scalar_subquery()
            )
            base = base.where(HITLQueue.agent_id.in_(visible_agent_ids))
            count_base = count_base.where(HITLQueue.agent_id.in_(visible_agent_ids))

        if priority:
            base = base.where(HITLQueue.priority == priority)
            count_base = count_base.where(HITLQueue.priority == priority)
        if status:
            base = base.where(HITLQueue.status == status)
            count_base = count_base.where(HITLQueue.status == status)
        elif include_expired:
            base = base.where(HITLQueue.status.in_(("pending", "expired")))
            count_base = count_base.where(HITLQueue.status.in_(("pending", "expired")))
        else:
            # Default: show only pending items
            base = base.where(HITLQueue.status == "pending")
            count_base = count_base.where(HITLQueue.status == "pending")

        # Exclude deadline-passed items from the decidable pending queue
        if (status == "pending" or not status) and not include_expired:
            now = datetime.now(UTC)
            base = base.where(
                (HITLQueue.expires_at.is_(None)) | (HITLQueue.expires_at > now)
            )
            count_base = count_base.where(
                (HITLQueue.expires_at.is_(None)) | (HITLQueue.expires_at > now)
            )

        total = (await session.execute(count_base)).scalar() or 0

        query = (
            base.order_by(HITLQueue.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        )
        result = await session.execute(query)
        items = result.scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedResponse(
        items=[_hitl_to_dict(i) for i in items],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ── Background workflow resume after HITL decision ─────────────────────────


async def _resume_workflow_bg(
    tenant_id: _uuid.UUID,
    workflow_run_id: _uuid.UUID,
    decision: dict,
    engine_run_id_hint: str | None = None,
) -> None:
    """Resume a workflow after HITL decision and sync remaining results to DB."""
    from core.models.workflow import WorkflowDefinition, WorkflowRun
    from workflows.engine import WorkflowEngine
    from workflows.run_sync import sync_engine_state_to_workflow_run
    from workflows.state_store import WorkflowStateStore

    # Load engine_run_id and workflow definition
    async with get_tenant_session(tenant_id) as session:
        db_run = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
            )
        ).scalar_one_or_none()
        if not db_run:
            return
        engine_run_id = engine_run_id_hint or (db_run.context or {}).get("_engine_run_id")
        wf_def = (
            await session.execute(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.id == db_run.workflow_def_id
                )
            )
        ).scalar_one_or_none()
        definition = wf_def.definition if wf_def else None

    if not engine_run_id or not definition:
        _log.error(
            "workflow_resume_missing_engine_context",
            run_id=str(workflow_run_id),
            has_engine_run_id=bool(engine_run_id),
            has_definition=bool(definition),
        )
        async with get_tenant_session(tenant_id) as session:
            db_run = (
                await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
                )
            ).scalar_one_or_none()
            if db_run is not None:
                db_run.status = "failed"
                db_run.error = {
                    "message": "Resume failed: workflow engine context is missing.",
                    "code": "workflow_resume_context_missing",
                }
                db_run.completed_at = datetime.now(UTC)
        return

    state_store = WorkflowStateStore()
    await state_store.init()
    engine = WorkflowEngine(state_store)

    try:
        # Resume executes all remaining steps (or pauses at next HITL)
        resume_result = await engine.resume_from_hitl(engine_run_id, decision)
        if isinstance(resume_result, dict) and resume_result.get("error"):
            raise RuntimeError(str(resume_result["error"]))

        state = await state_store.load(engine_run_id)
        if not state:
            async with get_tenant_session(tenant_id) as session:
                db_run = (
                    await session.execute(
                        select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
                    )
                ).scalar_one_or_none()
                if db_run is not None:
                    db_run.status = "failed"
                    db_run.error = {
                        "message": "Resume failed: workflow engine state is missing.",
                        "code": "workflow_resume_state_missing",
                    }
                    db_run.completed_at = datetime.now(UTC)
            return

        await sync_engine_state_to_workflow_run(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            engine_run_id=engine_run_id,
            state=state,
            definition=definition,
        )

    # enterprise-gate: broad-except-ok reason=approval-resume-background-marks-workflow-failed
    except Exception as exc:
        _log.error("workflow_resume_failed", run_id=str(workflow_run_id), error=str(exc))
        try:
            async with get_tenant_session(tenant_id) as session:
                db_run = (
                    await session.execute(
                        select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
                    )
                ).scalar_one()
                db_run.status = "failed"
                db_run.error = {"message": f"Resume failed: {exc}"}
                db_run.completed_at = datetime.now(UTC)
        # enterprise-gate: broad-except-ok reason=approval-resume-error-handler-failure-is-logged
        except Exception as inner:
            _log.error("workflow_resume_error_handler_failed", error=str(inner))
    finally:
        await state_store.close()


def _voter_identities(claims: dict) -> set[str]:
    """Every identifier a session (or a recorded vote) carries for one person, normalised."""
    values = [claims.get("agenticorg:user_id"), claims.get("sub"), claims.get("email")]
    recorded = claims.get("identities")
    if isinstance(recorded, list):
        values.extend(recorded)
    return {str(v).strip().lower() for v in values if isinstance(v, str) and v.strip()}


# ── POST /approvals/{id}/decide ─────────────────────────────────────────────
@router.post("/approvals/{hitl_id}/decide")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="approvals.decide",
    rate_limit="approval-decision",
    idempotency="terminal-state-conflict-prevents-duplicate-decision",
    audit_event="approvals.decide",
)
async def decide(
    hitl_id: UUID,
    body: HITLDecision,
    background_tasks: BackgroundTasks,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    user_claims: dict = Depends(get_current_user),
    user_role: str = Depends(get_user_role),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    tid = _uuid.UUID(tenant_id)
    learning_result: dict = {}

    # P2.1: capture decision_by from authenticated user (always required)
    # Prefer the canonical local user UUID. OIDC ``sub`` is commonly an email
    # or provider-specific opaque string and cannot populate decision_by's UUID
    # foreign key, which previously left authenticated decisions unattributed.
    user_id_str = user_claims.get("agenticorg:user_id") or user_claims.get("sub") or ""
    user_name = user_claims.get("name") or user_claims.get("email") or "unknown"
    if not user_id_str:
        raise HTTPException(401, "Cannot identify user — missing 'sub' claim")
    # Password/Google/SSO tokens all carry ``agenticorg:user_id`` (User.id);
    # a bare ``sub`` is an email and can never be a UUID.
    try:
        user_uuid: _uuid.UUID | None = _uuid.UUID(user_id_str)
    except (ValueError, TypeError):
        user_uuid = None

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(HITLQueue)
            .where(HITLQueue.id == hitl_id, HITLQueue.tenant_id == tid)
            .with_for_update()
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(404, "HITL item not found")

        # P1.1 + P3.2: Resolve the agent for the ownership and RBAC checks.
        agent = (
            await session.execute(select(Agent).where(Agent.id == item.agent_id, Agent.tenant_id == tid))
        ).scalar_one_or_none()
        agent_domain = getattr(agent, "domain", None)

        # Bug sheet 2026-09-14 row 30: a personal agent's items belong to its
        # owner (and admins). A caller who cannot even see the agent gets the
        # same 404 as a missing item, before any status is revealed.
        caller = caller_from_request(request)
        ownership_verdict = personal_approval_decision(agent, caller)
        if ownership_verdict is False and not can_view_agent(agent, caller):
            raise HTTPException(404, "HITL item not found")
        if item.status != "pending":
            raise HTTPException(409, f"HITL item already resolved with status '{item.status}'")

        # Check expiry
        if item.expires_at and datetime.now(UTC) > item.expires_at:
            raise HTTPException(410, "HITL item has expired")

        # Validate assignee_role exists (P2.1 — never allow approval without role)
        if not item.assignee_role:
            raise HTTPException(
                422,
                "HITL item has no assignee_role — cannot validate authorization",
            )

        if ownership_verdict is True:
            # Owner (or admin) of a personal agent: ownership replaces the
            # role hierarchy, which is written for shared domain agents.
            allowed, reason = True, ""
        elif ownership_verdict is False:
            allowed = False
            reason = (
                "only the agent's owner or a tenant admin can decide approvals for a personal agent"
                if is_personal_agent(agent)
                else f"role '{user_role}' may only decide approvals for its own personal agents"
            )
        else:
            # P1.1: Enforce role hierarchy and domain match
            allowed, reason = _can_decide(user_role, user_domains, item.assignee_role, agent_domain)

        # ── Delegation override ────────────────────────────────────────
        # If the direct check fails, look for an active delegation FROM
        # someone whose role *would* allow this decision TO the current
        # user. If we find one, the user acts on behalf of the delegator.
        delegated_from: str | None = None
        if not allowed and ownership_verdict is None:
            try:
                from datetime import UTC as _UTC
                from datetime import datetime as _dt

                from core.models.delegation import UserDelegation
                from core.models.user import User as UserModel

                if user_uuid is not None:
                    now = _dt.now(_UTC)
                    deleg_rows = await session.execute(
                        select(UserDelegation, UserModel.role)
                        .join(UserModel, UserModel.id == UserDelegation.delegator_id)
                        .where(
                            UserDelegation.tenant_id == tid,
                            UserDelegation.delegate_id == user_uuid,
                            UserDelegation.revoked_at.is_(None),
                            UserDelegation.starts_at <= now,
                        )
                    )
                    for delegation, delegator_role in deleg_rows.all():
                        ends_ok = delegation.ends_at is None or delegation.ends_at > now
                        if not ends_ok:
                            continue
                        d_allowed, _ = _can_decide(
                            delegator_role, user_domains, item.assignee_role, agent_domain
                        )
                        if d_allowed:
                            allowed = True
                            delegated_from = str(delegation.delegator_id)
                            reason = f"acting on behalf of {delegated_from} (role={delegator_role})"
                            break
            # enterprise-gate: broad-except-ok reason=delegation-lookup-failure-keeps-decision-denied
            except Exception:
                _log.debug("delegation_check_failed", hitl_id=str(hitl_id))

        if not allowed:
            _log.warning(
                "hitl_decide_denied",
                hitl_id=str(hitl_id),
                user_id=user_id_str,
                user_role=user_role,
                assignee_role=item.assignee_role,
                reason=reason,
            )
            raise HTTPException(403, f"Cannot decide on this approval: {reason}")

        # Apply decision with full attribution
        if user_uuid is not None:
            item.decision_by = user_uuid
        else:
            # Non-UUID sub claim — store None but log
            _log.warning("hitl_decide_non_uuid_user", user_id=user_id_str)

        # ── Multi-step approval policy resolution ──────────────────────
        #
        # If there's an ApprovalPolicy attached to this agent or workflow,
        # consult the policy engine before short-circuiting to "decided".
        # The engine tells us whether the current step has reached quorum
        # (advance), is still collecting (collect), or was rejected.
        #
        # Per-item state lives in item.context["policy_state"]:
        #   {
        #     "policy_id": "...",
        #     "current_sequence": 1,
        #     "approvals_collected": 0,
        #     "approvals": [{"user_id": ..., "decision": ..., "at": ...}, ...]
        #   }
        from core.approvals import (
            apply_decision,
            first_applicable_step,
            next_step_after,
            resolve_policy,
        )

        ctx = dict(item.context or {})
        if item.workflow_run_id is None and ctx.get("workflow_run_id"):
            try:
                item.workflow_run_id = _uuid.UUID(str(ctx["workflow_run_id"]))
            except (TypeError, ValueError):
                _log.warning(
                    "hitl_context_workflow_run_id_invalid",
                    hitl_id=str(hitl_id),
                    workflow_run_id=ctx.get("workflow_run_id"),
                )
        policy_state = dict(ctx.get("policy_state") or {})
        policy_action = "advance"  # default — the legacy single-step path

        policy = await resolve_policy(
            tenant_id=tid,
            workflow_id=item.workflow_run_id,
            agent_id=item.agent_id,
        )

        # An item part-way through a policy stays bound to that policy. If it was
        # deleted, or another one now resolves, deciding on this vote would apply
        # no policy (or the wrong one) to approvals already collected.
        in_flight_policy = str(policy_state.get("policy_id") or "")
        if in_flight_policy and (policy is None or str(policy.id) != in_flight_policy):
            _log.warning(
                "hitl_policy_changed_in_flight",
                hitl_id=str(hitl_id),
                policy_id=in_flight_policy,
                resolved_policy_id=str(policy.id) if policy is not None else None,
            )
            raise HTTPException(
                409,
                "The approval policy for this item changed while it was in progress",
            )

        if policy is not None:
            # Hydrate the engine's view of the current step
            current_seq = int(policy_state.get("current_sequence") or 0)
            approvals_collected = int(policy_state.get("approvals_collected") or 0)
            approvals_history = list(policy_state.get("approvals") or [])

            if current_seq == 0:
                step = await first_applicable_step(policy, ctx)
            else:
                # Re-fetch the current step by sequence
                from sqlalchemy import select as _select

                from core.models.approval_policy import ApprovalStep

                step_res = await session.execute(
                    _select(ApprovalStep).where(
                        ApprovalStep.policy_id == policy.id,
                        ApprovalStep.sequence == current_seq,
                    )
                )
                step = step_res.scalar_one_or_none()
                if step is None:
                    # The policy changed mid-approval. Deciding on one vote here
                    # would apply no policy at all; leave the item for an admin.
                    _log.warning(
                        "hitl_policy_step_missing",
                        hitl_id=str(hitl_id),
                        policy_id=str(policy.id),
                        sequence=current_seq,
                    )
                    raise HTTPException(
                        409,
                        "The approval policy changed while this item was in progress; "
                        "its current step no longer exists",
                    )

            if step is not None:
                # One vote per person per item, across every step: the per-step
                # count resets when the item advances, so a per-step check let
                # one reviewer satisfy each step of a multi-person policy in turn.
                # A person is matched on every identifier their session carries -
                # an invite-acceptance session has only the email, a login
                # session the user id as well - so they cannot vote once as each.
                voter = _voter_identities(user_claims)
                duplicate_vote = any(
                    voter & _voter_identities(
                        {"agenticorg:user_id": vote.get("user_id"), "identities": vote.get("identities")}
                    )
                    for vote in approvals_history
                    if isinstance(vote, dict)
                )
                if duplicate_vote:
                    raise HTTPException(
                        409,
                        "This reviewer has already voted on this approval",
                    )
                pdec = apply_decision(step, approvals_collected, body.decision or "approve")
                policy_action = pdec.action
                approvals_history.append(
                    {
                        "user_id": user_id_str,
                        "identities": sorted(voter),
                        "decision": body.decision,
                        "sequence": step.sequence,
                        "at": datetime.now(UTC).isoformat(),
                    }
                )
                policy_state = {
                    "policy_id": str(policy.id),
                    "current_sequence": step.sequence,
                    "approvals_collected": pdec.current_step_approvals,
                    "approvals": approvals_history,
                    "last_action": pdec.action,
                    "last_reason": pdec.reason,
                }

                if pdec.action == "advance":
                    next_step = await next_step_after(policy, step.sequence, ctx)
                    if next_step is not None:
                        # Move to the next step — keep the item open with
                        # the new assignee_role and reset the counter.
                        policy_state["current_sequence"] = next_step.sequence
                        policy_state["approvals_collected"] = 0
                        item.assignee_role = next_step.approver_role
                        policy_action = "collect"  # treat as still in flight
                # collect / reject fall through to the writes below

            ctx["policy_state"] = policy_state
            item.context = ctx

        if policy_action == "collect":
            # Quorum not met yet OR moved to next step — persist the
            # vote in the policy_state but leave the item open.
            item.decision_notes = body.notes if body.notes else None
            workflow_run_id = None
        elif policy_action == "reject":
            item.decision = body.decision
            item.decision_notes = body.notes if body.notes else None
            item.decision_at = datetime.now(UTC)
            item.status = "rejected"
            workflow_run_id = item.workflow_run_id
        else:
            # advance with no next step → fully decided (legacy behaviour)
            item.decision = body.decision
            item.decision_notes = body.notes if body.notes else None
            item.decision_at = datetime.now(UTC)
            item.status = "decided"
            workflow_run_id = item.workflow_run_id

        # Capture every human action in the same transaction as the approval.
        # Terminal shadow decisions also recalibrate the promotion confidence.
        from core.feedback.shadow_learning import capture_hitl_feedback

        learning_result = await capture_hitl_feedback(
            session,
            item=item,
            decision=body.decision or "defer",
            notes=body.notes or "",
            actor_id=user_id_str,
            actor_role=user_role,
            actor_name=user_name,
            policy_action=policy_action,
            policy_state=policy_state if policy is not None else None,
            delegated_from=delegated_from,
        )

        # Audit log entry — captures who approved/rejected what
        audit = AuditLog(
            tenant_id=tid,
            event_type="hitl.decided",
            actor_type="user",
            actor_id=user_id_str,
            agent_id=item.agent_id,
            action=body.decision or "decide",
            outcome="success",
            resource_type="hitl_item",
            resource_id=str(hitl_id),
            details={
                "decision": body.decision,
                "notes": body.notes or "",
                "user_name": user_name,
                "user_role": user_role,
                "assignee_role": item.assignee_role,
                "agent_domain": agent_domain,
                "policy_action": policy_action,
                "policy_state": policy_state if policy is not None else None,
                "delegated_from": delegated_from,
                "feedback_learning": learning_result,
            },
        )
        session.add(audit)

        _log.info(
            "hitl_decided",
            hitl_id=str(hitl_id),
            user_id=user_id_str,
            user_role=user_role,
            decision=body.decision,
            policy_action=policy_action,
        )

    # Resume workflow execution in background
    if workflow_run_id:
        engine_run_id_hint = (
            ctx.get("_engine_run_id")
            or ctx.get("engine_run_id")
            or ctx.get("workflow_engine_run_id")
        )
        background_tasks.add_task(
            _resume_workflow_bg,
            tid,
            workflow_run_id,
            {"decision": body.decision, "notes": body.notes},
            str(engine_run_id_hint or "") or None,
        )

    # Resume a paused standalone run from its checkpoint (flag
    # approvals.resume_agent_runs, default off). The task re-reads the row in
    # the tenant session; nothing from this request identifies the checkpoint.
    if policy_action in {"advance", "reject"} and await should_resume(item, tid):
        background_tasks.add_task(resume_approved_agent_run, tid, item.id)

    if learning_result.get("ran_in_shadow") and policy_action in {"advance", "reject"}:
        from core.feedback.analyzer import analyze_and_apply_feedback

        background_tasks.add_task(
            analyze_and_apply_feedback,
            str(item.agent_id),
            tenant_id,
        )

    return {
        "hitl_id": str(hitl_id),
        "decision": body.decision,
        "status": item.status,
        "decided_by": user_id_str,
        "decided_at": item.decision_at.isoformat() if item.decision_at else None,
        "policy_action": policy_action,
        "policy_state": policy_state if policy_state else None,
        "feedback_learning": learning_result,
    }
