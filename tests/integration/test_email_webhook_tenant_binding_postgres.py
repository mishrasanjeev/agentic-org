# SPDX-License-Identifier: Apache-2.0
"""Per-tenant email webhook URLs on PostgreSQL, through the real app and the real wait store.

A tenant admin with a human session reads the tenant's URLs; a signed MoEngage event posted to
tenant A's URL resumes tenant A's durable wait and nobody else's, and one naming tenant B in its
payload resumes neither. The shared URL, with ``AGENTICORG_WEBHOOKS_TENANT_BOUND_PATHS`` on,
refuses the same event with 409 and leaves both waits in PostgreSQL untouched.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.email_webhooks import webhook_path
from core.models.workflow import WorkflowEventWait
from workflows.event_waits import SqlAlchemyWorkflowEventWaitRepository, WorkflowEventWaitStore

_DB_URL = os.getenv("AGENTICORG_DB_URL", "")
pytestmark = pytest.mark.skipif(not _DB_URL, reason="integration tests require AGENTICORG_DB_URL")

MOENGAGE_KEY = "test-moengage-webhook-key"  # placeholder, not a real provider secret
EMAIL = "lead-02@example.com"
CAMPAIGN = "camp-autumn-3"


def _signed_moengage(tenant_claim: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"event_type": "EMAIL_CLICK", "email": EMAIL, "campaign_id": CAMPAIGN}
    if tenant_claim:
        payload["tenant_id"] = tenant_claim
    body = json.dumps(payload).encode()
    signature = hmac.new(MOENGAGE_KEY.encode(), body, hashlib.sha256).hexdigest()
    return {"content": body, "headers": {"Content-Type": "application/json", "X-MoEngage-Signature": signature}}


async def test_email_webhook_inbox_is_readable_by_the_tenant_admin_only(client: Any, auth_headers: dict[str, str]):
    from tests.integration.conftest import TEST_TENANT_ID

    response = await client.get("/api/v1/email-webhook-inbox", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "inboxes": [
            {"provider": provider, "path": webhook_path(TEST_TENANT_ID, provider)}
            for provider in ("sendgrid", "mailchimp", "moengage")
        ]
    }
    assert (await client.get("/api/v1/email-webhook-inbox")).status_code == 401


async def test_signed_events_resume_only_the_bound_tenants_postgres_wait(client: Any, monkeypatch) -> None:
    from tests.integration.conftest import TEST_TENANT_ID, seed_tenant_and_admin

    monkeypatch.delenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", raising=False)
    monkeypatch.setenv("MOENGAGE_WEBHOOK_KEY", MOENGAGE_KEY)
    tenant_a = uuid.UUID(TEST_TENANT_ID)
    tenant_b = uuid.uuid4()
    engine = create_async_engine(_DB_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await seed_tenant_and_admin(conn, str(tenant_b), str(uuid.uuid4()), "admin-b@example.com")
    store = WorkflowEventWaitStore(repository=SqlAlchemyWorkflowEventWaitRepository(session_factory), redis=AsyncMock())
    run_a, run_b = f"wfr_a_{uuid.uuid4().hex[:10]}", f"wfr_b_{uuid.uuid4().hex[:10]}"
    for run_id, tenant in ((run_a, tenant_a), (run_b, tenant_b)):
        await store.register(
            engine_run_id=run_id,
            step_id="wait-click",
            event_type="email.clicked",
            match_criteria={"campaign_id": CAMPAIGN, "email": EMAIL},
            timeout_at=datetime.now(UTC) + timedelta(hours=1),
            tenant_id=tenant,
        )

    async def statuses() -> dict[str, str]:
        async with session_factory() as session:
            rows = await session.execute(
                select(WorkflowEventWait.engine_run_id, WorkflowEventWait.status).where(
                    WorkflowEventWait.engine_run_id.in_([run_a, run_b])
                )
            )
            return dict(rows.tuples().all())

    resumed = MagicMock()
    try:
        with (
            patch("api.v1.webhooks._workflow_event_wait_store", return_value=store),
            patch("api.v1.webhooks._get_redis", return_value=AsyncMock()),
            patch("core.tasks.workflow_tasks.resume_workflow_wait.delay", resumed),
        ):
            # The shared URL, switched to tenant-bound paths: refused, nothing claimed.
            monkeypatch.setattr("core.config.settings.webhooks_tenant_bound_paths", True)
            shared = await client.post("/api/v1/webhooks/email/moengage", **_signed_moengage(str(tenant_b)))
            assert shared.status_code == 409, shared.text
            assert await statuses() == {run_a: "waiting", run_b: "waiting"}

            # Tenant A's URL, payload naming tenant B: refused, nothing claimed.
            path_a = webhook_path(tenant_a, "moengage")
            crossed = await client.post(path_a, **_signed_moengage(str(tenant_b)))
            assert crossed.status_code == 200 and crossed.json()["refused"] == 1, crossed.text
            assert await statuses() == {run_a: "waiting", run_b: "waiting"}

            # Tenant A's URL, no tenant in the payload: tenant A's wait, and only it.
            bound = await client.post(path_a, **_signed_moengage(None))
            assert bound.status_code == 200 and bound.json()["refused"] == 0, bound.text
            assert await statuses() == {run_a: "matched", run_b: "waiting"}
            assert [call.args[0] for call in resumed.call_args_list] == [run_a]
    finally:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(WorkflowEventWait).where(WorkflowEventWait.engine_run_id.in_([run_a, run_b]))
                )
        await engine.dispose()
