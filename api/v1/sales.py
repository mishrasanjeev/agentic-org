"""Sales pipeline API — lead management, sales agent triggers, email sequences, auto-outreach."""

from __future__ import annotations

import csv
import io
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.deps import get_current_tenant
from core.agents.registry import AgentRegistry
from core.database import get_tenant_session
from core.models.audit import AuditLog
from core.models.lead_pipeline import EmailSequence, LeadPipeline
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
    name: str = Field(..., max_length=255)
    email: str = Field(..., max_length=255)
    company: str = ""
    role: str = ""
    phone: str = ""
    source: str = "website"
    deal_value_usd: float | None = None
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


# ── POST /sales/pipeline/leads — Create a new lead ──

@router.post("/sales/pipeline/leads", status_code=201)
async def create_lead(body: LeadCreate, tenant_id: str = Depends(get_current_tenant)):
    """Create a new lead in the sales pipeline."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        lead = LeadPipeline(
            tenant_id=tid,
            name=body.name.strip(),
            email=body.email.strip().lower(),
            company=body.company.strip() if body.company else "",
            role=body.role.strip() if body.role else "",
            phone=body.phone.strip() if body.phone else "",
            source=body.source or "manual",
            stage="new",
            score=0,
            deal_value_usd=body.deal_value_usd,
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
    return _lead_to_dict(lead)


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
        "leads": [_lead_to_dict(lead) for lead in leads],
    }


# ── Static /sales/pipeline/* routes BEFORE {lead_id} to avoid FastAPI conflict ──

@router.get("/sales/pipeline/due-followups")
async def get_due_followups(tenant_id: str = Depends(get_current_tenant)):
    """Leads needing follow-up (next_followup_at <= now)."""
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
    return [_lead_to_dict(lead) for lead in leads]


@router.post("/sales/pipeline/process-lead")
async def process_lead_with_agent(
    payload: dict | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """Run the sales agent against a specific lead for qualification + outreach."""
    if payload is None:
        payload = {}
    lead_id = payload.get("lead_id")
    if not lead_id:
        raise HTTPException(400, "lead_id is required")

    result = await _run_sales_agent_on_lead(
        tenant_id=tenant_id,
        lead_id=lead_id,
        action=payload.get("action", "qualify_and_respond"),
        sequence_step=payload.get("sequence_step", 0),
    )
    if "error" in result:
        logger.error("sales_agent_error", lead_id=str(lead_id), error=result["error"])
        raise HTTPException(400, "Sales agent processing failed")
    # Return only safe fields — never forward raw agent internals
    return {
        "status": str(result.get("status", "unknown")),
        "lead_id": str(lead_id),
        "confidence": result.get("confidence"),
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

async def _run_sales_agent_on_lead(
    tenant_id: str, lead_id: str,
    action: str = "qualify_and_respond", sequence_step: int = 0,
) -> dict:
    """Core logic: run sales agent on a lead. Called from API endpoint and demo request trigger."""
    tid = _uuid.UUID(tenant_id)

    if not lead_id:
        return {"error": "lead_id is required"}

    # Load lead
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(LeadPipeline).where(LeadPipeline.id == _uuid.UUID(lead_id), LeadPipeline.tenant_id == tid)
        )
        lead = result.scalar_one_or_none()
        if not lead:
            return {"error": "Lead not found"}
        lead_data = _lead_to_dict(lead)

    # Find sales agent
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
        return {"error": "No sales agent found"}

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

    import core.agents  # noqa: F401
    agent_instance = AgentRegistry.create_from_config(agent_config)

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
            action=action,
            inputs={"lead": lead_data},
            context={"sequence_step": sequence_step, "action_type": action},
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
        return {"error": str(exc)}

    output = task_result.output or {}

    # Update lead, send email, audit, slack
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(LeadPipeline).where(LeadPipeline.id == _uuid.UUID(lead_id))
        )
        lead = result.scalar_one_or_none()
        if lead:
            if "lead_score" in output:
                lead.score = output["lead_score"]
            if "qualification" in output:
                lead.score_factors = output["qualification"].get("score_factors", {})
            if "lead_stage" in output and output["lead_stage"] != lead.stage:
                lead.stage = output["lead_stage"]
            if "next_followup_at" in output and output["next_followup_at"]:
                lead.next_followup_at = datetime.fromisoformat(output["next_followup_at"])
            lead.last_contacted_at = datetime.now(UTC)
            lead.followup_count += 1  # type: ignore[operator]

            email_data = output.get("email")
            if email_data and email_data.get("to"):
                email_record = EmailSequence(
                    tenant_id=tid, lead_id=lead.id,
                    sequence_name=email_data.get("sequence_name", "initial_outreach"),
                    step_number=email_data.get("step_number", 0),
                    email_subject=email_data.get("subject"),
                    email_body=email_data.get("body_html"),
                    status="pending",
                )
                session.add(email_record)
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

            audit = AuditLog(
                tenant_id=tid, event_type="sales.process_lead", actor_type="agent",
                actor_id=agent_config["id"], resource_type="lead", resource_id=str(lead.id),
                action="process_lead", outcome=task_result.status,
                details={
                    "score": output.get("lead_score"),
                    "stage": output.get("lead_stage"),
                    "confidence": task_result.confidence,
                },
            )
            session.add(audit)

    # Slack alert
    slack_msg = output.get("slack_message")
    if slack_msg:
        try:
            import httpx

            from core.config import external_keys
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {external_keys.slack_bot_token}"},
                    json={"channel": slack_msg.get("channel", "C0AMMN62FBR"), "text": slack_msg.get("text", "")},
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


# ═══════════════════════════════════════════════════════════════════════════
# CSV IMPORT — Bulk lead upload + auto-trigger sales agent on each
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/sales/import-csv")
async def import_leads_csv(
    file: UploadFile,
    auto_process: bool = True,
    tenant_id: str = Depends(get_current_tenant),
):
    """Import leads from CSV. Expected columns: name, email, company, role, phone (optional).

    If auto_process=true, triggers sales agent on each new lead after import.
    """
    tid = _uuid.UUID(tenant_id)
    content = await file.read()
    text_content = content.decode("utf-8-sig")  # Handle BOM
    reader = csv.DictReader(io.StringIO(text_content))

    imported = []
    skipped = []

    async with get_tenant_session(tid) as session:
        for row in reader:
            email = (row.get("email") or row.get("Email") or "").strip()
            name = (row.get("name") or row.get("Name") or row.get("full_name") or "").strip()
            if not email or not name:
                skipped.append({"reason": "missing name or email", "row": dict(row)})
                continue

            # Check duplicate
            exists = await session.execute(
                select(LeadPipeline.id).where(
                    LeadPipeline.tenant_id == tid, LeadPipeline.email == email
                )
            )
            if exists.scalar_one_or_none():
                skipped.append({"reason": "duplicate email", "email": email})
                continue

            lead = LeadPipeline(
                tenant_id=tid,
                name=name,
                email=email,
                company=(row.get("company") or row.get("Company") or "").strip(),
                role=(row.get("role") or row.get("Role") or row.get("title") or row.get("Title") or "").strip(),
                phone=(row.get("phone") or row.get("Phone") or "").strip(),
                source="csv_import",
                stage="new",
                score=0,
            )
            session.add(lead)
            await session.flush()
            imported.append({"id": str(lead.id), "name": name, "email": email})

    # Auto-trigger sales agent on each imported lead
    processed = 0
    if auto_process and imported:
        for lead_info in imported:
            try:
                # Call process_lead internally
                await process_lead_with_agent(
                    payload={"lead_id": lead_info["id"], "action": "qualify_and_respond"},
                    tenant_id=tenant_id,
                )
                processed += 1
            except Exception as e:
                logger.warning("csv_import_process_failed", lead_id=lead_info["id"], error=str(e))

    return {
        "imported": len(imported),
        "skipped": len(skipped),
        "processed_by_agent": processed,
        "leads": imported,
        "skip_details": skipped[:10],  # Show first 10 skip reasons
    }


# ═══════════════════════════════════════════════════════════════════════════
# AUTOMATED FOLLOW-UP ENGINE — Process all leads needing follow-up
# ═══════════════════════════════════════════════════════════════════════════

# Follow-up schedule: step → days after first contact
FOLLOWUP_SCHEDULE = {
    0: 0,   # Instant response (already sent)
    1: 1,   # Day 1: Value add
    2: 3,   # Day 3: Social proof
    3: 7,   # Day 7: Direct ask
    4: 14,  # Day 14: Breakup
}


@router.post("/sales/run-followups")
async def run_automated_followups(
    tenant_id: str = Depends(get_current_tenant),
):
    """Process all leads that need follow-up based on their sequence step and timing.

    Call this daily via CronJob or Cloud Scheduler.
    Returns summary of actions taken.
    """
    tid = _uuid.UUID(tenant_id)
    now = datetime.now(UTC)
    results: dict[str, Any] = {"processed": 0, "emailed": 0, "skipped": 0, "errors": 0, "details": []}

    async with get_tenant_session(tid) as session:
        # Get all active leads (not closed, not brand new with no contact)
        active_leads = await session.execute(
            select(LeadPipeline).where(
                LeadPipeline.tenant_id == tid,
                LeadPipeline.stage.not_in(["closed_won", "closed_lost"]),
            ).order_by(LeadPipeline.score.desc())
        )
        leads = active_leads.scalars().all()

    for lead in leads:
        try:
            # Determine next sequence step
            current_step = int(lead.followup_count)
            if current_step > 4:
                # Sequence complete — skip
                results["skipped"] += 1
                continue

            if current_step == 0 and lead.stage == "new":
                # Never contacted — send initial outreach
                pass  # Process below
            elif lead.last_contacted_at:
                # Check if enough time has passed for next step
                days_since_contact = (now - lead.last_contacted_at).days
                required_days = FOLLOWUP_SCHEDULE.get(current_step, 999)
                prev_days = FOLLOWUP_SCHEDULE.get(current_step - 1, 0) if current_step > 0 else 0
                gap_needed = required_days - prev_days

                if days_since_contact < gap_needed:
                    results["skipped"] += 1
                    continue
            else:
                results["skipped"] += 1
                continue

            # Trigger sales agent for this lead
            try:
                resp = await process_lead_with_agent(
                    payload={
                        "lead_id": str(lead.id),
                        "action": "followup",
                        "sequence_step": current_step,
                    },
                    tenant_id=str(tid),
                )
                results["processed"] += 1
                if resp.get("output", {}).get("email"):
                    results["emailed"] += 1
                results["details"].append({
                    "lead": lead.name,
                    "step": current_step,
                    "status": resp.get("status"),
                })
            except Exception as e:
                results["errors"] += 1
                logger.warning("followup_failed", lead=lead.name, error=str(e))

        except Exception as e:
            results["errors"] += 1
            logger.warning("followup_error", lead_id=str(lead.id), error=str(e))

    return results


# ═══════════════════════════════════════════════════════════════════════════
# SEED TARGET LEADS — Pre-built list of Indian enterprise prospects
# ═══════════════════════════════════════════════════════════════════════════

TARGET_PROSPECTS = [
    {"name": "Amit Sharma", "email": "amit.sharma@infosys.com", "company": "Infosys", "role": "VP Finance"},
    {"name": "Priya Mehta", "email": "priya.mehta@wipro.com", "company": "Wipro", "role": "CFO"},
    {"name": "Rajesh Iyer", "email": "rajesh.iyer@hcl.com", "company": "HCL Technologies", "role": "Head Operations"},
    {"name": "Neha Gupta", "email": "neha.gupta@zoho.com", "company": "Zoho Corporation", "role": "VP HR"},
    {"name": "Vikram Patel", "email": "vikram.patel@freshworks.com", "company": "Freshworks", "role": "CFO"},
    {"name": "Sunita Reddy", "email": "sunita.reddy@razorpay.com", "company": "Razorpay", "role": "Head Finance"},
    {"name": "Karthik Nair", "email": "karthik.nair@swiggy.com", "company": "Swiggy", "role": "VP Operations"},
    {"name": "Anjali Singh", "email": "anjali.singh@zerodha.com", "company": "Zerodha", "role": "COO"},
    {"name": "Rohit Bansal", "email": "rohit.bansal@snapdeal.com", "company": "Snapdeal", "role": "CFO"},
    {"name": "Meera Joshi", "email": "meera.joshi@nykaa.com", "company": "Nykaa", "role": "VP Finance"},
    {"name": "Arjun Kapoor", "email": "arjun.kapoor@policybazaar.com", "company": "PolicyBazaar", "role": "Head HR"},
    {"name": "Divya Krishnan", "email": "divya.krishnan@bharatpe.com", "company": "BharatPe", "role": "CFO"},
    {"name": "Sanjay Mittal", "email": "sanjay.mittal@delhivery.com", "company": "Delhivery", "role": "COO"},
    {"name": "Pooja Agarwal", "email": "pooja.agarwal@cred.club", "company": "CRED", "role": "VP Finance"},
    {"name": "Rahul Verma", "email": "rahul.verma@zomato.com", "company": "Zomato", "role": "Head Operations"},
    {"name": "Anita Deshmukh", "email": "anita.deshmukh@tatasteel.com", "company": "Tata Steel", "role": "VP Finance"},
    {"name": "Suresh Kumar", "email": "suresh.kumar@mahindra.com", "company": "Mahindra Group", "role": "CFO"},
    {"name": "Kavita Rao", "email": "kavita.rao@biocon.com", "company": "Biocon", "role": "Head HR"},
    {"name": "Vivek Pandey", "email": "vivek.pandey@dream11.com", "company": "Dream11", "role": "COO"},
    {"name": "Nandini Bhat", "email": "nandini.bhat@flipkart.com", "company": "Flipkart", "role": "VP Operations"},
]


@router.post("/sales/seed-prospects")
async def seed_target_prospects(
    auto_process: bool = False,
    tenant_id: str = Depends(get_current_tenant),
):
    """Seed 20 pre-built Indian enterprise prospects into pipeline.
    Set auto_process=true to immediately trigger sales agent on each.
    """
    tid = _uuid.UUID(tenant_id)
    imported = []
    skipped = []

    async with get_tenant_session(tid) as session:
        for prospect in TARGET_PROSPECTS:
            exists = await session.execute(
                select(LeadPipeline.id).where(
                    LeadPipeline.tenant_id == tid, LeadPipeline.email == prospect["email"]
                )
            )
            if exists.scalar_one_or_none():
                skipped.append(prospect["email"])
                continue

            lead = LeadPipeline(
                tenant_id=tid,
                name=prospect["name"],
                email=prospect["email"],
                company=prospect["company"],
                role=prospect["role"],
                source="target_list",
                stage="new",
                score=0,
            )
            session.add(lead)
            await session.flush()
            imported.append({"id": str(lead.id), "name": prospect["name"], "company": prospect["company"]})

    # Auto-process if requested
    processed = 0
    if auto_process:
        for lead_info in imported:
            try:
                await process_lead_with_agent(
                    payload={"lead_id": lead_info["id"]},
                    tenant_id=tenant_id,
                )
                processed += 1
            except Exception as e:
                logger.warning("seed_process_failed", lead=lead_info["name"], error=str(e))

    return {
        "seeded": len(imported),
        "skipped_duplicates": len(skipped),
        "processed_by_agent": processed,
        "leads": imported,
    }


# ═══════════════════════════════════════════════════════════════════════════
# INBOX MONITOR — Read replies, match to leads, auto-respond
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


@router.post("/sales/process-inbox")
async def process_inbox(
    tenant_id: str = Depends(get_current_tenant),
):
    """Read recent inbox replies, match to pipeline leads, trigger sales agent for response.

    Call this via CronJob every 5-10 minutes.
    """
    tid = _uuid.UUID(tenant_id)
    results: dict[str, Any] = {"replies_found": 0, "matched": 0, "responded": 0, "errors": 0, "details": []}

    try:
        from core.gmail_agent import get_recent_replies, mark_as_read, send_reply
    except ImportError:
        logger.error("gmail_agent_import_failed")
        return {"error": "Gmail agent module not available"}

    # Get recent replies (last 6 hours)
    try:
        replies = get_recent_replies(since_hours=6)
    except Exception:
        logger.exception("inbox_fetch_failed")
        return {"error": "Failed to fetch inbox"}

    results["replies_found"] = len(replies)

    for reply in replies:
        try:
            # Match reply to a lead in pipeline
            async with get_tenant_session(tid) as session:
                lead_result = await session.execute(
                    select(LeadPipeline).where(
                        LeadPipeline.tenant_id == tid,
                        LeadPipeline.email == reply["from_email"],
                        LeadPipeline.stage.not_in(["closed_won", "closed_lost"]),
                    )
                )
                lead = lead_result.scalar_one_or_none()

            if not lead:
                # Unknown sender — create a new lead
                async with get_tenant_session(tid) as session:
                    lead = LeadPipeline(
                        tenant_id=tid,
                        name=reply["from_name"],
                        email=reply["from_email"],
                        source="email_reply",
                        stage="new",
                        score=0,
                    )
                    session.add(lead)
                    await session.flush()
                    lead_id = str(lead.id)
                logger.info("new_lead_from_reply", email=reply["from_email"], lead_id=lead_id)
            else:
                lead_id = str(lead.id)

                # Update lead stage — they replied, so they're interested
                async with get_tenant_session(tid) as session:
                    result = await session.execute(
                        select(LeadPipeline).where(LeadPipeline.id == lead.id)
                    )
                    db_lead = result.scalar_one_or_none()
                    if db_lead and db_lead.stage in ("new", "contacted"):
                        db_lead.stage = "qualified"
                        logger.info("lead_qualified_by_reply", email=reply["from_email"])

            results["matched"] += 1

            # Get email history for context
            async with get_tenant_session(tid) as session:
                email_history = await session.execute(
                    select(EmailSequence).where(
                        EmailSequence.lead_id == _uuid.UUID(lead_id)
                    ).order_by(EmailSequence.step_number)
                )
                prev_emails = [
                    {
                        "subject": e.email_subject,
                        "body": e.email_body[:500] if e.email_body else "",
                        "step": e.step_number,
                    }
                    for e in email_history.scalars().all()
                ]

            # Trigger sales agent with reply context
            agent_result = await _run_sales_agent_on_lead(
                tenant_id=str(tid),
                lead_id=lead_id,
                action="respond_to_reply",
                sequence_step=len(prev_emails),
            )

            # Send the response via Gmail API (in-thread)
            agent_output = agent_result.get("output", {}) or {}
            agent_email = agent_output.get("email", {}) or {}
            response_body = agent_email.get("body_html", "")

            if response_body and agent_result.get("status") == "completed":
                try:
                    sent_id = send_reply(
                        thread_id=reply["thread_id"],
                        to=reply["from_email"],
                        subject=reply["subject"],
                        html_body=response_body,
                    )
                    mark_as_read(reply["message_id"])

                    # Record the sent reply
                    async with get_tenant_session(tid) as session:
                        email_record = EmailSequence(
                            tenant_id=tid,
                            lead_id=_uuid.UUID(lead_id),
                            sequence_name="reply",
                            step_number=len(prev_emails) + 1,  # type: ignore[operator]
                            email_subject=f"Re: {reply['subject']}",
                            email_body=response_body,
                            status="sent",
                            sent_at=datetime.now(UTC),
                        )
                        session.add(email_record)

                    results["responded"] += 1
                    results["details"].append({
                        "from": reply["from_email"],
                        "subject": reply["subject"],
                        "action": "responded",
                        "gmail_id": sent_id,
                    })
                except Exception as e:
                    logger.warning("reply_send_failed", error=str(e))
                    results["errors"] += 1
            else:
                results["details"].append({
                    "from": reply["from_email"],
                    "subject": reply["subject"],
                    "action": "agent_no_response",
                    "agent_status": agent_result.get("status"),
                })

        except Exception as e:
            results["errors"] += 1
            logger.warning("inbox_process_error", email=reply.get("from_email"), error=str(e))

    return results
