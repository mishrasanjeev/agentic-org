# SPDX-License-Identifier: Apache-2.0
"""Observability endpoints: admin-only reads of the run timelines and the live workload."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant
from api.route_enforcement import enforce_route_metadata
from api.v1 import observability as api
from observability import timeline, workload

TENANT = uuid.uuid4()
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


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


@pytest.fixture(autouse=True)
def _no_rate_limit_redis():
    # The route rate limiter counts in Redis; a unit test never reaches one (FINDINGS A-59).
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


SUMMARY = {
    "trace_id": TRACE,
    "span_id": "00f067aa0ba902b7",
    "name": "agenticorg.agent.run",
    "agent_id": "a1",
    "status": "unset",
    "run_status": "completed",
    "started_at": "2026-10-02T10:00:00+00:00",
    "duration_ms": 2400,
    "provider": "openai",
    "model": "gpt-4o",
    "tokens": 321,
    "correlation_id": "corr-1",
}


def test_non_admin_is_refused():
    client = TestClient(_app(["agents:write"]))
    assert client.get("/api/v1/observability/traces").status_code == 403
    assert client.get(f"/api/v1/observability/traces/{TRACE}").status_code == 403
    assert client.get("/api/v1/observability/workload").status_code == 403


def test_traces_list_says_whether_runs_are_recorded(monkeypatch):
    recent = AsyncMock(return_value=[SUMMARY])
    monkeypatch.setattr(timeline, "recent_traces", recent)
    client = TestClient(_app(["agenticorg:admin"]))
    resp = client.get("/api/v1/observability/traces", params={"agent_id": "a1", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False and body["tracing"] is False
    assert body["traces"] == [SUMMARY]
    recent.assert_awaited_once_with(TENANT, agent_id="a1", limit=5)
    assert client.get("/api/v1/observability/traces", params={"limit": 0}).status_code == 422


def test_trace_detail_and_the_404(monkeypatch):
    detail = {
        "trace_id": TRACE,
        "started_at": "2026-10-02T10:00:00+00:00",
        "duration_ms": 2400,
        "spans": [
            {
                "span_id": "00f067aa0ba902b7",
                "parent_span_id": None,
                "name": "agenticorg.agent.run",
                "kind": "internal",
                "status": "unset",
                "agent_id": "a1",
                "offset_ms": 0,
                "duration_ms": 2400,
                "attributes": {"agent.run.status": "completed"},
                "events": [{"name": "model_gateway.decision", "offset_ms": 0, "attributes": {"provider": "openai"}}],
            }
        ],
    }
    monkeypatch.setattr(timeline, "trace_detail", AsyncMock(return_value=detail))
    client = TestClient(_app(["agenticorg:admin"]))
    resp = client.get(f"/api/v1/observability/traces/{TRACE}")
    assert resp.status_code == 200 and resp.json() == detail
    monkeypatch.setattr(timeline, "trace_detail", AsyncMock(return_value=None))
    assert client.get(f"/api/v1/observability/traces/{TRACE}").status_code == 404
    assert client.get("/api/v1/observability/traces/not-a-trace").status_code == 422


def test_workload_is_the_assembled_picture(monkeypatch):
    picture = {"generated_at": "2026-10-02T10:05:00+00:00", "queues": {"queues": [], "error": None}}
    monkeypatch.setattr(workload, "workload", AsyncMock(return_value=picture))
    client = TestClient(_app(["agenticorg:admin"]))
    resp = client.get("/api/v1/observability/workload")
    assert resp.status_code == 200 and resp.json() == picture


class TestWorkloadParts:
    def test_queue_names_come_from_the_task_routes_and_the_default_queue(self):
        names = workload.queue_names()
        assert "celery" in names and "workflows" in names and "maintenance" in names and names == sorted(names)

    def test_queue_depths_read_the_broker_lists_and_report_an_unreachable_broker(self, monkeypatch):
        calls: list[str] = []

        class _Client:
            async def llen(self, name):
                calls.append(name)
                return 2 if name == "celery" else 0

            async def aclose(self):
                calls.append("closed")

        monkeypatch.setattr(workload.aioredis, "from_url", lambda *_a, **_k: _Client())
        depths = asyncio.run(workload.queue_depths())
        assert depths["error"] is None
        assert {q["name"]: q["depth"] for q in depths["queues"]}["celery"] == 2
        assert calls[-1] == "closed"

        class _Broken:
            async def llen(self, _name):
                raise OSError("connection refused")

            async def aclose(self):
                return None

        monkeypatch.setattr(workload.aioredis, "from_url", lambda *_a, **_k: _Broken())
        assert asyncio.run(workload.queue_depths()) == {"queues": None, "error": "OSError"}

    def test_review_deadlines_count_pending_overdue_and_the_soonest(self, monkeypatch):
        now = datetime.now(UTC)
        rows = [(now - timedelta(minutes=5),), (now + timedelta(minutes=2, seconds=5),), (now + timedelta(hours=3),)]

        class _Session:
            async def execute(self, _query):
                return SimpleNamespace(all=lambda: rows)

        @contextlib.asynccontextmanager
        async def _ctx(_tid):
            yield _Session()

        monkeypatch.setattr("core.database.get_tenant_session", _ctx)
        reviews = asyncio.run(workload.review_deadlines(TENANT))
        assert reviews["pending"] == 3 and reviews["overdue"] == 1 and reviews["error"] is None
        assert 110 <= reviews["soonest_seconds_left"] <= 125
        assert reviews["soonest_due_at"] == rows[1][0].isoformat()

    def test_outcome_parts_summarise_their_rows(self, monkeypatch):
        answers = {
            "runs": [({"agent.run.status": "completed"}, 1000), ({"agent.run.status": "completed"}, 3000), ({}, 2000)],
            "calls": [("completed", 900), ("failed", 100), ("completed", 1500)],
            "guardrails": [("blocked", 2), ("transformed", 5)],
        }
        current = {"key": "runs"}

        class _Session:
            async def execute(self, _query):
                return SimpleNamespace(all=lambda: answers[current["key"]])

        @contextlib.asynccontextmanager
        async def _ctx(_tid):
            yield _Session()

        monkeypatch.setattr("core.database.get_tenant_session", _ctx)
        runs = asyncio.run(workload.run_outcomes(TENANT))
        assert runs == {
            "window_hours": 1,
            "runs": 3,
            "by_status": {"completed": 2, "unknown": 1},
            "p50_duration_ms": 2000,
            "error": None,
        }
        current["key"] = "calls"
        calls = asyncio.run(workload.model_call_outcomes(TENANT))
        assert calls == {"window_hours": 1, "calls": 3, "failed": 1, "p50_latency_ms": 900, "error": None}
        current["key"] = "guardrails"
        guardrails = asyncio.run(workload.guardrail_outcomes(TENANT))
        assert guardrails == {"window_hours": 1, "blocked": 2, "transformed": 5, "error": None}

    def test_every_part_reports_its_own_failure(self, monkeypatch):
        @contextlib.asynccontextmanager
        async def _broken(_tid):
            raise RuntimeError("database down")
            yield  # pragma: no cover

        monkeypatch.setattr("core.database.get_tenant_session", _broken)
        monkeypatch.setattr(workload, "queue_depths", AsyncMock(return_value={"queues": None, "error": "OSError"}))
        picture = asyncio.run(workload.workload(TENANT))
        assert picture["queues"]["error"] == "OSError"
        for part in ("reviews", "runs", "model_calls", "guardrails"):
            assert picture[part]["error"] == "RuntimeError", part
        assert picture["tracing_enabled"] is False and picture["timeline_enabled"] is False
        assert datetime.fromisoformat(picture["generated_at"]).tzinfo is not None
