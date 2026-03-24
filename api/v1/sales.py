"""Sales pipeline API — lead management, sales agent triggers, email sequences."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text

from api.deps import get_current_tenant
from core.agents.registry import AgentRegistry
from core.database import get_tenant_session
from core.models.lead_pipeline import EmailSequence, LeadPipeline
from core.models.audit import AuditLog
from core.schemas.messages import (
    HITLPolicy,
    TargetAgent,
    TaskAssignment,
    TaskInput,
    TaskMetadata,
)

logger = structlog.get_logger()
router = APIRouter()


# ── Schemas ──

class LeadCreate(BaseModel):
    name: str
    email: str
    company: str = ""
    role: str = ""
    phone: str = ""
    source: str = "website"
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None


class LeadUpdate(BaseModel):
    stage: str | None = None
    score: int | None = None
    assigned_human: str | None = None
    budget: str | None = None
    authority: str | None = None
    need: str | None = None
    timeline: str | None = None
    deal_value_usd: float | None = None
    lost_reason: str | None = None
    notes: str | None = None
    next_followup_at: str | None = None
    demo_scheduled_at: str | None = None


def _lead_to_dict(lead: LeadPipeline) -> dict:
    return {
        "id": str(lead.id),
        "name": lead.name,
        "email": lead.email,
        "company": lead.company,
        "role": lead.role,
        "phone": lead.phone,
        "source": lead.source,
        "stage": lead.stage,
        "score": lead.score,
        "score_factors": lead.score_factors,
        "assigned_agent_id": str(lead.assigned_agent_id) if lead.assigned_agent_id else None,
        "assigned_human": lead.assigned_human,
        "budget": lead.budget,
        "authority": lead.authority,
        "need": lead.need,
        "timeline": lead.timeline,
        "last_contacted_at": lead.last_contacted_at.isoformat() if lead.last_contacted_at else None,
        "next_followup_at": lead.next_followup_at.isoformat() if lead.next_followup_at else None,
        "followup_count": lead.followup_count,
        "demo_scheduled_at": lead.demo_scheduled_at.isoformat() if lead.demo_scheduled_at else None,
        "trial_started_at": lead.trial_started_at.isoformat() if lead.trial_started_at else None,
        "deal_value_usd": float(lead.deal_value_usd) if lead.deal_value_usd else None,
        "lost_reason": lead.lost_reason,
        "notes": lead.notes,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
    }


# ── GET /sales/pipeline — Pipeline overview with funnel metrics ──

@router.get("/sales/pipeline")
async def get_pipeline(
    stage: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # Funnel counts
        funnel_q = select(
            LeadPipeline.stage, func.count().label("count")
        ).where(LeadPipeline.tenant_id == tid).group_by(LeadPipeline.stage)
        funnel_result = await session.execute(funnel_q)
        funnel = {row.stage: row.count for row in funnel_result}

        # Leads list
        query = select(LeadPipeline).where(LeadPipeline.tenant_id == tid)
        if stage:
            query = query.where(LeadPipeline.stage == stage)
        query = query.order_by(LeadPipeline.score.desc(), LeadPipeline.created_at.desc())
        result = await session.execute(query)
        leads = result.scalars().all()

    return {
        "funnel": funnel,
        "total": sum(funnel.values()),
        "leads": [_lead_to_dict(l) for l in leads],
    }


# ── GET /sales/pipeline/{id} ──

@router.get("/sales/pipeline/{lead_id}")
async def get_lead(lead_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(LeadPipeline).where(LeadPipeline.id == lead_id, LeadPipeline.tenant_id == tid)
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise HTTPException(404, "Lead not found")

        # Get email history
        emails_result = await session.execute(
            select(EmailSequence)
            .where(EmailSequence.lead_id == lead_id)
            .order_by(EmailSequence.step_number)
        )
        emails = emails_result.scalars().all()

    lead_dict = _lead_to_dict(lead)
    lead_dict["emails"] = [
        {
            "id": str(e.id),
            "sequence_name": e.sequence_name,
            "step_number": e.step_number,
            "subject": e.email_subject,
            "status": e.status,
            "sent_at": e.sent_at.isoformat() if e.sent_at else None,
        }
        for e in emails
    ]
    return lead_dict


# ── PATCH /sales/pipeline/{id} ──

@router.patch("/sales/pipeline/{lead_id}")
async def update_lead(
    lead_id: UUID,
    body: LeadUpdate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(LeadPipeline).where(LeadPipeline.id == lead_id, LeadPipeline.tenant_id == tid)
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise HTTPException(404, "Lead not found")

        update_data = body.model_dump(exclude_unset=True)
        for key, val in update_data.items():
            if key == "deal_value_usd" and val is not None:
                from decimal import Decimal
                setattr(lead, key, Decimal(str(val)))
            elif key in ("next_followup_at", "demo_scheduled_at") and val:
                setattr(lead, key, datetime.fromisoformat(val))
            else:
                setattr(lead, key, val)

    return {"id": str(lead_id), "updated": True}


# ── POST /sales/pipeline/process-lead — Trigger sales agent on a lead ──

@router.post("/sales/pipeline/process-lead")
async def process_lead_with_agent(
    payload: dict | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """Run the sales agent against a specific lead for qualification + outreach."""
    if payload is None:
        payload = {}
    tid = _uuid.UUID(tenant_id)
    lead_id = payload.get("lead_id")

    if not lead_id:
        raise HTTPException(400, "lead_id is required")

    # Load lead
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(LeadPipeline).where(LeadPipeline.id == _uuid.UUID(lead_id), LeadPipeline.tenant_id == tid)
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise HTTPException(404, "Lead not found")

        lead_data = _lead_to_dict(lead)

    # Find or use default sales agent
    async with get_tenant_session(tid) as session:
        from core.models.agent import Agent
        result = await session.execute(
            select(Agent).where(
                Agent.tenant_id == tid,
                Agent.agent_type == "sales_agent",
                Agent.status.in_(["active", "shadow"]),
            ).limit(1)
        )
        agent_row = result.scalar_one_or_none()

    if not agent_row:
        raise HTTPException(404, "No sales agent found. Create one via the Agent Creator wizard.")

    agent_config = {
        "id": str(agent_row.id),
        "tenant_id": tenant_id,
        "agent_type": agent_row.agent_type,
        "authorized_tools": agent_row.authorized_tools or [],
        "prompt_variables": agent_row.prompt_variables or {},
        "hitl_condition": agent_row.hitl_condition or "",
        "output_schema": agent_row.output_schema,
        "system_prompt_text": agent_row.system_prompt_text,
    }

    # Ensure agent modules registered
    import core.agents  # noqa: F401

    agent_instance = AgentRegistry.create_from_config(agent_config)

    # Build task
    task_assignment = TaskAssignment(
        message_id=f"msg_{_uuid.uuid4().hex[:12]}",
        correlation_id=f"sales_{_uuid.uuid4().hex[:12]}",
        workflow_run_id=f"sales_pipeline_{_uuid.uuid4().hex[:8]}",
        workflow_definition_id="sales_pipeline",
        step_id="process_lead",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(
            agent_id=agent_config["id"],
            agent_type="sales_agent",
            agent_token="runtime",
        ),
        task=TaskInput(
            action=payload.get("action", "qualify_and_respond"),
            inputs={"lead": lead_data},
            context={
                "sequence_step": payload.get("sequence_step", 0),
                "action_type": payload.get("action", "qualify_and_respond"),
            },
        ),
        hitl_policy=HITLPolicy(
            enabled=True,
            threshold_expression=agent_config.get("hitl_condition", ""),
        ),
        metadata=TaskMetadata(priority="high"),
    )

    try:
        task_result = await agent_instance.execute(task_assignment)
    except Exception as exc:
        logger.error("sales_agent_error", lead_id=lead_id, error=str(exc))
        raise HTTPException(500, f"Sales agent execution failed: {exc}") from exc

    # Process agent output — update lead, send email, post to Slack
    output = task_result.output or {}

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(LeadPipeline).where(LeadPipeline.id == _uuid.UUID(lead_id))
        )
        lead = result.scalar_one_or_none()
        if lead:
            # Update score
            if "lead_score" in output:
                lead.score = output["lead_score"]
            if "qualification" in output:
                lead.score_factors = output["qualification"].get("score_factors", {})
            # Update stage
            if "lead_stage" in output and output["lead_stage"] != lead.stage:
                lead.stage = output["lead_stage"]
            # Update follow-up
            if "next_followup_at" in output and output["next_followup_at"]:
                lead.next_followup_at = datetime.fromisoformat(output["next_followup_at"])
            lead.last_contacted_at = datetime.now(UTC)
            lead.followup_count += 1

            # Record email if agent generated one
            email_data = output.get("email")
            if email_data and email_data.get("to"):
                email_record = EmailSequence(
                    tenant_id=tid,
                    lead_id=lead.id,
                    sequence_name=email_data.get("sequence_name", "initial_outreach"),
                    step_number=email_data.get("step_number", 0),
                    email_subject=email_data.get("subject"),
                    email_body=email_data.get("body_html"),
                    status="pending",
                )
                session.add(email_record)

                # Actually send the email
                try:
                    from core.email import send_email
                    send_email(
                        to=email_data["to"],
                        subject=email_data.get("subject", "AgenticOrg"),
                        html=email_data.get("body_html", ""),
                    )
                    email_record.status = "sent"
                    email_record.sent_at = datetime.now(UTC)
                except Exception as e:
                    logger.warning("sales_email_failed", lead_id=lead_id, error=str(e))

            # Audit log
            audit = AuditLog(
                tenant_id=tid,
                event_type="sales.process_lead",
                actor_type="agent",
                actor_id=agent_config["id"],
                resource_type="lead",
                resource_id=str(lead.id),
                action="process_lead",
                outcome=task_result.status,
                details={
                    "score": output.get("lead_score"),
                    "stage": output.get("lead_stage"),
                    "confidence": task_result.confidence,
                    "action": output.get("action"),
                },
            )
            session.add(audit)

    # Send Slack alert for new leads
    slack_msg = output.get("slack_message")
    if slack_msg:
        try:
            from core.config import external_keys
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {external_keys.slack_bot_token}"},
                    json={
                        "channel": slack_msg.get("channel", "C0AMMN62FBR"),
                        "text": slack_msg.get("text", ""),
                    },
                )
        except Exception as e:
            logger.warning("sales_slack_failed", error=str(e))

    return {
        "task_id": task_result.message_id,
        "lead_id": lead_id,
        "status": task_result.status,
        "output": output,
        "confidence": task_result.confidence,
        "reasoning_trace": task_result.reasoning_trace,
    }


# ── GET /sales/pipeline/due-followups — Leads needing follow-up ──

@router.get("/sales/pipeline/due-followups")
async def get_due_followups(tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(LeadPipeline).where(
                LeadPipeline.tenant_id == tid,
                LeadPipeline.next_followup_at <= datetime.now(UTC),
                LeadPipeline.stage.not_in(["closed_won", "closed_lost"]),
            ).order_by(LeadPipeline.score.desc())
        )
        leads = result.scalars().all()
    return [_lead_to_dict(l) for l in leads]


# ── GET /sales/metrics — Weekly digest data ──

@router.get("/sales/metrics")
async def get_sales_metrics(tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)

    async with get_tenant_session(tid) as session:
        # Total leads
        total = await session.execute(
            select(func.count()).select_from(LeadPipeline).where(LeadPipeline.tenant_id == tid)
        )
        total_leads = total.scalar() or 0

        # This week's leads
        new_q = await session.execute(
            select(func.count()).select_from(LeadPipeline).where(
                LeadPipeline.tenant_id == tid, LeadPipeline.created_at >= week_ago
            )
        )
        new_this_week = new_q.scalar() or 0

        # Funnel
        funnel_q = await session.execute(
            select(LeadPipeline.stage, func.count().label("count"))
            .where(LeadPipeline.tenant_id == tid)
            .group_by(LeadPipeline.stage)
        )
        funnel = {row.stage: row.count for row in funnel_q}

        # Avg score
        avg_q = await session.execute(
            select(func.avg(LeadPipeline.score)).where(
                LeadPipeline.tenant_id == tid, LeadPipeline.score > 0
            )
        )
        avg_score = round(float(avg_q.scalar() or 0), 1)

        # Emails sent this week
        emails_q = await session.execute(
            select(func.count()).select_from(EmailSequence).where(
                EmailSequence.tenant_id == tid,
                EmailSequence.sent_at >= week_ago,
                EmailSequence.status == "sent",
            )
        )
        emails_sent = emails_q.scalar() or 0

        # Stale leads (no contact in 7+ days, not closed)
        stale_q = await session.execute(
            select(func.count()).select_from(LeadPipeline).where(
                LeadPipeline.tenant_id == tid,
                LeadPipeline.last_contacted_at < week_ago,
                LeadPipeline.stage.not_in(["closed_won", "closed_lost", "new"]),
            )
        )
        stale_leads = stale_q.scalar() or 0

    return {
        "total_leads": total_leads,
        "new_this_week": new_this_week,
        "funnel": funnel,
        "avg_score": avg_score,
        "emails_sent_this_week": emails_sent,
        "stale_leads": stale_leads,
    }
