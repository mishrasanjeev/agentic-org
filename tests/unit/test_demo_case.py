# SPDX-License-Identifier: Apache-2.0
"""The governed-case demo (scripts/demo_case.py), without a database, Grantex or Docker:
every step runs against fakes of the runtime, the evidence service, the audit log and the
``grantex-evidence`` CLI, and the script fails at the first step whose outcome is not the
expected one."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from core.cases import evidence as case_evidence
from core.cases import runtime as case_runtime
from core.cases import store as case_store
from core.cases.evidence import ExportedEvidence
from core.config import external_keys, settings
from core.tool_gateway.provider_gateway import ToolDecision
from scripts import demo_case

ENV = {"AGENTICORG_ENV": "development", "GRANTEX_BASE_URL": "http://grantex:3001", "GRANTEX_API_KEY": "placeholder-key"}
CASE_REF = "case_" + "a" * 24
RUN_ID = "tenant:t:case:" + CASE_REF + ":underwriter:x"
GRANT = "grnt_run"
ROOT = "sha256:" + "f" * 64
ANCHOR = "e" * 64
STARTED = "2026-10-01T10:00:00.123456+00:00"
# Captured before any test patches httpx.AsyncClient (the demo module shares the httpx module).
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _jwt(claims: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.sig"


def _case() -> SimpleNamespace:
    calls = [
        {
            "sequence": 1,
            "tool": "resolve_business",
            "provider": "mock",
            "outcome": "ok",
            "record_ids": ["r1"],
            "started_at": STARTED,
            "grant_id": GRANT,
        },
        {
            "sequence": 2,
            "tool": "ownership",
            "provider": "mock",
            "outcome": "ok",
            "record_ids": ["r2", "r3"],
            "started_at": STARTED,
            "grant_id": GRANT,
        },
    ]
    return SimpleNamespace(case_ref=CASE_REF, agent_records=[{"run_id": RUN_ID, "tool_calls": calls}])


class _Authorizer:
    async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
        assert (connector, tool) == ("mock", demo_case.OUT_OF_SCOPE_TOOL)
        return ToolDecision(
            allowed=False, reason="tool_not_granted", sub_reason="tool_scope_missing", grant_id="grnt_s"
        )


class _Runtime:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.roles: list[str] = []

    def session_factory(self, _tenant: uuid.UUID) -> Any:
        @asynccontextmanager
        async def _open() -> Any:
            yield object()

        return _open()

    def clock(self) -> datetime:
        return datetime.now(UTC)

    def authorizer_for(self, tenant: str, case_ref: str, role: str, purpose: str) -> _Authorizer:
        self.roles.append(role)
        return _Authorizer()


class _Service:
    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.exported: list[str] = []
        self.entries = entries

    async def export(self, case_ref: str) -> ExportedEvidence:
        self.exported.append(case_ref)
        entries = (
            self.entries
            if self.entries is not None
            else [
                {"type": "grant", "at": "2026-10-01T09:00:00.000Z"},
                {"type": "tool_call", "at": "2026-10-01T10:00:00.123Z"},
                {"type": "tool_call", "at": "2026-10-01T10:00:00.123Z"},
            ]
        )
        package = {"format": "grantex-evidence-package", "case": {"state": "open"}, "entries": entries}
        return ExportedEvidence(data=json.dumps(package).encode(), root=ROOT, anchor_hash=ANCHOR)


def _http_factory(audit_root: str = ROOT, audit_hash: str = ANCHOR) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audit/entries":
            assert request.url.params["action"] == "evidence.package_exported"
            assert request.headers["authorization"] == "Bearer placeholder-key"
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            "entryId": "alog_other",
                            "metadata": {"case_id": "case_other", "package_root": "x"},
                            "hash": "y",
                        },
                        {
                            "entryId": "alog_1",
                            "metadata": {"case_id": CASE_REF, "package_root": audit_root},
                            "hash": audit_hash,
                        },
                    ]
                },
            )
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": [{"kty": "EC", "kid": "k1"}]})
        return httpx.Response(404)

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handle), base_url=kwargs.get("base_url", ""))

    return factory


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    runtime_holder: dict[str, Any] = {}
    service = _Service()

    def make_runtime(**kwargs: Any) -> _Runtime:
        runtime_holder["runtime"] = _Runtime(**kwargs)
        return runtime_holder["runtime"]

    async def prepare(_tenant: uuid.UUID) -> dict[str, str]:
        external_keys.grantex_root_grant_token = _jwt(
            {"grnt": "grnt_root", "purpose": "aml.cdd.onboarding", "scope": "a b c", "exp": 1}
        )
        return {"business_underwriter": "agent-a", "screening_disposition": "agent-b"}

    async def enable(_session: Any, _tenant: uuid.UUID) -> None:
        return None

    async def create(_session: Any, **kwargs: Any) -> SimpleNamespace:
        runtime_holder["created"] = kwargs
        return _case()

    async def get(_session: Any, _tenant: uuid.UUID, _case_ref: str, **_kw: Any) -> SimpleNamespace:
        return _case()

    async def investigate(_tenant: uuid.UUID, case_ref: str, *, runtime: Any, actor: str) -> dict[str, Any]:
        runtime_holder["investigated"] = (case_ref, actor)
        return {"case_ref": case_ref, "state": "awaiting_decision", "recommendation": "approve", "tier": "low"}

    monkeypatch.setattr(settings, "grants_enforce_closed", "deny")
    monkeypatch.setattr(settings, "case_evidence_service", "grantex")
    monkeypatch.setattr(demo_case, "prepare_case_agents", prepare)
    monkeypatch.setattr(demo_case, "enable_flag", enable)
    monkeypatch.setattr(case_runtime, "CaseRuntime", make_runtime)
    monkeypatch.setattr(case_runtime, "investigate_case", investigate)
    monkeypatch.setattr(case_store, "create_case", create)
    monkeypatch.setattr(case_store, "get_case", get)
    monkeypatch.setattr(case_evidence, "GrantexEvidenceService", _Service)
    monkeypatch.setattr(case_evidence, "evidence_service", lambda: service)
    monkeypatch.setattr(demo_case.httpx, "AsyncClient", _http_factory())
    runs: list[list[str]] = []

    def run(command: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        runs.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=f"verified: 3 entries, root {ROOT}\n", stderr="")

    monkeypatch.setattr(demo_case.subprocess, "run", run)
    return {"holder": runtime_holder, "service": service, "runs": runs}


def test_the_demo_runs_every_step_and_verifies_the_package(
    world: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert demo_case.main(["--output", str(tmp_path)], environ=ENV) == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("[")]
    titles = [re.sub(r"^\[\d+\] (.*?)\s{2,}(live|fixture).*$", r"", line) for line in lines]
    assert titles == [
        "root grant issued (purpose, tools, caps)",
        "case agent ready: business_underwriter",
        "case agent ready: screening_disposition",
        "case submitted",
        "underwriter run completed",
        "run grant delegated from the root grant",
        "provider call authorised: resolve_business",
        "provider call authorised: ownership",
        f"out-of-scope call denied: {demo_case.OUT_OF_SCOPE_ROLE} -> {demo_case.OUT_OF_SCOPE_TOOL}",
        "evidence package exported",
        "root and anchor read from the tenant audit log",
        "package verified with grantex-evidence",
    ]
    assert "fixture" in lines[4] and all("live" in line for line in lines[:4] + lines[5:])
    assert "reason=tool_not_granted sub_reason=tool_scope_missing" in lines[8]
    assert out.rstrip().endswith("demo-case: PASS")

    holder = world["holder"]
    assert (
        holder["created"]["purpose"] == "aml.cdd.onboarding" and holder["created"]["created_by"] == demo_case.DEMO_ACTOR
    )
    assert holder["investigated"] == (CASE_REF, demo_case.DEMO_ACTOR)
    assert holder["runtime"].kwargs["evidence_service"] is case_evidence.evidence_service
    assert holder["runtime"].roles == [demo_case.OUT_OF_SCOPE_ROLE]
    assert world["service"].exported == [CASE_REF]

    (command,) = world["runs"]
    assert command[1:5] == ["-m", "grantex.cli", "evidence", "verify"]
    assert command[command.index("--root") + 1] == ROOT and command[command.index("--anchor") + 1] == ANCHOR
    assert "--require-anchor" in command and "--require-signature" in command
    assert (tmp_path / f"{CASE_REF}.evidence.json").exists() and (tmp_path / "grantex-jwks.json").exists()
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["case_ref"] == CASE_REF and summary["run_grant"] == GRANT and summary["root"] == ROOT
    assert summary["denied"] == {"reason": "tool_not_granted", "sub_reason": "tool_scope_missing"}
    assert len(summary["steps"]) == 12


def test_the_demo_refuses_to_run_without_deny_mode_or_the_sink(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(settings, "grants_enforce_closed", "warn")
    assert demo_case.main(["--output", str(tmp_path)], environ=ENV) == 1
    assert "AGENTICORG_GRANTS_ENFORCE_CLOSED=deny" in capsys.readouterr().err
    monkeypatch.setattr(settings, "grants_enforce_closed", "deny")
    monkeypatch.setattr(settings, "case_evidence_service", "off")
    assert demo_case.main(["--output", str(tmp_path)], environ=ENV) == 1
    assert "AGENTICORG_CASE_EVIDENCE_SERVICE=grantex" in capsys.readouterr().err


def test_the_demo_fails_when_the_audit_log_disagrees_with_the_export(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(demo_case.httpx, "AsyncClient", _http_factory(audit_root="sha256:" + "0" * 64))
    assert demo_case.main(["--output", str(tmp_path)], environ=ENV) == 1
    assert "package root differs" in capsys.readouterr().err


def test_the_demo_fails_when_the_package_lacks_the_runs_calls(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    short = _Service(entries=[{"type": "grant", "at": "x"}, {"type": "tool_call", "at": "2026-10-01T10:00:00.123Z"}])
    monkeypatch.setattr(case_evidence, "evidence_service", lambda: short)
    assert demo_case.main(["--output", str(tmp_path)], environ=ENV) == 1
    assert "the package holds 1 tool calls, the run made 2" in capsys.readouterr().err


def test_the_demo_fails_when_the_cli_does_not_verify(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing(command: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="FAILED entry_hash_mismatch: entry 2\n", stderr="")

    monkeypatch.setattr(demo_case.subprocess, "run", failing)
    assert demo_case.main(["--output", str(tmp_path)], environ=ENV) == 1
    assert "grantex-evidence verify exited 1" in capsys.readouterr().err


def test_the_demo_fails_when_the_out_of_scope_call_is_allowed(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def allow(self: Any, *, connector: str, tool: str) -> ToolDecision:
        return ToolDecision(allowed=True, grant_id="grnt_s")

    monkeypatch.setattr(_Authorizer, "authorize", allow)
    assert demo_case.main(["--output", str(tmp_path)], environ=ENV) == 1
    assert "was allowed ownership" in capsys.readouterr().err


def test_the_demo_refuses_outside_a_development_runtime(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert demo_case.main(["--output", str(tmp_path)], environ={**ENV, "AGENTICORG_ENV": "production"}) == 1
    assert "demo-case: FAIL" in capsys.readouterr().err
    assert demo_case._jwt_claims("not-a-jwt") == {}
