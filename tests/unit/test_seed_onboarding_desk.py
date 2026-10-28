# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Onboarding desk seed for the development stack (scripts/seed_onboarding_desk.py), without a database or Grantex."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.cases import push
from core.onboarding_desk import applications, cases, store
from scripts import seed_onboarding_desk as seed
from scripts.seed_dev import SeedError, seed_id

TENANT = seed_id("tenant")
DEV = {
    "AGENTICORG_ENV": "development",
    "GRANTEX_BASE_URL": "http://grantex:3001",
    "GRANTEX_API_KEY": "agenticorg-dev-grantex-sandbox-key",
}


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """The desk, push and governed-case calls the seed makes, recorded instead of written."""
    recorded: list[tuple[str, Any]] = []

    @asynccontextmanager
    async def _session(tenant_id: uuid.UUID) -> Any:
        yield SimpleNamespace(tenant_id=tenant_id)

    async def _enable_flag(_session: Any, tenant_id: uuid.UUID) -> None:
        recorded.append(("flag", tenant_id))

    async def _put_branding(tenant_id: uuid.UUID, branding: dict[str, Any], *, actor: str) -> None:
        recorded.append(("branding", (tenant_id, branding, actor)))

    async def _put_case_settings(tenant_id: uuid.UUID, settings: dict[str, Any], *, actor: str) -> None:
        recorded.append(("case_settings", (tenant_id, settings, actor)))

    async def _create_source(tenant_id: uuid.UUID, name: str, kind: str, *, actor: str) -> tuple[Any, str]:
        recorded.append(("source", (tenant_id, name, kind)))
        return SimpleNamespace(id=uuid.UUID(int=7)), "whsec-development"

    async def _configure_endpoint(_session: Any, tenant_id: uuid.UUID, *, url: str, enabled: bool, now: Any) -> Any:
        recorded.append(("endpoint", (tenant_id, url, enabled)))
        return None, SimpleNamespace(key_id="key-1", secret="push-secret-1")

    monkeypatch.setattr(seed, "_tenant_session", _session)
    monkeypatch.setattr(seed, "enable_flag", _enable_flag)
    monkeypatch.setattr(store, "enabled", lambda: True)
    monkeypatch.setattr(store, "put_branding", _put_branding)
    monkeypatch.setattr(store, "create_source", _create_source)
    monkeypatch.setattr(cases, "put_case_settings", _put_case_settings)
    monkeypatch.setattr(push, "configure_endpoint", _configure_endpoint)
    return recorded


async def test_configure_sets_up_the_seeded_tenant_and_hands_the_suite_its_signing_values(
    calls: list[tuple[str, Any]],
) -> None:
    summary = await seed.configure("http://grantex:3099/case-push")

    assert summary == {
        "tenant_id": str(TENANT),
        "partner_name": seed.BRANDING["partner_name"],
        "lead_source": {
            "id": str(uuid.UUID(int=7)),
            "webhook_path": f"/api/v1/onboarding-desk/webhooks/leads/{TENANT}/{uuid.UUID(int=7)}",
            "signing_secret": "whsec-development",
        },
        "push": {"url": "http://grantex:3099/case-push", "key_id": "key-1", "secret": "push-secret-1"},
    }
    kinds = [kind for kind, _ in calls]
    assert kinds == ["flag", "branding", "case_settings", "source", "endpoint"]
    case_settings = dict(calls)["case_settings"]
    assert case_settings == (TENANT, {"auto_open": True, "investigate": False}, seed.SEED_ACTOR)
    assert dict(calls)["endpoint"] == (TENANT, "http://grantex:3099/case-push", True)


async def test_configure_refuses_when_the_desk_is_off(
    calls: list[tuple[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "enabled", lambda: False)
    with pytest.raises(SeedError, match="onboarding desk is off"):
        await seed.configure("http://grantex:3099/case-push")
    assert calls == []


async def test_configure_refuses_a_lead_source_without_a_signing_secret(
    calls: list[tuple[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no_secret(*_args: Any, **_kwargs: Any) -> tuple[Any, str]:
        return SimpleNamespace(id=uuid.UUID(int=8)), ""

    monkeypatch.setattr(store, "create_source", _no_secret)
    with pytest.raises(SeedError, match="no signing secret"):
        await seed.configure("http://grantex:3099/case-push")


async def test_an_endpoint_from_an_earlier_run_gets_a_new_key_and_the_others_are_retired(
    calls: list[tuple[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _existing(*_args: Any, **_kwargs: Any) -> Any:
        return None, None

    async def _rotate(_session: Any, tenant_id: uuid.UUID, *, now: Any) -> Any:
        calls.append(("rotate", tenant_id))
        return SimpleNamespace(key_id="key-2", secret="push-secret-2")

    async def _retire(_session: Any, tenant_id: uuid.UUID, *, now: Any) -> None:
        calls.append(("retire", tenant_id))

    monkeypatch.setattr(push, "configure_endpoint", _existing)
    monkeypatch.setattr(push, "rotate_signing_key", _rotate)
    monkeypatch.setattr(push, "retire_previous_keys", _retire)

    key = await seed._push_endpoint(TENANT, "http://grantex:3099/case-push")

    assert key == {"key_id": "key-2", "secret": "push-secret-2"}
    assert calls[-2:] == [("rotate", TENANT), ("retire", TENANT)]


@pytest.fixture
def investigation(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """An application with a case, and checks that bring it to a decision."""
    state: dict[str, Any] = {"case_ref": "case_seeded", "case_error": None, "final": "awaiting_decision"}

    async def _get_application(tenant_id: uuid.UUID, application_id: uuid.UUID) -> Any:
        state["application"] = (tenant_id, application_id)
        return SimpleNamespace(case_ref=state["case_ref"], case_error=state["case_error"])

    async def _prepare(tenant_id: uuid.UUID) -> None:
        state["prepared"] = tenant_id

    async def _investigate(tenant_id: uuid.UUID, case_ref: str, *, actor: str, runtime: Any) -> dict[str, Any]:
        state["investigated"] = (tenant_id, case_ref, actor, runtime)
        return {"case_ref": case_ref, "investigated": True}

    async def _get_case(_tenant_id: uuid.UUID, _case_ref: str) -> Any:
        return SimpleNamespace(state=state["final"], policy_result={"tier": "low"})

    monkeypatch.setattr(applications, "get_application", _get_application)
    monkeypatch.setattr(seed, "prepare_case_agents", _prepare)
    monkeypatch.setattr(cases, "investigate", _investigate)
    monkeypatch.setattr(cases, "get_case", _get_case)
    monkeypatch.setattr("core.cases.runtime.CaseRuntime", lambda **kwargs: SimpleNamespace(**kwargs))
    return state


def _run(**overrides: Any) -> dict[str, Any]:
    return {"tenant_id": str(TENANT), "application_id": str(uuid.UUID(int=9)), **overrides}


async def test_investigate_runs_the_checks_with_the_case_agents_and_the_model_stub(
    investigation: dict[str, Any],
) -> None:
    result = await seed.investigate(_run())

    assert result == {"case_ref": "case_seeded", "investigated": True, "state": "awaiting_decision", "tier": "low"}
    assert investigation["application"] == (TENANT, uuid.UUID(int=9))
    assert investigation["prepared"] == TENANT
    tenant_id, case_ref, actor, runtime = investigation["investigated"]
    assert (tenant_id, case_ref, actor) == (TENANT, "case_seeded", seed.SEED_ACTOR)
    assert runtime.llm_model == seed.SAMPLE_AGENT_MODEL


@pytest.mark.parametrize(
    ("run", "message"),
    [
        (_run(tenant_id=str(uuid.UUID(int=1))), "another tenant"),
        (_run(application_id="not-a-uuid"), "no application_id"),
    ],
)
async def test_investigate_refuses_a_run_file_it_cannot_use(
    investigation: dict[str, Any], run: dict[str, Any], message: str
) -> None:
    with pytest.raises(SeedError, match=message):
        await seed.investigate(run)
    assert "prepared" not in investigation


async def test_investigate_refuses_an_application_the_desk_opened_no_case_for(investigation: dict[str, Any]) -> None:
    investigation.update(case_ref=None, case_error="policy_unavailable")
    with pytest.raises(SeedError, match="policy_unavailable"):
        await seed.investigate(_run())
    assert "prepared" not in investigation


@pytest.mark.parametrize("runtime_env", ["production", "staging", ""])
def test_both_steps_refuse_a_runtime_that_is_not_development(tmp_path: Path, runtime_env: str) -> None:
    out = tmp_path / "seed.json"
    environ = {**DEV, "AGENTICORG_ENV": runtime_env}
    assert seed.main(["configure", "--output", str(out), "--push-url", "http://grantex:3099/case-push"], environ) == 2
    assert seed.main(["investigate", "--run", str(out)], environ) == 2
    assert not out.exists()


def test_configure_writes_the_values_for_the_suite(calls: list[tuple[str, Any]], tmp_path: Path) -> None:
    out = tmp_path / "results" / "seed.json"
    assert seed.main(["configure", "--output", str(out), "--push-url", "http://grantex:3099/case-push"], DEV) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["tenant_id"] == str(TENANT)
    assert written["push"]["key_id"] == "key-1"


def test_investigate_refuses_a_remote_grantex_before_reading_the_run(tmp_path: Path) -> None:
    run = tmp_path / "run.json"
    run.write_text(json.dumps(_run()), encoding="utf-8")
    environ = {**DEV, "GRANTEX_BASE_URL": "https://api.grantex.dev"}
    assert seed.main(["investigate", "--run", str(run)], environ) == 2
    assert json.loads(run.read_text(encoding="utf-8")) == _run()


def test_investigate_refuses_an_unreadable_run_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert seed.main(["investigate", "--run", str(tmp_path / "missing.json")], DEV) == 2
    assert "cannot read the run file" in capsys.readouterr().err


@pytest.mark.parametrize(("final", "code"), [("awaiting_decision", 0), ("in_progress", 1)])
def test_investigate_records_the_result_and_fails_unless_the_case_awaits_a_decision(
    investigation: dict[str, Any], tmp_path: Path, final: str, code: int
) -> None:
    investigation["final"] = final
    run = tmp_path / "run.json"
    run.write_text(json.dumps(_run(case_ref="case_seeded")), encoding="utf-8")

    assert seed.main(["investigate", "--run", str(run)], DEV) == code

    written = json.loads(run.read_text(encoding="utf-8"))
    assert written["case_ref"] == "case_seeded"
    assert written["investigation"]["state"] == final
    assert written["investigation"]["tier"] == "low"
