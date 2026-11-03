# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The dashboard's own metrics, the landing-view synthetic check and the per-statement plans."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prometheus_client import REGISTRY

from core.insights import telemetry
from observability import synthetic

TENANT = uuid.uuid4()


def _sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_timed_observes_the_block_even_when_it_raises():
    before = _sample("agenticorg_insights_view_seconds_count", {"view": "telemetry_probe"})
    with telemetry.timed(telemetry.view_seconds, "telemetry_probe"):
        pass
    with pytest.raises(RuntimeError), telemetry.timed(telemetry.view_seconds, "telemetry_probe"):
        raise RuntimeError("boom")
    assert _sample("agenticorg_insights_view_seconds_count", {"view": "telemetry_probe"}) == before + 2


def test_the_metrics_carry_no_tenant_user_or_run_label():
    for metric in (telemetry.metric_seconds, telemetry.view_seconds, telemetry.explorer_seconds,
                   telemetry.export_jobs_total, telemetry.rollup_last_success):
        labels = set(metric._labelnames)
        assert not labels & {"tenant", "tenant_id", "user", "user_id", "run", "run_id"}, metric


@pytest.mark.asyncio
async def test_resolving_a_metric_is_timed_under_its_key_also_when_it_fails():
    import dataclasses

    from core.insights import registry

    metric = dataclasses.replace(registry.METRICS["runs.volume"], resolver=AsyncMock(side_effect=LookupError))
    before = _sample("agenticorg_insights_metric_seconds_count", {"metric": "runs.volume"})
    with pytest.raises(LookupError):
        await registry.resolve(metric, SimpleNamespace())
    assert _sample("agenticorg_insights_metric_seconds_count", {"metric": "runs.volume"}) == before + 1


def test_insights_landing_config_defaults_to_every_view_and_a_1500_ms_budget():
    from core.insights import registry

    clean = synthetic.validate_config("insights_landing", {})
    assert clean == {"views": list(registry.VIEWS), "max_view_ms": 1500}
    clean = synthetic.validate_config("insights_landing", {"views": ["finance", "finance", "hr"], "max_view_ms": 900})
    assert clean == {"views": ["finance", "hr"], "max_view_ms": 900}


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"views": []}, "views must be a list"),
        ({"views": "finance"}, "views must be a list"),
        ({"views": ["finance", "nowhere"]}, "views must be a list"),
        ({"max_view_ms": 50}, "between 100 and 60000"),
        ({"url": "https://example.test"}, "unknown config keys"),
    ],
)
def test_an_invalid_insights_landing_config_says_what_is_wrong(config, message):
    with pytest.raises(ValueError, match=message):
        synthetic.validate_config("insights_landing", config)


@pytest.mark.asyncio
async def test_the_landing_probe_fails_slow_or_broken_views(monkeypatch):
    timings = {"finance": {"ms": 400.0}, "hr": {"ms": 2100.0}, "partner": {"ms": 30.0, "error": "DBAPIError"}}
    fake = AsyncMock(return_value=timings)
    monkeypatch.setattr(telemetry, "time_landing_views", fake)
    check = synthetic.Check(
        id=uuid.uuid4(), tenant_id=TENANT, name="landing", kind="insights_landing",
        config={"views": ["finance", "hr", "partner"], "max_view_ms": 1500},
    )
    result = await synthetic.probe(check)
    fake.assert_awaited_once_with(TENANT, ["finance", "hr", "partner"])
    assert result.status == "failed"
    assert result.reasons == ["insights_view_slow:hr", "insights_view_failed:partner"]
    assert result.detail == {"views": timings, "budget_ms": 1500}

    fake.return_value = {"finance": {"ms": 400.0}}
    check = synthetic.Check(id=uuid.uuid4(), tenant_id=TENANT, name="landing", kind="insights_landing",
                            config={"views": ["finance"]})
    assert (await synthetic.probe(check)).status == "ok"


@pytest.mark.asyncio
async def test_time_landing_views_reports_a_failing_view_and_carries_on(monkeypatch):
    import contextlib

    from core import database
    from core.insights import registry

    calls: list[str] = []

    @contextlib.asynccontextmanager
    async def session(_tid):
        yield SimpleNamespace()

    contexts: list[SimpleNamespace] = []

    async def context(_session, _tid, _caller, **_kw):
        # The second view answered (hr) fails on its first metric.
        contexts.append(SimpleNamespace(domains=None, fail=len(contexts) == 1))
        return contexts[-1]

    async def resolve(metric, ctx):
        calls.append(metric.key)
        if ctx.fail:
            raise RuntimeError("hr failed")
        return {}

    monkeypatch.setattr(database, "get_tenant_session", session)
    monkeypatch.setattr("api.v1.insights._context", context)
    monkeypatch.setattr(registry, "resolve", resolve)
    out = await telemetry.time_landing_views(TENANT, ["finance", "hr"])
    assert set(out) == {"finance", "hr"}
    assert "error" not in out["finance"] and out["hr"]["error"] == "RuntimeError"
    assert set(registry.VIEWS["finance"].keys) <= set(calls)


@pytest.mark.asyncio
async def test_the_statement_timeout_also_plans_each_statement_for_its_parameters():
    from core.insights.metrics import apply_statement_timeout

    executed: list[str] = []

    class Session:
        async def execute(self, statement, params=None):
            executed.append(str(statement))

    await apply_statement_timeout(Session())
    assert any("plan_cache_mode" in sql and "force_custom_plan" in sql for sql in executed)
    assert any("statement_timeout" in sql for sql in executed)
