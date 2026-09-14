"""Enterprise bug sweep 2026-09-13 — billing / CDC / push / cron regressions.

Findings covered (each test fails on the pre-fix tree):
  1. Entitlement is durable (billing_subscriptions), Plural periods expire.
  2. Usage metering is wired into the agent runner and gated by plan limits.
  3. Push subscriptions are keyed by tenant + user.
  4. CDC triggers are tenant-scoped rows; the global poller is gone.
  5. The S3 connector refuses the fake "s3_compatible" mode.
  6. Catalog INR/USD prices sit inside a plausible FX band.
  7. The compliance cron runs on the real Celery app.
  8. No per-call sync Redis clients; diagnostics reflect Plural config vars.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

TENANT = "11111111-1111-1111-1111-111111111111"


# ── Helpers ──────────────────────────────────────────────────────────


class _FakeSession:
    """Records SQL executions; returns a canned row for the SELECT."""

    def __init__(self, row=None):
        self.row = row
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params or {}))
        if sql.lstrip().startswith("SELECT provider"):
            return SimpleNamespace(first=lambda: self.row)
        return SimpleNamespace(first=lambda: None, scalar_one=lambda: 0, all=lambda: [])


def _row(**overrides):
    base = {
        "provider": "stripe",
        "external_id": "sub_1",
        "provider_customer_id": "cus_1",
        "plan": "pro",
        "status": "active",
        "current_period_start": datetime.now(UTC),
        "current_period_end": datetime.now(UTC) + timedelta(days=30),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@contextlib.asynccontextmanager
async def _session_ctx(session):
    yield session


# ── 1. Durable entitlement ───────────────────────────────────────────


class TestDurableSubscriptions:
    def test_record_subscription_upserts_row_then_warms_cache(self, monkeypatch):
        from core.billing import subscriptions as subs

        session = _FakeSession(row=_row())
        redis = AsyncMock()
        monkeypatch.setattr(
            "core.database.get_tenant_session", lambda tid: _session_ctx(session)
        )
        monkeypatch.setattr("core.async_redis.get_async_redis", AsyncMock(return_value=redis))

        result = asyncio.run(
            subs.record_subscription(
                TENANT, provider="stripe", plan="pro",
                provider_subscription_id="sub_1", provider_customer_id="cus_1",
            )
        )

        assert result["is_paid"] is True and result["plan"] == "pro"
        assert any("INSERT INTO billing_subscriptions" in sql for sql, _ in session.executed)
        redis.set.assert_any_await(f"tenant_tier:{TENANT}", "pro")
        redis.set.assert_any_await(f"tenant:{TENANT}:plan", "pro")
        redis.set.assert_any_await(f"tenant:{TENANT}:billing_provider", "stripe")

    def test_record_subscription_rejects_non_uuid_tenant_and_unknown_provider(self):
        from core.billing import subscriptions as subs

        with pytest.raises(ValueError):
            asyncio.run(subs.record_subscription("t1", provider="stripe", plan="pro"))
        with pytest.raises(ValueError):
            subs.record_subscription_sync(TENANT, provider="paypal", plan="pro")

    def test_get_subscription_reads_db_not_redis(self, monkeypatch):
        """Redis says 'enterprise' (stale); the DB row says 'pro' and wins."""
        from core.billing import subscriptions as subs

        session = _FakeSession(row=_row(plan="pro"))
        redis = AsyncMock()
        redis.get.return_value = "enterprise"
        monkeypatch.setattr(
            "core.database.get_tenant_session", lambda tid: _session_ctx(session)
        )
        monkeypatch.setattr("core.async_redis.get_async_redis", AsyncMock(return_value=redis))

        sub = asyncio.run(subs.get_subscription(TENANT))
        assert sub["plan"] == "pro"
        redis.set.assert_any_await(f"tenant:{TENANT}:plan", "pro")  # cache re-warmed

    def test_get_subscription_falls_back_to_cache_only_when_db_unavailable(self, monkeypatch):
        from core.billing import subscriptions as subs

        @contextlib.asynccontextmanager
        async def _broken(tid):
            raise RuntimeError("db down")
            yield  # pragma: no cover

        redis = AsyncMock()
        redis.get.side_effect = lambda k: {
            f"tenant:{TENANT}:plan": "pro",
            f"tenant:{TENANT}:billing_provider": "plural",
            f"tenant:{TENANT}:billing_order_id": "ord_1",
        }.get(k)
        monkeypatch.setattr("core.database.get_tenant_session", _broken)
        monkeypatch.setattr("core.async_redis.get_async_redis", AsyncMock(return_value=redis))

        sub = asyncio.run(subs.get_subscription(TENANT))
        assert (sub["plan"], sub["provider"], sub["is_paid"]) == ("pro", "plural", True)

    def test_expired_or_cancelled_row_is_free(self):
        from core.billing.subscriptions import _row_to_dict

        for status in ("expired", "cancelled", "canceled", "unpaid"):
            sub = _row_to_dict(TENANT, _row(status=status))
            assert sub["plan"] == "free" and sub["is_paid"] is False, status

    def test_plural_activation_records_fixed_period_end(self, monkeypatch):
        """Finding 1: Plural is a one-time order — activation must carry an end date."""
        from core.billing import pinelabs_client

        recorded: dict = {}

        def _record(tenant_id, **kwargs):
            recorded["tenant_id"] = tenant_id
            recorded.update(kwargs)
            return {"current_period_end": kwargs["current_period_end"].isoformat()}

        from core.billing.subscriptions import free_subscription

        monkeypatch.setattr("core.billing.subscriptions.record_subscription_sync", _record)
        # Activation now reads the existing row first (stale-order guard).
        monkeypatch.setattr(
            "core.billing.subscriptions.get_subscription_sync",
            lambda tenant_id: free_subscription(tenant_id),
        )
        pinelabs_client._activate_subscription(TENANT, "pro", "order_123")

        assert recorded["provider"] == "plural"
        assert recorded["provider_subscription_id"] == "order_123"
        assert recorded["current_period_end"] - recorded["current_period_start"] == timedelta(
            days=30
        )

    def test_expire_overdue_plural_downgrades_only_past_period(self, monkeypatch):
        from core.billing import subscriptions as subs

        past = _row(provider="plural", external_id="o1",
                    current_period_end=datetime.now(UTC) - timedelta(days=1))
        future = _row(provider="plural", external_id="o2",
                      current_period_end=datetime.now(UTC) + timedelta(days=10))
        stripe_row = _row(provider="stripe", current_period_end=datetime.now(UTC) - timedelta(days=1))
        t_past, t_future, t_stripe = (uuid.uuid4() for _ in range(3))
        sessions = {t_past: _FakeSession(past), t_future: _FakeSession(future),
                    t_stripe: _FakeSession(stripe_row)}

        class _TenantsSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(all=lambda: [(t_past,), (t_future,), (t_stripe,)])

        monkeypatch.setattr("core.database.async_session_factory", lambda: _TenantsSession())
        monkeypatch.setattr(
            "core.database.get_tenant_session", lambda tid: _session_ctx(sessions[tid])
        )
        monkeypatch.setattr("core.async_redis.get_async_redis", AsyncMock(return_value=None))

        result = asyncio.run(subs.expire_overdue_plural_subscriptions())

        assert result == {"checked": 3, "expired": 1, "failed": 0}
        assert any("UPDATE billing_subscriptions" in sql for sql, _ in sessions[t_past].executed)
        assert not any("UPDATE" in sql for sql, _ in sessions[t_future].executed)
        assert not any("UPDATE" in sql for sql, _ in sessions[t_stripe].executed)

    def test_beat_schedules_plural_expiry_hourly(self):
        from core.tasks.celery_app import app

        entry = app.conf.beat_schedule["expire-plural-subscriptions"]
        assert entry["task"] == "core.tasks.budget_tasks.expire_plural_subscriptions"
        assert entry["schedule"] <= 3600.0

    def test_sync_bridge_refuses_to_run_inside_event_loop(self):
        from core.billing.subscriptions import _run_bridge

        async def _inner():
            async def _coro():
                return {}

            with pytest.raises(RuntimeError):
                _run_bridge(_coro())

        asyncio.run(_inner())

    def test_migration_v6z18_chains_off_newest_head_and_enables_rls(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(Config("alembic.ini"))
        # v6z19 (fresh-database repair for the same two tables) chains off
        # v6z18. Later migrations may sit above it; pin the single-head
        # invariant and the chain, not the identity of the current head.
        assert len(script.get_heads()) == 1
        assert "v6z19_repair_billing_cdc" in {r.revision for r in script.walk_revisions()}
        assert script.get_revision("v6z19_repair_billing_cdc").down_revision == "v6z18_billing_cdc_state"
        rev = script.get_revision("v6z18_billing_cdc_state")
        assert rev.down_revision == "v6z17_sessions_dsar"
        src = open(rev.path).read()
        assert 'TABLES: tuple[str, ...] = ("billing_subscriptions", "cdc_triggers")' in src
        assert "FORCE ROW LEVEL SECURITY" in src and "_tenant_isolation" in src
        assert "current_setting('agenticorg.tenant_id', true)" in src
        assert "provider_customer_id" in src


class TestBillingRoutesUseDurableState:
    @staticmethod
    def _app():
        from fastapi import FastAPI, Request

        from api.v1.billing import router

        app = FastAPI()

        @app.middleware("http")
        async def identity(request: Request, call_next):
            request.state.tenant_id = TENANT
            request.state.scopes = ["agenticorg:admin"]
            request.state.claims = {"sub": "user-1"}
            return await call_next(request)

        app.include_router(router, prefix="/api/v1")
        return app

    def test_subscription_route_reads_subscriptions_module(self, monkeypatch):
        from fastapi.testclient import TestClient

        from core.billing.subscriptions import free_subscription

        async def _get(tenant_id):
            sub = free_subscription(tenant_id)
            sub.update(plan="pro", tier="pro", provider="plural", order_id="o1",
                       status="active", is_paid=True)
            return sub

        monkeypatch.setattr("core.billing.subscriptions.get_subscription", _get)
        resp = TestClient(self._app()).get("/api/v1/billing/subscription")
        assert resp.status_code == 200
        body = resp.json()
        assert body["plan"] == "pro" and body["provider"] == "plural" and body["is_paid"] is True
        assert body["tenant_id"] == TENANT

    def test_usage_route_counts_agents_from_db_and_runs_from_async_pool(self, monkeypatch):
        from fastapi.testclient import TestClient

        redis = AsyncMock()
        redis.get.side_effect = lambda k: {f"usage:{TENANT}:runs": "7"}.get(k)
        monkeypatch.setattr("core.async_redis.get_async_redis", AsyncMock(return_value=redis))
        monkeypatch.setattr(
            "core.billing.usage_tracker.count_active_agents", AsyncMock(return_value=4)
        )
        resp = TestClient(self._app()).get("/api/v1/billing/usage")
        assert resp.status_code == 200
        assert resp.json() == {"agent_runs": 7, "agent_count": 4, "storage_bytes": 0}

    def test_cancel_uses_server_side_row_never_client_subscription_id(self, monkeypatch):
        from fastapi.testclient import TestClient

        from core.billing.subscriptions import free_subscription

        async def _get(tenant_id):
            sub = free_subscription(tenant_id)
            sub.update(plan="pro", tier="pro", provider="stripe", order_id="sub_server",
                       status="active", is_paid=True)
            return sub

        cancelled: list[str] = []
        deactivated: list[tuple] = []

        async def _deactivate(tenant_id, *, status):
            deactivated.append((tenant_id, status))
            return free_subscription(tenant_id)

        monkeypatch.setattr("core.billing.subscriptions.get_subscription", _get)
        monkeypatch.setattr("core.billing.subscriptions.deactivate_subscription", _deactivate)
        monkeypatch.setattr(
            "core.billing.stripe_client.cancel_subscription",
            lambda sid: cancelled.append(sid) or True,
        )
        resp = TestClient(self._app()).post(
            "/api/v1/billing/cancel", json={"subscription_id": "sub_ATTACKER"}
        )
        assert resp.status_code == 200
        assert cancelled == ["sub_server"]
        assert deactivated == [(TENANT, "cancelled")]

    def test_cancel_without_paid_subscription_is_400(self, monkeypatch):
        from fastapi.testclient import TestClient

        from core.billing.subscriptions import free_subscription

        async def _get(tenant_id):
            return free_subscription(tenant_id)

        monkeypatch.setattr("core.billing.subscriptions.get_subscription", _get)
        resp = TestClient(self._app()).post("/api/v1/billing/cancel", json={"subscription_id": "x"})
        assert resp.status_code == 400


# ── 2. Metering ──────────────────────────────────────────────────────


class TestRunMetering:
    def test_gate_returns_structured_limit_exceeded(self, monkeypatch):
        from core.billing.limits import LimitResult
        from core.billing.metering import gate_agent_run

        monkeypatch.setattr(
            "core.billing.limits.check_limit",
            AsyncMock(return_value=LimitResult(False, 1000, 1000, False)),
        )
        result = asyncio.run(gate_agent_run(TENANT))
        assert result["status"] == "limit_exceeded"
        assert result["limit"] == {"metric": "agent_runs", "usage": 1000, "limit": 1000}
        assert result["output"] == {} and "error" in result

    def test_gate_allows_when_metering_unavailable(self, monkeypatch):
        from core.billing.metering import gate_agent_run

        monkeypatch.setattr(
            "core.billing.limits.check_limit", AsyncMock(side_effect=RuntimeError("redis down"))
        )
        assert asyncio.run(gate_agent_run(TENANT)) is None

    def test_meter_never_raises(self, monkeypatch):
        from core.billing.metering import meter_agent_run

        monkeypatch.setattr(
            "core.billing.usage_tracker.increment_agent_runs",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        asyncio.run(meter_agent_run(TENANT))  # no exception

    def test_runner_calls_gate_and_meter(self):
        import inspect

        from core.langgraph import runner

        src = inspect.getsource(runner.run_agent)
        assert "gate_agent_run(tenant_id)" in src
        assert "meter_agent_run(tenant_id)" in src
        # gate must precede graph construction; meter must follow the invoke
        assert src.index("gate_agent_run") < src.index("build_agent_graph(")
        assert src.index("compiled.ainvoke") < src.index("meter_agent_run(tenant_id)")

    def test_tier_source_is_subscriptions_table(self, monkeypatch):
        from core.billing.limits import _get_tenant_tier
        from core.billing.subscriptions import free_subscription

        async def _get(tenant_id):
            sub = free_subscription(tenant_id)
            sub.update(plan="enterprise", tier="enterprise", is_paid=True, status="active")
            return sub

        monkeypatch.setattr("core.billing.subscriptions.get_subscription", _get)
        assert asyncio.run(_get_tenant_tier(TENANT)) == "enterprise"
        monkeypatch.setattr(
            "core.billing.subscriptions.get_subscription", AsyncMock(side_effect=RuntimeError)
        )
        assert asyncio.run(_get_tenant_tier(TENANT)) == "free"  # fail closed


# ── 3. Push per user ─────────────────────────────────────────────────


class TestPushPerUser:
    @pytest.fixture(autouse=True)
    def _memory_mode(self, monkeypatch):
        import sys
        import types

        from core.push import sender

        # pywebpush is an optional runtime dependency; provide a stub module.
        fake = types.ModuleType("pywebpush")
        fake.webpush = lambda **kwargs: None
        fake.WebPushException = type("WebPushException", (Exception,), {})
        monkeypatch.setitem(sys.modules, "pywebpush", fake)
        with patch("core.push.sender._get_redis", return_value=None):
            sender._memory_store.clear()
            yield
            sender._memory_store.clear()

    @staticmethod
    def _sub(i: str) -> dict:
        return {"endpoint": f"https://push.example.com/{i}", "keys": {"p256dh": "k", "auth": "a"}}

    def test_user_send_only_hits_that_user(self):
        from core.push import sender

        asyncio.run(sender.save_subscription("tenant-1", self._sub("alice"), user_id="alice"))
        asyncio.run(sender.save_subscription("tenant-1", self._sub("bob"), user_id="bob"))
        asyncio.run(sender.save_subscription("tenant-2", self._sub("eve"), user_id="alice"))

        with patch("core.push.sender.get_vapid_keys", return_value=("pub", "priv")), patch(
            "pywebpush.webpush"
        ) as mock_push:
            result = asyncio.run(
                sender.send_push_notification_for_user("tenant-1", "alice", "T", "B")
            )
        assert result["sent"] == 1
        endpoints = {c.kwargs["subscription_info"]["endpoint"] for c in mock_push.call_args_list}
        assert endpoints == {"https://push.example.com/alice"}

    def test_notify_approval_created_fans_out_to_subscribed_users(self):
        from core.push import sender

        asyncio.run(sender.save_subscription("tenant-1", self._sub("alice"), user_id="alice"))
        asyncio.run(sender.save_subscription("tenant-1", self._sub("bob"), user_id="bob"))
        with patch("core.push.sender.get_vapid_keys", return_value=("pub", "priv")), patch(
            "pywebpush.webpush"
        ) as mock_push:
            # bug sheet 2026-09-14 row 30: a shared-agent item still fans out to subscribers.
            totals = asyncio.run(
                sender.notify_approval_created("tenant-1", item_id="h1", agent_name="AP", agent_visibility="tenant")
            )
        assert totals["sent"] == 2 and mock_push.call_count == 2
        payloads = [c.kwargs["data"] for c in mock_push.call_args_list]
        assert all('"approval_id": "h1"' in p for p in payloads)

    def test_notify_approval_created_never_raises(self):
        from core.push import sender

        with patch("core.push.sender.get_vapid_keys", side_effect=RuntimeError("no vapid")):
            # bug sheet 2026-09-14 row 30: explicit shared scope so the vapid failure path runs.
            totals = asyncio.run(
                sender.notify_approval_created(
                    "tenant-1", item_id="h1", user_ids=["alice"], agent_visibility="tenant"
                )
            )
        assert totals == {"sent": 0, "failed": 0, "stale_removed": 0}

    def test_routes_require_user_subject(self):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        from api.v1.push import router

        app = FastAPI()

        @app.middleware("http")
        async def identity(request: Request, call_next):
            request.state.tenant_id = "tenant-1"
            request.state.claims = {"sub": ""}  # authenticated but no subject
            return await call_next(request)

        app.include_router(router, prefix="/api/v1")
        body = {"subscription": self._sub("x")}
        resp = TestClient(app).post("/api/v1/push/subscribe", json=body)
        assert resp.status_code == 401

    def test_subscribe_route_stores_under_user_key(self):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        from api.v1.push import router
        from core.push import sender

        app = FastAPI()

        @app.middleware("http")
        async def identity(request: Request, call_next):
            request.state.tenant_id = "tenant-1"
            request.state.claims = {"sub": "alice"}
            return await call_next(request)

        app.include_router(router, prefix="/api/v1")
        resp = TestClient(app).post("/api/v1/push/subscribe", json={"subscription": self._sub("x")})
        assert resp.status_code == 200
        assert len(sender._memory_store["push_subs:tenant-1:user:alice"]) == 1
        assert "push_subs:tenant-1" not in sender._memory_store


# ── 5. S3 connector ──────────────────────────────────────────────────


class TestS3ConnectorHonesty:
    def test_s3_compatible_mode_is_refused(self):
        from connectors.comms.s3 import S3CompatibleModeUnsupportedError, S3Connector

        with pytest.raises(S3CompatibleModeUnsupportedError):
            S3Connector({"s3_compatible": True, "bucket": "b"})

    def test_access_key_is_never_sent_as_bearer(self):
        import inspect

        from connectors.comms import s3

        src = inspect.getsource(s3)
        assert "access_key" not in src.split("class S3Connector")[1]
        assert "s3_compatible" in src  # the refusal path exists

    def test_catalog_entry_no_longer_claims_aws_s3(self):
        from connectors.catalog_meta import CATALOG_META

        assert "AWS S3" not in CATALOG_META["s3"]["display_name"]


# ── 6. Catalog FX band ───────────────────────────────────────────────


class TestCatalogFxBand:
    def test_current_catalog_passes(self):
        from scripts.check_billing_catalog import catalog_consistency_issues, fx_band_issues

        assert fx_band_issues() == []
        assert catalog_consistency_issues() == []

    def test_leftover_two_dollar_pro_price_is_caught(self, monkeypatch):
        from scripts import check_billing_catalog as chk

        def _price(plan_id, currency):
            if plan_id == "pro":
                return 2_00 if currency == "USD" else 9_999_00
            return 0

        monkeypatch.setattr(chk, "plan_price_minor", _price)
        issues = chk.fx_band_issues()
        assert any(i.startswith("pro:") and "FX band" in i for i in issues)

    def test_pro_usd_is_documented_99(self):
        from core.billing.catalog import plan_price_minor
        from core.billing.invoice_generator import PLAN_MONTHLY_FEE
        from core.billing.stripe_client import PLAN_AMOUNT_USD

        assert plan_price_minor("pro", "USD") == 99_00
        assert PLAN_AMOUNT_USD["pro"] == 99_00
        assert str(PLAN_MONTHLY_FEE["pro"]) == "99.00"


# ── 4/7/8. CDC poller, cron app, sync clients, diagnostics ──────────


class TestSweepMisc:
    def test_dead_modules_removed(self):
        assert importlib.util.find_spec("core.cdc.poller") is None
        assert importlib.util.find_spec("core.cron.celery_beat") is None

    def test_no_global_cdc_trigger_registry(self):
        from core.cdc import triggers

        assert not hasattr(triggers, "_triggers")

    def test_provider_clients_do_not_build_redis_per_call(self):
        import inspect

        from core.billing import pinelabs_client, stripe_client, usage_tracker

        for mod in (pinelabs_client, stripe_client):
            assert "from_url" not in inspect.getsource(mod)
        # exactly one cached sync client, guarded by a lock
        src = inspect.getsource(usage_tracker.sync_redis_client)
        assert "_sync_client is None" in src

    def test_diagnostics_reports_inr_readiness_from_plural_vars(self, monkeypatch):
        from api.v1.billing import _plural_configured

        monkeypatch.delenv("PLURAL_CLIENT_ID", raising=False)
        monkeypatch.delenv("PLURAL_CLIENT_SECRET", raising=False)
        monkeypatch.setenv("PINELABS_API_KEY", "legacy")  # must NOT count
        assert _plural_configured() is False
        monkeypatch.setenv("PLURAL_CLIENT_ID", "id")
        monkeypatch.setenv("PLURAL_CLIENT_SECRET", "secret")
        assert _plural_configured() is True

        import inspect

        from api.v1 import health

        assert "inr_plural_configured" in inspect.getsource(health.diagnostics)
