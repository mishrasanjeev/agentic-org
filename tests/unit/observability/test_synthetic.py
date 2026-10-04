# SPDX-License-Identifier: Apache-2.0
"""Synthetic checks: configuration, the probes, running, the sweep and the endpoints."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant
from api.route_enforcement import enforce_route_metadata
from api.v1 import observability as api
from core.config import settings
from observability import synthetic

TENANT = uuid.uuid4()
ROOT = Path(__file__).resolve().parents[3]


def _check(kind: str = "model", config: dict | None = None, **over) -> synthetic.Check:
    defaults = {
        "model": {"prompt": "Reply with the word ready."},
        "knowledge": {"query": "leave policy", "top_k": 5, "min_results": 1},
        "guardrail": {"stage": "input", "text": "card 4111 1111 1111 1111", "expect": "detected"},
        "audit_chain": {"recent": 100},
    }
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "name": f"{kind} check",
        "kind": kind,
        "config": config if config is not None else defaults[kind],
    }
    base.update(over)
    return synthetic.Check(**base)


class TestConfig:
    def test_each_kind_fills_its_defaults_and_drops_empty_options(self):
        assert synthetic.validate_config("model", {"prompt": " hello "}) == {"prompt": "hello"}
        assert synthetic.validate_config("model", {"prompt": "p", "contains": "ready", "max_latency_ms": 5000}) == {
            "prompt": "p",
            "contains": "ready",
            "max_latency_ms": 5000,
        }
        assert synthetic.validate_config("knowledge", {"query": "q"}) == {"query": "q", "top_k": 5, "min_results": 1}
        assert synthetic.validate_config("guardrail", {"stage": "Input", "text": "t"}) == {
            "stage": "input",
            "text": "t",
            "expect": "detected",
        }
        assert synthetic.validate_config("audit_chain", {}) == {"recent": 1000}

    @pytest.mark.parametrize(
        ("kind", "config", "message"),
        [
            ("http", {}, "kind must be one of"),
            ("model", [], "config must be an object"),
            ("model", {}, "prompt is required"),
            ("model", {"prompt": "p", "url": "https://example.test"}, "unknown config keys"),
            ("model", {"prompt": "x" * 2001}, "at most 2000"),
            ("model", {"prompt": "p", "max_latency_ms": 0}, "between 1 and"),
            ("model", {"prompt": "p", "max_latency_ms": True}, "whole number"),
            ("knowledge", {"query": "q", "top_k": 3, "min_results": 4}, "cannot exceed top_k"),
            ("knowledge", {"query": "q", "top_k": 21}, "between 1 and 20"),
            ("guardrail", {"stage": "nowhere", "text": "t"}, "stage must be one of"),
            ("guardrail", {"stage": "input", "text": "t", "expect": "maybe"}, "expect must be one of"),
            ("audit_chain", {"recent": 0}, "between 1 and 10000"),
        ],
    )
    def test_an_invalid_configuration_says_what_is_wrong(self, kind, config, message):
        with pytest.raises(ValueError, match=message):
            synthetic.validate_config(kind, config)

    def test_interval_and_name_bounds(self):
        assert synthetic.validate_interval(5) == 5 and synthetic.validate_interval(1440) == 1440
        for bad in (4, 1441, "60", True):
            with pytest.raises(ValueError):
                synthetic.validate_interval(bad)
        assert synthetic.validate_name("  nightly model  ") == "nightly model"
        for bad_name in ("", " ", "x" * 121):
            with pytest.raises(ValueError):
                synthetic.validate_name(bad_name)

    def test_a_check_is_due_once_its_interval_has_passed(self):
        now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        assert _check().due(now) is True
        assert _check(last_run_at=now - timedelta(minutes=59), interval_minutes=60).due(now) is False
        assert _check(last_run_at=now - timedelta(minutes=60), interval_minutes=60).due(now) is True
        assert _check(enabled=False).due(now) is False


class TestProbes:
    def test_the_model_probe_goes_through_the_router_as_the_tenant(self):
        answer = SimpleNamespace(content="Ready.", model="gpt-4o-mini", tokens_used=12)
        complete = AsyncMock(return_value=answer)
        with patch("core.llm.router.llm_router.complete", complete):
            reasons, detail = asyncio.run(
                synthetic._probe_model(TENANT, {"prompt": "Reply with ready.", "contains": "READY", "model": "m1"})
            )
        assert reasons == [] and detail == {"model": "gpt-4o-mini", "tokens": 12}
        complete.assert_awaited_once_with(
            [{"role": "user", "content": "Reply with ready."}],
            model_override="m1",
            max_tokens=synthetic.MODEL_MAX_TOKENS,
            tenant_id=str(TENANT),
        )

    def test_the_model_probe_reports_an_empty_or_wrong_answer_without_keeping_it(self):
        for content, expected in (
            ("", ["empty_answer", "answer_missing_expected_text"]),
            ("no", ["answer_missing_expected_text"]),
        ):
            answer = SimpleNamespace(content=content, model="m", tokens_used=1)
            with patch("core.llm.router.llm_router.complete", AsyncMock(return_value=answer)):
                reasons, detail = asyncio.run(synthetic._probe_model(TENANT, {"prompt": "p", "contains": "ready"}))
            assert reasons == expected and "content" not in detail and content not in map(str, detail.values())

    def test_the_knowledge_probe_counts_results(self):
        found = SimpleNamespace(results=[object(), object()])
        search = AsyncMock(return_value=found)
        with patch("api.v1.knowledge._search_knowledge", search):
            ok = asyncio.run(synthetic._probe_knowledge(TENANT, {"query": "q", "top_k": 5, "min_results": 2}))
            short = asyncio.run(synthetic._probe_knowledge(TENANT, {"query": "q", "top_k": 5, "min_results": 3}))
        assert ok == ([], {"results": 2}) and short == (["too_few_results"], {"results": 2})
        request, tenant = search.await_args.args
        assert (request.query, request.top_k, tenant) == ("q", 5, str(TENANT))

    @pytest.mark.parametrize(
        ("expect", "allowed", "findings", "outcomes", "reasons"),
        [
            ("blocked", False, 1, 1, []),
            ("blocked", True, 1, 1, ["guardrail_not_blocked"]),
            ("detected", True, 2, 1, []),
            ("detected", True, 0, 0, ["guardrail_not_detected"]),
            ("clean", True, 0, 0, []),
            ("clean", True, 1, 1, ["guardrail_unexpected_finding"]),
        ],
    )
    def test_the_guardrail_probe_is_a_dry_run_held_to_its_expectation(
        self, expect, allowed, findings, outcomes, reasons
    ):
        result = SimpleNamespace(allowed=allowed, findings=findings, outcomes=[object()] * outcomes, enforced=False)
        evaluate = AsyncMock(return_value=result)
        with patch("core.governance.guardrails.evaluate", evaluate):
            got, detail = asyncio.run(
                synthetic._probe_guardrail(TENANT, {"stage": "input", "text": "synthetic", "expect": expect})
            )
        assert got == reasons
        assert detail == {"findings": findings, "rules_matched": outcomes, "allowed": allowed, "enforced": False}
        evaluate.assert_awaited_once_with("input", "synthetic", tenant_id=TENANT, dry_run=True)

    def test_the_audit_chain_probe_verifies_the_newest_links(self):
        from core.governance import audit_chain

        intact = audit_chain.Verification(
            tenant_id=TENANT, head=audit_chain.Head(seq=250, hash="h"), unsealed=0, checked_from=151
        )
        intact.verified = 100
        verify = AsyncMock(return_value=intact)
        status = AsyncMock(return_value={"head": {"seq": 250}})
        with patch.object(audit_chain, "status", status), patch.object(audit_chain, "verify", verify):
            reasons, detail = asyncio.run(synthetic._probe_audit_chain(TENANT, {"recent": 100}))
        assert reasons == [] and detail == {"status": "verified", "head_seq": 250, "verified": 100}
        verify.assert_awaited_once_with(TENANT, from_seq=151, limit=100)
        broken = audit_chain.Verification(
            tenant_id=TENANT, head=audit_chain.Head(seq=250, hash="h"), unsealed=0, checked_from=151
        )
        broken.first_break = audit_chain.Break(seq=200, row_id="r", reason="link_hash")
        with (
            patch.object(audit_chain, "status", status),
            patch.object(audit_chain, "verify", AsyncMock(return_value=broken)),
        ):
            reasons, detail = asyncio.run(synthetic._probe_audit_chain(TENANT, {"recent": 100}))
        assert reasons == ["audit_chain_broken"] and (detail["break_seq"], detail["break_reason"]) == (200, "link_hash")


class TestRun:
    def test_ok_failed_and_error_results(self, monkeypatch):
        monkeypatch.setattr(synthetic, "_probe_model", AsyncMock(return_value=([], {"model": "m", "tokens": 3})))
        ok = asyncio.run(synthetic.probe(_check()))
        assert (ok.status, ok.reasons, ok.detail, ok.trigger) == ("ok", [], {"model": "m", "tokens": 3}, "schedule")
        assert ok.started_at.tzinfo is not None and ok.latency_ms >= 0

        monkeypatch.setattr(synthetic, "_probe_model", AsyncMock(return_value=(["empty_answer"], {"model": "m"})))
        failed = asyncio.run(synthetic.probe(_check(), trigger="manual"))
        assert (failed.status, failed.reasons, failed.trigger) == ("failed", ["empty_answer"], "manual")

        monkeypatch.setattr(synthetic, "_probe_model", AsyncMock(side_effect=RuntimeError("provider secret detail")))
        error = asyncio.run(synthetic.probe(_check()))
        assert error.status == "error" and error.detail == {"error_type": "RuntimeError"} and error.reasons == []

    def test_a_slow_probe_fails_and_a_hung_one_is_an_error(self, monkeypatch):
        async def _slow(_tenant, _config):
            await asyncio.sleep(0.05)
            return [], {}

        monkeypatch.setattr(synthetic, "_probe_model", _slow)
        slow = asyncio.run(synthetic.probe(_check(config={"prompt": "p", "max_latency_ms": 1})))
        assert (slow.status, slow.reasons) == ("failed", ["too_slow"])
        monkeypatch.setattr(synthetic, "PROBE_TIMEOUT_SECONDS", 0.01)
        hung = asyncio.run(synthetic.probe(_check()))
        assert hung.status == "error" and hung.detail == {"error_type": "TimeoutError"}

    def test_a_stored_configuration_that_is_no_longer_valid_is_an_error(self):
        result = asyncio.run(synthetic.probe(_check(config={"prompt": ""})))
        assert result.status == "error" and result.detail == {"error_type": "ValueError"}

    def test_run_check_stores_and_meters_the_result(self, monkeypatch):
        from observability.metrics import synthetic_checks_total

        stored: list = []

        async def _store(check, result):
            stored.append((check, result))

        monkeypatch.setattr(synthetic, "_store_result", _store)
        monkeypatch.setattr(
            synthetic, "_probe_knowledge", AsyncMock(return_value=(["too_few_results"], {"results": 0}))
        )
        counter = synthetic_checks_total.labels(kind="knowledge", result="failed")
        before = counter._value.get()
        check = _check("knowledge")
        result = asyncio.run(synthetic.run_check(check, trigger="manual"))
        assert stored == [(check, result)] and result.status == "failed"
        assert counter._value.get() == before + 1

    def test_the_metric_carries_no_tenant_label(self):
        from observability.metrics import synthetic_checks_total

        assert tuple(synthetic_checks_total._labelnames) == ("kind", "result")

    def test_due_checks_are_the_longest_waiting_first(self, monkeypatch):
        now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        never = _check(name="never")
        old = _check(name="old", last_run_at=now - timedelta(hours=5))
        older = _check(name="older", last_run_at=now - timedelta(hours=9))
        fresh = _check(name="fresh", last_run_at=now - timedelta(minutes=1))
        off = _check(name="off", enabled=False)
        monkeypatch.setattr(synthetic, "list_checks", AsyncMock(return_value=[fresh, old, off, never, older]))
        due = asyncio.run(synthetic.due_checks(TENANT, now=now))
        assert [check.name for check in due] == ["never", "older", "old"]
        assert [check.name for check in asyncio.run(synthetic.due_checks(TENANT, now=now, limit=1))] == ["never"]


class TestTasks:
    def test_the_sweep_is_off_by_default(self):
        from core.tasks import synthetic_tasks

        assert settings.synthetic_checks_enabled is False
        with patch.object(synthetic_tasks, "_tenants_with_checks", AsyncMock(side_effect=AssertionError("never read"))):
            assert asyncio.run(synthetic_tasks._run_synthetic_checks_async()) == {
                "enabled": False,
                "tenants": 0,
                "ran": 0,
                "not_ok": 0,
                "errors": 0,
            }

    def test_the_sweep_runs_due_checks_and_isolates_failures(self, monkeypatch):
        from core.tasks import synthetic_tasks

        monkeypatch.setattr(settings, "synthetic_checks_enabled", True)
        t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        good, bad, broken = _check(name="good"), _check(name="bad"), _check(name="broken")

        async def _due(tenant_id, **_k):
            if tenant_id == t2:
                raise RuntimeError("tenant database unavailable")
            return [good, bad, broken] if tenant_id == t1 else []

        async def _run(check, **_k):
            if check is broken:
                raise RuntimeError("result not stored")
            status = "ok" if check is good else "failed"
            return synthetic.Result(check_id=check.id, status=status, latency_ms=1, started_at=datetime.now(UTC))

        with (
            patch.object(synthetic_tasks, "_tenants_with_checks", AsyncMock(return_value=[t1, t2, t3])),
            patch.object(synthetic, "due_checks", _due),
            patch.object(synthetic, "run_check", _run),
        ):
            result = asyncio.run(synthetic_tasks._run_synthetic_checks_async())
        assert result == {"enabled": True, "tenants": 3, "ran": 2, "not_ok": 1, "errors": 2}

    def test_the_tasks_are_scheduled(self):
        from core.tasks.celery_app import app

        assert "core.tasks.synthetic_tasks" in app.conf.include
        schedule = app.conf.beat_schedule
        assert schedule["run-synthetic-checks"]["task"] == "core.tasks.synthetic_tasks.run_synthetic_checks"
        assert schedule["prune-synthetic-results"]["task"] == "core.tasks.synthetic_tasks.prune_synthetic_results"

    def test_the_tables_are_tenant_scoped_under_row_level_security(self):
        migration = (ROOT / "migrations" / "versions" / "v6_z42_synthetic_checks.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z41_tamper_evident_audit"' in migration
        assert '_TABLES = ("synthetic_checks", "synthetic_check_results")' in migration
        assert "FORCE ROW LEVEL SECURITY" in migration and "current_setting('agenticorg.tenant_id', true)" in migration
        for kind in synthetic.KINDS:
            assert f"'{kind}'" in migration


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _app(scopes: list[str]) -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.auth_mode = "api_key"
        request.state.claims = {"sub": "apikey:key_01"}
        request.state.scopes = scopes
        request.state.tenant_id = str(TENANT)
        return await call_next(request)

    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: str(TENANT)
    return app


@pytest.fixture
def _no_rate_limit_redis():
    # The route rate limiter counts in Redis; a unit test never reaches one (FINDINGS A-59).
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


@pytest.mark.usefixtures("_no_rate_limit_redis")
class TestEndpoints:
    def test_every_route_is_admin_only(self):
        client = TestClient(_app(["agents:write"]))
        cid = uuid.uuid4()
        assert client.get("/api/v1/observability/checks").status_code == 403
        assert client.post("/api/v1/observability/checks", json={"name": "n", "kind": "model"}).status_code == 403
        assert client.patch(f"/api/v1/observability/checks/{cid}", json={"enabled": False}).status_code == 403
        assert client.delete(f"/api/v1/observability/checks/{cid}").status_code == 403
        assert client.post(f"/api/v1/observability/checks/{cid}/run").status_code == 403
        assert client.get(f"/api/v1/observability/checks/{cid}/results").status_code == 403

    def test_the_list_says_whether_the_sweep_is_on(self, monkeypatch):
        check = _check()
        monkeypatch.setattr(synthetic, "list_checks", AsyncMock(return_value=[check]))
        client = TestClient(_app(["agenticorg:admin"]))
        body = client.get("/api/v1/observability/checks").json()
        assert body["enabled"] is False and body["kinds"] == list(synthetic.KINDS) and body["limit"] == 20
        assert body["checks"] == [check.to_dict()]

    def test_create_attributes_the_caller_and_refuses_an_invalid_check(self, monkeypatch):
        created = _check(created_by="api_key:apikey:key_01")
        create = AsyncMock(return_value=created)
        monkeypatch.setattr(synthetic, "create_check", create)
        client = TestClient(_app(["agenticorg:admin"]))
        payload = {"name": "model check", "kind": "model", "config": {"prompt": "p"}, "interval_minutes": 30}
        resp = client.post("/api/v1/observability/checks", json=payload)
        assert resp.status_code == 201 and resp.json()["id"] == str(created.id)
        create.assert_awaited_once_with(
            TENANT,
            actor_id="api_key:apikey:key_01",
            name="model check",
            kind="model",
            config={"prompt": "p"},
            interval_minutes=30,
            enabled=True,
        )
        monkeypatch.setattr(synthetic, "create_check", AsyncMock(side_effect=ValueError("prompt is required")))
        refused = client.post("/api/v1/observability/checks", json={"name": "n", "kind": "model"})
        assert refused.status_code == 422 and "prompt is required" in refused.text
        assert client.post("/api/v1/observability/checks", json={**payload, "interval_minutes": 1}).status_code == 422

    def test_update_delete_and_their_404s(self, monkeypatch):
        check = _check()
        update = AsyncMock(return_value=check)
        monkeypatch.setattr(synthetic, "update_check", update)
        client = TestClient(_app(["agenticorg:admin"]))
        resp = client.patch(f"/api/v1/observability/checks/{check.id}", json={"enabled": False})
        assert resp.status_code == 200
        update.assert_awaited_once_with(TENANT, check.id, actor_id="api_key:apikey:key_01", changes={"enabled": False})
        assert client.patch(f"/api/v1/observability/checks/{check.id}", json={}).status_code == 422
        monkeypatch.setattr(synthetic, "update_check", AsyncMock(return_value=None))
        assert client.patch(f"/api/v1/observability/checks/{check.id}", json={"enabled": True}).status_code == 404
        monkeypatch.setattr(synthetic, "delete_check", AsyncMock(return_value=True))
        assert client.delete(f"/api/v1/observability/checks/{check.id}").status_code == 204
        monkeypatch.setattr(synthetic, "delete_check", AsyncMock(return_value=False))
        assert client.delete(f"/api/v1/observability/checks/{check.id}").status_code == 404

    def test_run_now_and_the_results(self, monkeypatch):
        check = _check()
        result = synthetic.Result(
            check_id=check.id, status="ok", latency_ms=420, started_at=datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        )
        run = AsyncMock(return_value=result)
        monkeypatch.setattr(synthetic, "get_check", AsyncMock(return_value=check))
        monkeypatch.setattr(synthetic, "run_check", run)
        monkeypatch.setattr(synthetic, "results", AsyncMock(return_value=[result]))
        client = TestClient(_app(["agenticorg:admin"]))
        resp = client.post(f"/api/v1/observability/checks/{check.id}/run")
        assert resp.status_code == 200 and resp.json() == result.to_dict()
        run.assert_awaited_once_with(check, trigger="manual")
        listed = client.get(f"/api/v1/observability/checks/{check.id}/results", params={"limit": 5})
        assert listed.status_code == 200 and listed.json() == {"results": [result.to_dict()]}
        assert client.get(f"/api/v1/observability/checks/{check.id}/results", params={"limit": 0}).status_code == 422
        monkeypatch.setattr(synthetic, "get_check", AsyncMock(return_value=None))
        assert client.post(f"/api/v1/observability/checks/{check.id}/run").status_code == 404
        assert client.get(f"/api/v1/observability/checks/{check.id}/results").status_code == 404
