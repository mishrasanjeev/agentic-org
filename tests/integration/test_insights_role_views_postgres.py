# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights role views (operations, HR, marketing, commerce) against real Postgres, as a role without BYPASSRLS.

A scratch schema holds the tables these views read (from the ORM models), the
v6z106 migration adds the insights tables, and every tenant-scoped table gets
the forced row-level policy production uses. Each role opens its own view: the
COO's operations view counts runs of agents filed under ``ops`` and
``operations`` only, plus workflow runs, stalled runs and unhealthy connectors;
the CHRO's HR view counts HR runs and professional tax returns; the CMO's
marketing view counts leads, drafts and the draft approval time; the merchant's
commerce view draws the shopping funnel in order and counts dead-lettered order
webhooks. An administrator opening a role view sees that view's domain only, a
CFO is refused the operations view, and another tenant sees none of it.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from core.insights.metrics import day_bounds, local_today

KOLKATA = ZoneInfo("Asia/Kolkata")
MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z106_insights_foundation.py"
PASSWORD = "local-test-password"  # noqa: S105 - a throwaway role in a disposable test database
BASE_TABLES = (
    "tenants",
    "users",
    "companies",
    "agents",
    "hitl_queue",
    "agent_cost_ledger",
    "spend_usage_rollups",
    "spend_fx_rates",
    "budget_alerts",
    "audit_log",
    "agent_feedback",
    "connectors",
    "connector_configs",
    "workflow_definitions",
    "workflow_runs",
    "lead_pipeline",
    "content_drafts",
    "professional_tax_returns",
    "commerce_carts",
    "commerce_checkout_sessions",
    "commerce_orders",
    "commerce_payment_operations",
    "commerce_order_webhook_deliveries",
    # v6z109: the views read money saved and alerts.
    "value_baselines",
    "value_rates",
    "insights_alerts",
)

pytestmark = [
    pytest.mark.skipif(not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"),
    pytest.mark.timeout(300),
]


def _sync_url() -> str:
    return os.environ["AGENTICORG_DB_URL"].replace("+asyncpg", "")


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z106_insights_foundation_role_views", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in_schema(conn: Connection, schema: str) -> None:
    conn.exec_driver_sql(f'SET search_path TO "{schema}"')


def _tables():
    import core.models  # noqa: F401 - registers every model on the metadata
    from core.models.base import BaseModel

    md = BaseModel.metadata
    closure: list = []
    stack = list(BASE_TABLES)
    while stack:
        name = stack.pop()
        if name in [t.name for t in closure]:
            continue
        table = md.tables[name]
        closure.append(table)
        stack.extend(fk.column.table.name for fk in table.foreign_keys)
    return closure


def _rls(conn: Connection, table: str, column: str = "tenant_id") -> None:
    # Identifiers come from the ORM metadata, never from input.
    conn.exec_driver_sql(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    conn.exec_driver_sql(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    conn.exec_driver_sql(
        f'CREATE POLICY tenant_isolation ON "{table}" '
        f"USING ({column}::text = current_setting('agenticorg.tenant_id', true)) "
        f"WITH CHECK ({column}::text = current_setting('agenticorg.tenant_id', true))"
    )


class World:
    def __init__(self, admin: Engine, schema: str) -> None:
        self.admin = admin
        self.schema = schema

    def add(self, *objects) -> None:
        with self.admin.connect() as conn:
            _in_schema(conn, self.schema)
            with Session(bind=conn) as session:
                session.add_all(objects)
                session.commit()
            conn.commit()

    def scalar(self, sql: str, **params):
        with self.admin.begin() as conn:
            _in_schema(conn, self.schema)
            return conn.execute(text(sql), params).scalar()


@pytest.fixture
async def world(monkeypatch) -> AsyncIterator[World]:
    admin = create_engine(_sync_url())
    schema = "insights_views_" + uuid.uuid4().hex[:12]
    role = "insights_views_" + uuid.uuid4().hex[:12]
    tables = _tables()
    with admin.begin() as conn:
        # Identifiers are a fixed prefix plus UUID hex; never client input.
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        _in_schema(conn, schema)
        from core.models.base import BaseModel

        BaseModel.metadata.create_all(conn, tables=tables)
        with Operations.context(MigrationContext.configure(conn)):
            _migration().upgrade()
        for table in tables:
            if table.name == "tenants":
                _rls(conn, "tenants", "id")
            elif "tenant_id" in table.c:
                _rls(conn, table.name)
        conn.exec_driver_sql(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{PASSWORD}' NOBYPASSRLS")
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
    url = admin.url.set(drivername="postgresql+asyncpg", username=role, password=PASSWORD)
    runtime = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    from core import database

    factory = async_sessionmaker(runtime, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    monkeypatch.setattr("api.v1.insights.insights_enabled_for", AsyncMock(return_value=True))
    from core.config import settings

    monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
    try:
        yield World(admin, schema)
    finally:
        await runtime.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
        admin.dispose()


def _request(role: str, *, domains: list[str] | None, admin: bool = False):
    scopes = ["agenticorg:admin"] if admin else ["insights:read"]
    claims = {
        "sub": f"{role}@example.test",
        "role": role,
        "agenticorg:user_id": str(uuid.uuid4()),
        "agenticorg:domains": domains,
        "grantex:scopes": scopes,
    }
    return SimpleNamespace(state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode=None), query_params={})


ADMIN = _request("admin", domains=None, admin=True)
COO = _request("coo", domains=["ops"])
CHRO = _request("chro", domains=["hr"])
CMO = _request("cmo", domains=["marketing"])
MERCHANT = _request("merchant", domains=["commerce"])
CFO = _request("cfo", domains=["finance"])


def _seed(world: World) -> SimpleNamespace:
    from core.models.agent import Agent
    from core.models.commerce_payments import CommercePaymentOperationRow
    from core.models.commerce_ucp import (
        CommerceCartRow,
        CommerceCheckoutSessionRow,
        CommerceOrderRow,
        CommerceOrderWebhookDeliveryRow,
        CommerceOrderWebhookSubscriptionRow,
    )
    from core.models.company import Company
    from core.models.connector_config import ConnectorConfig
    from core.models.content_draft import ContentDraft
    from core.models.insights import RunFact
    from core.models.lead_pipeline import LeadPipeline
    from core.models.professional_tax import ProfessionalTaxReturn
    from core.models.tenant import Tenant
    from core.models.workflow import WorkflowDefinition, WorkflowRun

    now = datetime.now(UTC)
    today = local_today(KOLKATA, now)
    start = day_bounds(today, KOLKATA)[0] + timedelta(minutes=1)
    a, b = uuid.uuid4(), uuid.uuid4()
    world.add(
        Tenant(id=a, name="Synthetic views A", slug="views-a-" + a.hex, data_region="IN", settings={}),
        Tenant(id=b, name="Synthetic views B", slug="views-b-" + b.hex, data_region="IN", settings={}),
    )
    company = uuid.uuid4()
    world.add(Company(id=company, tenant_id=a, name="Acme Books", pan="AAAPA1234A"))
    base = {"system_prompt_ref": "prompts/synthetic.txt", "hitl_condition": "confidence < 0.8", "status": "active"}
    agents = {domain: uuid.uuid4() for domain in ("ops", "operations", "hr", "finance", "marketing")}
    world.add(
        *(
            Agent(id=agent_id, tenant_id=a, name=f"{d} agent", agent_type=f"{d}_bot", domain=d, **base)
            for d, agent_id in agents.items()
        )
    )

    def fact(domain, minute, status="completed"):
        return RunFact(
            tenant_id=a,
            run_key="agents:" + uuid.uuid4().hex,
            entrypoint="agents",
            agent_id=agents[domain],
            agent_type=f"{domain}_bot",
            domain=domain,
            status=status,
            duration_ms=1000,
            tokens_total=10,
            cost_usd=Decimal("0.01"),
            started_at=start + timedelta(minutes=minute),
        )

    world.add(
        fact("ops", 1),
        fact("ops", 2),
        fact("operations", 3, status="failed"),
        *(fact("hr", 10 + i) for i in range(3)),
        *(fact("finance", 20 + i) for i in range(4)),
        fact("marketing", 30),
    )
    wf = uuid.uuid4()
    world.add(WorkflowDefinition(id=wf, tenant_id=a, name="Ticket to fix", domain="ops", definition={"steps": []}))
    world.add(
        WorkflowRun(
            tenant_id=a,
            workflow_def_id=wf,
            status="completed",
            started_at=start,
            completed_at=start + timedelta(minutes=3),
        ),
        WorkflowRun(
            tenant_id=a,
            workflow_def_id=wf,
            status="completed",
            started_at=start + timedelta(minutes=5),
            completed_at=start + timedelta(minutes=9),
        ),
        WorkflowRun(
            tenant_id=a,
            workflow_def_id=wf,
            status="running",
            started_at=start + timedelta(minutes=6),
            timeout_at=now - timedelta(minutes=1),
        ),
        ConnectorConfig(tenant_id=a, connector_name="tally", health_status="unhealthy"),
        ConnectorConfig(tenant_id=a, connector_name="gmail", health_status="healthy"),
        ProfessionalTaxReturn(
            tenant_id=a,
            company_id=company,
            state_code="MH",
            filing_period="2026-10",
            gross_wages=Decimal("100000"),
            pt_amount=Decimal("200"),
            total_payable=Decimal("200"),
            status="submitted",
            submitted_at=start + timedelta(hours=1),
        ),
        ProfessionalTaxReturn(
            tenant_id=a,
            company_id=company,
            state_code="KA",
            filing_period="2026-10",
            gross_wages=Decimal("90000"),
            pt_amount=Decimal("200"),
            total_payable=Decimal("200"),
            status="ready_for_manual_upload",
        ),
        LeadPipeline(tenant_id=a, name="Lead one", email="one@example.test", stage="new", created_at=start),
        LeadPipeline(tenant_id=a, name="Lead two", email="two@example.test", stage="qualified", created_at=start),
        ContentDraft(
            tenant_id=a, service="blog", status="approved", created_at=start, decided_at=start + timedelta(hours=2)
        ),
        ContentDraft(tenant_id=a, service="blog", status="draft", created_at=start),
    )
    commerce = {
        "merchant_id": "shop-a",
        "seller_agent_id": "seller",
        "serving_host": "shop.example.test",
        "ucp_version": "1",
    }
    expires = now + timedelta(days=1)
    world.add(
        CommerceCartRow(id="cart-1", tenant_id=a, state={}, expires_at=expires, created_at=start, **commerce),
        CommerceCartRow(id="cart-2", tenant_id=a, state={}, expires_at=expires, created_at=start, **commerce),
        CommerceCheckoutSessionRow(
            id="chk-1",
            tenant_id=a,
            status="completed",
            order_id="ord-1",
            state={},
            expires_at=expires,
            created_at=start,
            **commerce,
        ),
        CommerceCheckoutSessionRow(
            id="chk-2", tenant_id=a, status="incomplete", state={}, expires_at=expires, created_at=start, **commerce
        ),
        CommerceOrderRow(
            id="ord-1",
            tenant_id=a,
            checkout_session_id="chk-1",
            currency="INR",
            total_minor_units=50000,
            snapshot={},
            decision_id="dec-1",
            executor="plural",
            created_at=start,
            **commerce,
        ),
        CommercePaymentOperationRow(
            tenant_id=a,
            merchant_id="shop-a",
            checkout_session_id="chk-1",
            order_id="ord-1",
            decision_id="dec-1",
            provider="plural",
            operation="capture",
            phase="succeeded",
            idempotency_key="idem-1",
            created_at=start,
        ),
    )
    subscription = uuid.uuid4()
    world.add(
        CommerceOrderWebhookSubscriptionRow(
            id=subscription,
            tenant_id=a,
            merchant_id="shop-a",
            url="https://buyer.example.test/hook",
            scheme="ucp",
            source="merchant",
        ),
    )
    world.add(
        CommerceOrderWebhookDeliveryRow(
            id=uuid.uuid4(),
            tenant_id=a,
            subscription_id=subscription,
            merchant_id="shop-a",
            order_id="ord-1",
            order_revision=1,
            webhook_id=uuid.uuid4(),
            event_type="order.created",
            payload={},
            payload_sha256="0" * 64,
            next_attempt_at=now,
            status="dead_lettered",
            created_at=start,
        ),
    )
    return SimpleNamespace(a=a, b=b)


def _tiles(view: dict) -> dict:
    return {m["key"]: m for m in view["tiles"] + view["charts"]}


@pytest.mark.asyncio
async def test_each_role_view_reads_its_own_domain(world) -> None:
    from api.v1 import insights as api

    seed = _seed(world)
    a = str(seed.a)

    ops = _tiles(await api.insights_view("operations", COO, tenant_id=a))
    # Runs of agents filed under "ops" and "operations" (2 completed, 1 failed), nothing else.
    assert ops["runs.volume"]["value"] == 3
    assert ops["runs.success_rate"]["value"] == pytest.approx(66.7)
    assert ops["workflows.runs"]["value"] == 3
    assert {r["label"]: r["value"] for r in ops["workflows.runs"]["rows"]} == {"completed": 2, "running": 1}
    assert ops["workflows.stalled"]["value"] == 1 and ops["workflows.stalled"]["state"] == "watch"
    assert ops["connectors.unhealthy"]["value"] == 1

    hr = _tiles(await api.insights_view("hr", CHRO, tenant_id=a))
    assert hr["runs.volume"]["value"] == 3 and hr["runs.success_rate"]["value"] == 100.0
    assert hr["hr.pt_returns"]["value"] == 1
    assert {r["label"]: r["value"] for r in hr["hr.pt_returns"]["rows"]} == {"submitted": 1, "not_submitted": 1}

    marketing = _tiles(await api.insights_view("marketing", CMO, tenant_id=a))
    assert marketing["marketing.leads"]["value"] == 2
    assert {r["label"] for r in marketing["marketing.leads"]["rows"]} == {"new", "qualified"}
    assert marketing["marketing.content_drafts"]["value"] == 2
    assert marketing["marketing.draft_cycle_time"]["value"] == pytest.approx(2 * 3_600_000)
    assert marketing["runs.volume"]["value"] == 1

    commerce = _tiles(await api.insights_view("commerce", MERCHANT, tenant_id=a))
    funnel = commerce["commerce.funnel"]
    assert [(r["label"], r["value"]) for r in funnel["rows"]] == [
        ("carts", 2),
        ("checkouts", 2),
        ("checkouts_completed", 1),
        ("orders", 1),
        ("orders_paid", 1),
    ]
    assert commerce["commerce.orders"]["value"] == 1
    assert commerce["commerce.webhook_failures"]["value"] == 1
    assert commerce["commerce.webhook_failures"]["state"] == "watch"

    # An administrator opening a role view sees that view's domain only.
    admin_hr = _tiles(await api.insights_view("hr", ADMIN, tenant_id=a))
    assert admin_hr["runs.volume"]["value"] == 3
    executive = _tiles(await api.insights_view("executive", ADMIN, tenant_id=a))
    assert executive["runs.volume"]["value"] == 11

    # A CFO cannot open the operations view, nor the COO the HR view.
    for request, view in ((CFO, "operations"), (COO, "hr"), (MERCHANT, "executive")):
        with pytest.raises(HTTPException) as refused:
            await api.insights_view(view, request, tenant_id=a)
        assert refused.value.status_code == 403

    # Another tenant sees none of tenant A's activity.
    other = _tiles(await api.insights_view("operations", ADMIN, tenant_id=str(seed.b)))
    assert other["workflows.runs"]["data_quality"] == "no_data"
    assert other["connectors.unhealthy"]["data_quality"] == "no_data"
    other_commerce = _tiles(await api.insights_view("commerce", ADMIN, tenant_id=str(seed.b)))
    assert other_commerce["commerce.funnel"]["data_quality"] == "no_data"
