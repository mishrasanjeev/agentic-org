# SPDX-License-Identifier: Apache-2.0
"""Resume a standalone agent run when its approval is decided (PRD F-2).

Behind the per-tenant feature flag ``approvals.resume_agent_runs`` (off unless
a flag row enables it). When an approval with a stored checkpoint thread is
decided, ``api/v1/approvals.py::decide`` schedules
``resume_approved_agent_run``, which:

1. re-reads the approval inside the tenant's RLS session and claims it
   (``context.checkpoint_resume.state = "resuming"``), refusing when the row is
   missing, belongs to a workflow, has no thread, holds a thread outside the
   tenant, was already resumed, or carries a decision other than approve or
   reject;
2. rebuilds the graph from the parameters the run endpoint recorded at pause
   time (``context._checkpoint_resume``: confidence floor, HITL condition,
   tools, model) so the approval gate evaluates exactly as it did when it
   paused;
3. resumes through ``core.langgraph.runner.resume_agent`` with
   ``require_paused=True`` (a thread with no checkpoint at the gate is refused,
   never started afresh);
4. records the outcome on the approval and in the audit log, counts it in
   ``agenticorg_agent_run_resumes_total{outcome}``, and deletes the finished
   thread's checkpoints.

No step takes a thread id, tenant or run parameter from a request. Connector
credentials are not loaded: a run resumed at the approval gate ends there, and
anything that tried to call a tool would find no credentials.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from prometheus_client import Counter
from sqlalchemy import select

from core.database import get_tenant_session
from core.langgraph.thread_ids import thread_belongs_to_tenant
from core.models.agent import Agent
from core.models.audit import AuditLog
from core.models.hitl import HITLQueue

logger = structlog.get_logger()

RESUME_FLAG = "approvals.resume_agent_runs"
# Written by POST /agents/{id}/run when a run pauses; never returned by the API.
RESUME_SPEC_KEY = "_checkpoint_resume"
# Outcome of the resume, shown with the approval.
RESUME_STATE_KEY = "checkpoint_resume"
AUDIT_EVENT = "agent.run.resumed"

OUTCOME_COMPLETED = "completed"
OUTCOME_REJECTED = "rejected"
OUTCOME_REFUSED = "refused"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"

agent_run_resumes_total = Counter(
    "agenticorg_agent_run_resumes_total",
    "Standalone agent runs resumed after an approval decision, by outcome",
    ["outcome"],
)


def resume_command(status: str | None, decision: str | None, notes: str | None) -> dict[str, Any] | None:
    """The ``Command(resume=...)`` payload for a decided approval, or ``None`` if it is not resumable."""
    choice = (decision or "").strip().lower()
    if status == "rejected" or (status == "decided" and choice in {"reject", "rejected"}):
        return {"action": "reject", "reason": notes or ""}
    if status == "decided" and choice in {"approve", "approved"}:
        return {"action": "approve"}
    return None


async def should_resume(item: HITLQueue, tenant_id: uuid.UUID) -> bool:
    """Whether ``decide`` should schedule a resume for this approval."""
    if item.workflow_run_id is not None or not getattr(item, "checkpoint_thread_id", None):
        return False
    if resume_command(item.status, item.decision, item.decision_notes) is None:
        return False
    from core.feature_flags import FeatureFlagLookupError, load_flag_rows_strict, row_enabled

    # An operator-managed authority flag: the global and tenant rows are read
    # separately. A global row that disables resuming wins; otherwise the
    # tenant row decides, else the global row. An unreadable flag store keeps
    # the run paused.
    reason = "resume_flag_off"
    try:
        rows = await load_flag_rows_strict(RESUME_FLAG, tenant_id=tenant_id)
    except FeatureFlagLookupError:
        reason = "resume_flag_unavailable"
    else:
        subject = str(tenant_id)
        if rows.global_row is not None and not row_enabled(RESUME_FLAG, rows.global_row, subject_id=subject):
            reason = "resume_flag_disabled_globally"
        elif row_enabled(
            RESUME_FLAG, rows.tenant_row if rows.tenant_row is not None else rows.global_row, subject_id=subject
        ):
            return True
    # The run stays paused. Say so, since nothing else will.
    agent_run_resumes_total.labels(outcome=OUTCOME_SKIPPED).inc()
    logger.warning(
        "agent_run_resume_skipped",
        hitl_id=str(item.id),
        reason=reason,
        flag=RESUME_FLAG,
    )
    return False


def public_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """The approval context without the server-only resume parameters."""
    if not isinstance(context, dict) or RESUME_SPEC_KEY not in context:
        return context
    return {key: value for key, value in context.items() if key != RESUME_SPEC_KEY}


@dataclass
class _Claim:
    refusal: str = ""
    agent_id: uuid.UUID | None = None
    thread_id: str = ""
    command: dict[str, Any] = field(default_factory=dict)
    spec: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _claim(tenant_id: uuid.UUID, hitl_id: uuid.UUID) -> _Claim:
    async with get_tenant_session(tenant_id) as session:
        item = (
            await session.execute(
                select(HITLQueue).where(HITLQueue.id == hitl_id, HITLQueue.tenant_id == tenant_id).with_for_update()
            )
        ).scalar_one_or_none()
        if item is None:
            return _Claim(refusal="approval_not_found")

        context = dict(item.context or {})
        previous = context.get(RESUME_STATE_KEY)
        thread_id = item.checkpoint_thread_id or ""
        command = resume_command(item.status, item.decision, item.decision_notes)
        spec = context.get(RESUME_SPEC_KEY)
        agent = None
        if isinstance(previous, dict) and previous.get("state"):
            refusal = "already_resumed"
        elif item.workflow_run_id is not None or not thread_id:
            refusal = "approval_not_resumable"
        elif not thread_belongs_to_tenant(thread_id, tenant_id):
            refusal = "checkpoint_thread_tenant_mismatch"
        elif command is None:
            refusal = "decision_not_resumable"
        elif not isinstance(spec, dict):
            refusal = "resume_spec_missing"
        else:
            agent = (
                await session.execute(select(Agent).where(Agent.id == item.agent_id, Agent.tenant_id == tenant_id))
            ).scalar_one_or_none()
            refusal = "" if agent is not None else "agent_not_found"

        if refusal or agent is None or command is None or not isinstance(spec, dict):
            return _Claim(refusal=refusal or "approval_not_resumable", agent_id=item.agent_id)
        # Claimed in the same locked transaction: a second resume sees the state.
        context[RESUME_STATE_KEY] = {"state": "resuming", "started_at": _now()}
        item.context = context
        return _Claim(
            agent_id=item.agent_id,
            thread_id=thread_id,
            command=command,
            spec=spec,
            system_prompt=str(agent.system_prompt_text or ""),
        )


def _classify(command: dict[str, Any], result: dict[str, Any]) -> tuple[str, str]:
    status = result.get("status")
    reason = str(result.get("reason") or "")
    if reason:
        # checkpoint_* codes: the run was not resumed (refused before or while
        # reading the checkpoint); anything else failed during the resume.
        return (OUTCOME_REFUSED if reason.startswith("checkpoint_") else OUTCOME_FAILED), reason
    if command["action"] == "reject":
        if status == "failed" and str(result.get("error", "")).startswith("Rejected by human"):
            return OUTCOME_REJECTED, ""
        # The gate did not apply the rejection: never report the run as done.
        return OUTCOME_FAILED, "rejection_not_applied"
    if status == "completed":
        return OUTCOME_COMPLETED, ""
    return OUTCOME_FAILED, "run_failed"


async def _delete_thread(thread_id: str) -> bool:
    from core.langgraph.checkpointer import get_checkpointer

    try:
        saver = await get_checkpointer()
        await saver.adelete_thread(thread_id)
        return True
    # enterprise-gate: broad-except-ok reason=checkpoint-cleanup-failure-degrades-to-the-retention-runbook-sweep
    except Exception as exc:
        logger.warning("agent_run_resume_checkpoint_cleanup_failed", error_type=type(exc).__name__)
        return False


async def _record(
    tenant_id: uuid.UUID,
    hitl_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    outcome: str,
    reason: str,
    run_status: str | None,
    checkpoint_deleted: bool,
) -> None:
    async with get_tenant_session(tenant_id) as session:
        item = (
            await session.execute(
                select(HITLQueue).where(HITLQueue.id == hitl_id, HITLQueue.tenant_id == tenant_id).with_for_update()
            )
        ).scalar_one_or_none()
        if item is not None:
            context = dict(item.context or {})
            state = dict(context.get(RESUME_STATE_KEY) or {})
            state.update(
                {
                    "state": outcome,
                    "reason": reason,
                    "run_status": run_status,
                    "checkpoint_deleted": checkpoint_deleted,
                    "finished_at": _now(),
                }
            )
            context[RESUME_STATE_KEY] = state
            item.context = context
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                event_type=AUDIT_EVENT,
                actor_type="system",
                actor_id="approval_resume",
                agent_id=agent_id,
                action="resume",
                outcome=outcome,
                resource_type="hitl_item",
                resource_id=str(hitl_id),
                details={"reason": reason, "run_status": run_status, "checkpoint_deleted": checkpoint_deleted},
            )
        )


def _count(outcome: str, reason: str, hitl_id: uuid.UUID) -> None:
    agent_run_resumes_total.labels(outcome=outcome).inc()
    log = logger.info if outcome in {OUTCOME_COMPLETED, OUTCOME_REJECTED} else logger.warning
    log("agent_run_resume_finished", hitl_id=str(hitl_id), outcome=outcome, reason=reason)


async def resume_approved_agent_run(tenant_id: uuid.UUID, hitl_id: uuid.UUID) -> dict[str, str]:
    """Resume the run paused behind approval ``hitl_id`` of ``tenant_id``. Returns ``{outcome, reason}``."""
    from core.langgraph import runner
    from core.langgraph.checkpointer import CheckpointerUnavailableError

    claim = await _claim(tenant_id, hitl_id)
    if claim.refusal:
        _count(OUTCOME_REFUSED, claim.refusal, hitl_id)
        if claim.refusal not in {"approval_not_found", "already_resumed"}:
            await _record(tenant_id, hitl_id, claim.agent_id, OUTCOME_REFUSED, claim.refusal, None, False)
        return {"outcome": OUTCOME_REFUSED, "reason": claim.refusal}

    spec = claim.spec
    try:
        result = await runner.resume_agent(
            agent_id=str(claim.agent_id),
            thread_id=claim.thread_id,
            decision=claim.command,
            system_prompt=claim.system_prompt,
            authorized_tools=list(spec.get("authorized_tools") or []),
            llm_model=str(spec.get("llm_model") or ""),
            confidence_floor=float(spec["confidence_floor"]),
            hitl_condition=str(spec.get("hitl_condition") or ""),
            connector_config={},
            connector_names=spec.get("connector_names"),
            tenant_id=str(tenant_id),
            company_id=spec.get("company_id"),
            domain=spec.get("domain"),
            llm_provider=spec.get("llm_provider"),
            require_paused=True,
        )
    except CheckpointerUnavailableError as exc:
        result = {"status": "failed", "error": str(exc), "reason": exc.reason}
    # enterprise-gate: broad-except-ok reason=resume-failure-is-recorded-on-the-approval-and-audited
    except Exception as exc:
        logger.error("agent_run_resume_error", hitl_id=str(hitl_id), error_type=type(exc).__name__)
        result = {"status": "failed", "error": type(exc).__name__, "reason": "resume_failed"}

    outcome, reason = _classify(claim.command, result)
    deleted = await _delete_thread(claim.thread_id) if outcome in {OUTCOME_COMPLETED, OUTCOME_REJECTED} else False
    await _record(tenant_id, hitl_id, claim.agent_id, outcome, reason, result.get("status"), deleted)
    _count(outcome, reason, hitl_id)
    return {"outcome": outcome, "reason": reason}
