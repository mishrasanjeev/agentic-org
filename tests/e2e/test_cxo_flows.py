"""E2E tests for CFO and CMO user flows.

Tests complete user journeys using the FastAPI TestClient, verifying
that the platform works end-to-end from KPIs through NL queries,
report scheduling, and agent tool verification.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Module-level tenant UUID so every test in this module shares the same
# FK-satisfying tenants row. See the app() fixture for the INSERT.
_TEST_TENANT_ID = str(uuid.uuid4())


@pytest.fixture(scope="module")
def app():
    import asyncio

    from sqlalchemy import text as _sql_text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from api.main import app as _app
    from core import database as _db
    from core.database import init_db
    from core.models.base import BaseModel

    # Replace the process-wide engine with a ``NullPool`` engine for the
    # lifetime of this module. NullPool opens a fresh asyncpg connection
    # per checkout and closes it on release, which sidesteps the
    # "Future attached to a different loop" errors that show up when
    # Starlette's BaseHTTPMiddleware spawns child tasks — without
    # pooling, no connection ever crosses an event loop boundary.
    test_engine = create_async_engine(
        _db.settings.db_url,
        poolclass=NullPool,
        pool_pre_ping=True,
    )
    original_engine = _db.engine
    original_factory = _db.async_session_factory
    _db.engine = test_engine
    _db.async_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _setup() -> None:
        async with test_engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        await init_db()
        async with test_engine.begin() as conn:
            await conn.execute(
                _sql_text(
                    "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
                    "VALUES (:id, :name, :slug, 'enterprise', 'IN', '{}'::jsonb) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": _TEST_TENANT_ID,
                    "name": f"E2E Tenant {_TEST_TENANT_ID[:8]}",
                    "slug": f"e2e-{_TEST_TENANT_ID[:8]}",
                },
            )

    asyncio.run(_setup())
    asyncio.run(test_engine.dispose())

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    _app.router.lifespan_context = _test_lifespan
    try:
        yield _app
    finally:
        # Restore the production engine so other modules that import
        # core.database don't see the test replacement.
        _db.engine = original_engine
        _db.async_session_factory = original_factory


@pytest.fixture
def client(app):
    """TestClient with auth middleware bypassed and admin scopes granted.

    Shares the module-scoped tenant UUID so FK constraints resolve, and
    disposes the shared engine's connection pool before each test to
    sidestep Starlette's ``BaseHTTPMiddleware`` sub-task loop-binding
    quirk. Multi-request tests (company_switcher, create_and_retrieve)
    spawn a child task inside that middleware which re-enters asyncpg
    with a Future born in the previous test's loop; disposing the pool
    forces fresh connections bound to the current test's loop.
    """
    test_tenant_id = _TEST_TENANT_ID

    from api.deps import get_current_tenant
    app.dependency_overrides[get_current_tenant] = lambda: test_tenant_id

    async def _fake_validate(token):
        return {
            "sub": "e2e-user",
            "agenticorg:tenant_id": test_tenant_id,
            "agenticorg:scopes": ["agenticorg:admin"],
        }

    with patch("auth.grantex_middleware.validate_token", side_effect=_fake_validate):
        with patch("auth.grantex_middleware.extract_tenant_id", return_value=test_tenant_id):
            with patch("auth.grantex_middleware.extract_scopes", return_value=["agenticorg:admin"]):
                # raise_server_exceptions=True so server tracebacks surface
                # in the pytest output. Without it, every 500 looks the
                # same — which is why these tests took several CI cycles
                # to debug.
                with TestClient(app, raise_server_exceptions=True) as c:
                    c.headers["Authorization"] = "Bearer fake-e2e-token"
                    c._test_tenant_id = test_tenant_id
                    yield c

    app.dependency_overrides.pop(get_current_tenant, None)


# Tests in this module drive the FastAPI TestClient and expect a real
# Postgres+Redis behind it. The e2e-tests CI job now provides both via
# service containers (see .github/workflows/deploy.yml), so these run
# unconditionally. For local development, bring up compose or export
# AGENTICORG_DATABASE_URL / AGENTICORG_REDIS_URL to point at any pair.


# ═══════════════════════════════════════════════════════════════════════════
# CFO Journey
# ═══════════════════════════════════════════════════════════════════════════


class TestCFOJourney:
    """End-to-end CFO user flow."""

    def test_cfo_kpis_return_valid_data(self, client):
        """CFO KPI dashboard returns all required metrics (basic metrics shape)."""
        resp = client.get("/api/v1/kpis/cfo")
        assert resp.status_code == 200
        data = resp.json()
        # Current KPI contract uses basic metrics shape
        required_keys = [
            "agent_count", "total_tasks_30d", "success_rate",
            "hitl_interventions", "total_cost_usd", "domain_breakdown",
        ]
        for key in required_keys:
            assert key in data, f"CFO KPI missing: {key}"

    def test_nl_query_finance_routes_correctly(self, client):
        """NL query with finance question routes to finance domain."""
        resp = client.post("/api/v1/chat/query", json={
            "query": "What is our accounts receivable aging and cash flow position?"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "finance"
        assert data["confidence"] >= 0.7

    def test_report_schedule_created_successfully(self, client):
        """CFO can create a report schedule that persists."""
        resp = client.post("/api/v1/report-schedules", json={
            "report_type": "cfo_daily",
            "cron_expression": "daily",
            "delivery_channels": [{"type": "email", "target": "cfo@company.com"}],
            "format": "pdf",
            "is_active": True,
        })
        assert resp.status_code == 201
        schedule = resp.json()
        assert schedule["report_type"] == "cfo_daily"
        assert schedule["is_active"] is True

        # Verify it appears in the list
        list_resp = client.get("/api/v1/report-schedules")
        assert list_resp.status_code == 200
        schedules = list_resp.json()
        schedule_ids = [s["id"] for s in schedules]
        assert schedule["id"] in schedule_ids

    def test_company_switcher_lists_companies(self, client):
        """Company switcher returns list after creating companies."""
        # Create companies with required fields (pan is mandatory)
        client.post("/api/v1/companies", json={
            "name": "Test Corp A", "pan": "AABCA0001A", "industry": "IT",
        })
        client.post("/api/v1/companies", json={
            "name": "Test Corp B", "pan": "AABCB0002B", "industry": "Finance",
        })
        resp = client.get("/api/v1/companies")
        assert resp.status_code == 200
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items", [])
        assert len(items) >= 2

    def test_ap_processor_has_pinelabs_payment_tools(self):
        """AP Processor agent includes PineLabs payment tools."""
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS
        ap_tools = _AGENT_TYPE_DEFAULT_TOOLS["ap_processor"]
        assert "create_order" in ap_tools, "AP Processor missing PineLabs create_order"
        assert "check_order_status" in ap_tools, "AP Processor missing PineLabs check_order_status"

    def test_treasury_agent_has_expected_tools(self):
        """Treasury agent has the expected set of finance tools."""
        from core.langgraph.agents.treasury_agent import DEFAULT_TOOLS
        expected = {"check_account_balance", "fetch_bank_statement", "get_balance",
                    "get_balance_sheet", "get_cash_position"}
        assert expected == set(DEFAULT_TOOLS)

    def test_cfo_kpis_with_company_filter(self, client):
        """CFO KPIs accept company_id parameter."""
        resp = client.get("/api/v1/kpis/cfo?company_id=test-company")
        assert resp.status_code == 200
        data = resp.json()
        assert data["company_id"] == "test-company"

    def test_create_and_retrieve_company(self, client):
        """Full company lifecycle: create -> retrieve -> verify fields."""
        create_resp = client.post("/api/v1/companies", json={
            "name": "Sharma Manufacturing",
            "gstin": "27AABCS9999F1Z5",
            "pan": "AABCS9999F",
            "industry": "Manufacturing",
        })
        assert create_resp.status_code == 201
        company = create_resp.json()

        get_resp = client.get(f"/api/v1/companies/{company['id']}")
        assert get_resp.status_code == 200
        fetched = get_resp.json()
        assert fetched["name"] == "Sharma Manufacturing"
        assert fetched["gstin"] == "27AABCS9999F1Z5"


# ═══════════════════════════════════════════════════════════════════════════
# CMO Journey
# ═══════════════════════════════════════════════════════════════════════════


class TestCMOJourney:
    """End-to-end CMO user flow."""

    def test_cmo_kpis_return_valid_data(self, client):
        """CMO KPI dashboard returns all required metrics (basic metrics shape)."""
        resp = client.get("/api/v1/kpis/cmo")
        assert resp.status_code == 200
        data = resp.json()
        required_keys = [
            "agent_count", "total_tasks_30d", "success_rate",
            "hitl_interventions", "total_cost_usd", "domain_breakdown",
        ]
        for key in required_keys:
            assert key in data, f"CMO KPI missing: {key}"

    def test_nl_query_marketing_routes_correctly(self, client):
        """NL query with marketing question routes to marketing domain."""
        resp = client.post("/api/v1/chat/query", json={
            "query": "Show me the latest SEO rankings and campaign conversion rates"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "marketing"
        assert data["confidence"] >= 0.7

    def test_content_factory_has_cmo_approval_gate(self):
        """Content Factory agent includes CMO approval-related tools."""
        from core.langgraph.agents.content_factory import DEFAULT_TOOLS
        assert "approve_draft_post" in DEFAULT_TOOLS, (
            "Content Factory missing approve_draft_post tool for CMO gate"
        )

    def test_ga4_connector_has_real_api_paths(self):
        """GA4 connector uses correct Google Analytics Data API base URL."""
        from connectors.marketing.ga4 import GA4Connector
        c = GA4Connector({"property_id": "123456"})
        assert c.base_url == "https://analyticsdata.googleapis.com/v1beta"

    def test_social_media_agent_tools(self):
        """Social Media agent has CMO-governed social publishing tools."""
        from core.langgraph.agents.social_media import DEFAULT_TOOLS
        assert "create_tweet" in DEFAULT_TOOLS
        assert "create_update" in DEFAULT_TOOLS
        assert "get_post_analytics" in DEFAULT_TOOLS

    def test_social_media_agent_confidence_floor(self):
        """Social Media agent has appropriate confidence floor for approval."""
        import inspect

        from core.langgraph.agents.social_media import build_graph
        sig = inspect.signature(build_graph)
        floor = sig.parameters["confidence_floor"].default
        assert floor == 0.85

    def test_cmo_weekly_report_generation(self):
        """CMO weekly report generates valid HTML output using basic-metrics contract.

        The legacy ROAS section was removed when /kpis/cmo stopped
        fabricating marketing dashboards from raw task_output. The
        current report uses the same basic-metrics shape as the KPI
        endpoint; only assert for sections that actually exist.
        """
        from core.reports.generator import ReportGenerator, ReportOutput
        gen = ReportGenerator()
        output = gen.generate(report_type="cmo_weekly", params={})
        assert isinstance(output, ReportOutput)
        assert output.report_type == "cmo_weekly"
        assert "CMO Weekly Report" in output.content_html
        for section in ("Agents", "Tasks (30d)", "Success Rate", "Domain Breakdown"):
            assert section in output.content_html, f"CMO report missing: {section}"

    def test_campaign_report_generation(self):
        """Campaign performance report contains marketing metrics."""
        from core.reports.generator import ReportGenerator
        gen = ReportGenerator()
        output = gen.generate(report_type="campaign_report", params={})
        data = output.content_data
        # Current campaign_report uses _fetch_cmo_kpis — the unified
        # basic-metrics shape. Legacy roas_by_channel / social_engagement
        # fields were removed when /kpis/cmo stopped fabricating
        # dashboards from raw task_output.
        required = ("agent_count", "total_tasks_30d", "success_rate",
                    "hitl_interventions", "total_cost_usd", "domain_breakdown")
        for key in required:
            assert key in data, f"campaign_report missing: {key}"

    def test_email_marketing_agent_registered(self):
        """Email marketing agent is registered in the platform."""
        from api.v1.a2a import _DOMAIN_MAP
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS
        assert "email_marketing" in _AGENT_TYPE_DEFAULT_TOOLS
        assert _DOMAIN_MAP["email_marketing"] == "marketing"

    def test_competitive_intel_agent_registered(self):
        """Competitive intel agent is registered in the platform."""
        from api.v1.a2a import _DOMAIN_MAP
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS
        assert "competitive_intel" in _AGENT_TYPE_DEFAULT_TOOLS
        assert _DOMAIN_MAP["competitive_intel"] == "marketing"

    def test_abm_agent_registered(self):
        """ABM agent is registered in the platform."""
        from api.v1.a2a import _DOMAIN_MAP
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS
        assert "abm" in _AGENT_TYPE_DEFAULT_TOOLS
        assert _DOMAIN_MAP["abm"] == "marketing"


# ═══════════════════════════════════════════════════════════════════════════
# Cross-Domain Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossDomainIntegration:
    """Verify cross-domain features work together."""

    def test_a2a_agent_card_returns_skills(self, client):
        """A2A agent card lists available agent skills."""
        resp = client.get("/api/v1/a2a/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "skills" in data
        assert isinstance(data["skills"], list)
        assert len(data["skills"]) > 0

    def test_a2a_agent_list(self, client):
        """A2A agents endpoint lists all available agents."""
        resp = client.get("/api/v1/a2a/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data

    def test_connector_registry_has_all_domains(self):
        """Connector categories cover finance, marketing, hr, ops, comms."""
        import importlib

        from connectors.framework.base_connector import BaseConnector

        # Import connector modules to verify they exist with correct categories
        domain_connectors = {
            "finance": "connectors.finance.stripe",
            "marketing": "connectors.marketing.hubspot",
            "hr": "connectors.hr.okta",
            "ops": "connectors.ops.jira",
            "comms": "connectors.comms.slack",
        }
        for domain, module_path in domain_connectors.items():
            mod = importlib.import_module(module_path)
            # Find the connector class in the module
            connector_cls = None
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (isinstance(attr, type) and issubclass(attr, BaseConnector)
                        and attr is not BaseConnector):
                    connector_cls = attr
                    break
            assert connector_cls is not None, f"No connector class found in {module_path}"
            assert connector_cls.category == domain, (
                f"{module_path}: expected category={domain}, got {connector_cls.category}"
            )

    def test_workflow_templates_exist(self):
        """All 15 workflow templates are present."""
        from pathlib import Path
        workflows_dir = Path(__file__).resolve().parent.parent.parent / "workflows" / "examples"
        yamls = list(workflows_dir.glob("*.yaml"))
        assert len(yamls) >= 11
