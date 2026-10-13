# SPDX-License-Identifier: Apache-2.0
"""Tenant-bound marketing outbox; SMTP acceptance is not inbox delivery."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from uuid import UUID, uuid4

from sqlalchemy import bindparam, column, func, table, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from core.crypto.tenant_secrets import decrypt_for_tenant, encrypt_with_kek, resolve_tenant_kek
from core.database import get_tenant_session

logger = logging.getLogger(__name__)
MARKETING_TENANT = UUID("00000000-0000-0000-0000-000000000001")
_STEPS = ("notification", "confirmation", "agent")
_JOBS = table("demo_delivery_jobs",
              column("id", PG_UUID), column("tenant_id", PG_UUID), column("lead_id", PG_UUID),
              column("payload_encrypted"), column("notification_status"), column("confirmation_status"),
              column("agent_status"), column("updated_at"), column("error_codes", JSONB))


def enabled() -> bool:
    return os.getenv("AGENTICORG_DEMO_DURABLE_DELIVERY_ENABLED", "false").lower() in {"1", "true", "yes"}


async def prepare(payload: dict, lead_id: str | None) -> UUID:
    job_id = uuid4()
    kek = await resolve_tenant_kek(MARKETING_TENANT)
    encrypted = await asyncio.to_thread(encrypt_with_kek, json.dumps(payload), kek)
    async with get_tenant_session(MARKETING_TENANT) as session:
        await session.execute(text("""
            INSERT INTO demo_delivery_jobs(id,tenant_id,lead_id,payload_encrypted)
            VALUES (:id,:tenant,:lead,:payload)
        """), {"id": job_id, "tenant": MARKETING_TENANT,
                "lead": UUID(lead_id) if lead_id else None, "payload": encrypted})
    return job_id


async def enqueue(job_id: UUID) -> bool:
    from core.tasks.demo_tasks import dispatch

    try:
        await asyncio.to_thread(dispatch.apply_async, args=[str(MARKETING_TENANT), str(job_id)], queue="delivery")
    # enterprise-gate: broad-except-ok reason=broker-failure-leaves-durable-pending-job-for-sweep
    except Exception as exc:
        logger.warning("Demo delivery enqueue failed (%s)", type(exc).__name__)
        return False
    return True


async def _claim(tenant: UUID, job_id: UUID, step: str) -> dict | None:
    if step not in _STEPS:
        raise ValueError("Unknown delivery step")
    async with get_tenant_session(tenant) as session:
        state = _JOBS.c[f"{step}_status"]
        statement = (update(_JOBS).where(_JOBS.c.id == job_id, _JOBS.c.tenant_id == tenant, state == "pending")
                     .values({state: "processing", _JOBS.c.updated_at: func.now()})
                     .returning(_JOBS.c.payload_encrypted, _JOBS.c.lead_id))
        row = (await session.execute(statement)).mappings().first()
        return dict(row) if row else None


async def _finish(tenant: UUID, job_id: UUID, step: str, status: str, error: str | None = None) -> None:
    if step not in _STEPS:
        raise ValueError("Unknown delivery step")
    async with get_tenant_session(tenant) as session:
        state = _JOBS.c[f"{step}_status"]
        statement = (update(_JOBS).where(_JOBS.c.id == job_id, _JOBS.c.tenant_id == tenant, state == "processing")
                     .values({state: status, _JOBS.c.updated_at: func.now(),
                              _JOBS.c.error_codes: _JOBS.c.error_codes.op("||")(bindparam("errors", type_=JSONB))}))
        await session.execute(statement, {"errors": {step: error}})
    logger.info("demo_delivery_step job=%s step=%s status=%s", job_id, step, status)


async def dispatch_one(tenant: UUID, job_id: UUID) -> dict[str, str]:
    if tenant != MARKETING_TENANT:
        raise ValueError("Marketing delivery requires the server-owned tenant")
    from api.v1.demo import DemoRequest, _send_email_notification, _send_trial_confirmation
    from api.v1.sales import _run_sales_agent_on_lead

    outcomes = {}
    for step in _STEPS:
        row = await _claim(tenant, job_id, step)
        if row is None:
            continue
        status = "failed"
        error = "processing_failed"
        try:
            payload = await asyncio.to_thread(decrypt_for_tenant, row["payload_encrypted"])
            body = DemoRequest.model_validate_json(payload)
            if step == "agent":
                if not row["lead_id"]:
                    status, error = "skipped", "lead_unavailable"
                else:
                    result = await _run_sales_agent_on_lead(str(tenant), str(row["lead_id"]))
                    result_status = result.get("status")
                    status = ("completed" if result_status == "completed" else
                              "needs_review" if result_status == "hitl_triggered" else "failed")
                    error = None if status in {"completed", "needs_review"} else "sales_processing_failed"
            else:
                send = _send_email_notification if step == "notification" else _send_trial_confirmation
                accepted = await asyncio.to_thread(send, body)
                status = "smtp_accepted" if accepted is True else "failed"
                error = None if accepted is True else "email_not_accepted"
        # enterprise-gate: broad-except-ok reason=outbox-step-failure-is-persisted-without-retrying-external-action
        except Exception as exc:
            logger.warning("Demo delivery step failed (%s)", type(exc).__name__)
        await _finish(tenant, job_id, step, status, error)
        outcomes[step] = status
    return outcomes


async def sweep() -> int:
    if not enabled():
        return 0
    async with get_tenant_session(MARKETING_TENANT) as session:
        # A lost process may have sent before its status commit. Never resend
        # an ambiguous SMTP action automatically: keep an operator-visible state.
        await session.execute(text("""
            UPDATE demo_delivery_jobs SET
                notification_status=CASE WHEN notification_status='processing'
                    THEN 'unknown' ELSE notification_status END,
                confirmation_status=CASE WHEN confirmation_status='processing'
                    THEN 'unknown' ELSE confirmation_status END,
                agent_status=CASE WHEN agent_status='processing' THEN 'unknown' ELSE agent_status END
            WHERE tenant_id=:tenant AND updated_at < now()-INTERVAL '15 minutes'
                AND 'processing' IN (notification_status,confirmation_status,agent_status)
        """), {"tenant": MARKETING_TENANT})
        ids = (await session.execute(text("""
            SELECT id FROM demo_delivery_jobs WHERE tenant_id=:tenant
                AND 'pending' IN (notification_status,confirmation_status,agent_status)
            ORDER BY created_at LIMIT 50
        """), {"tenant": MARKETING_TENANT})).scalars().all()
    count = 0
    for job_id in ids:
        count += int(await enqueue(job_id))
    return count


async def statuses(tenant: UUID, lead_id: UUID) -> list[dict]:
    async with get_tenant_session(tenant) as session:
        rows = (await session.execute(text("""
            SELECT id,notification_status,confirmation_status,agent_status,error_codes,created_at,updated_at
            FROM demo_delivery_jobs WHERE tenant_id=:tenant AND lead_id=:lead
            ORDER BY created_at DESC LIMIT 10
        """), {"tenant": tenant, "lead": lead_id})).mappings().all()
        return [dict(row) for row in rows]
