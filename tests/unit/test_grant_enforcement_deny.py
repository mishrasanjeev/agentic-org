# SPDX-License-Identifier: Apache-2.0
"""PRD F-1c — deny mode can be switched on and its denials are visible.

Acceptance criteria covered here:

* a tenant ``deny`` flag (or a ``deny`` deployment default) now resolves to
  ``deny``;
* in deny a refused tool call stops the run: the graph reports ``failed`` with
  the reason code, the runner's result carries ``grant_denial`` and
  ``POST /agents/{id}/run`` writes it to the audit row and the response;
* ``off`` and ``warn`` runs never carry ``grant_denial``;
* the per-tenant report counts would-deny and denied events by reason and by
  call, from raw JSON log lines or Cloud Logging entries.
"""

from __future__ import annotations

import inspect
import io
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from auth import grant_enforcement as ge
from auth.grant_enforcement import EnforcementMode, resolve_enforcement_mode
from auth.run_grants import RunGrant
from core.langgraph.agent_graph import build_agent_graph
from core.test_doubles.scripted_model import final, tool_call

TENANT = str(uuid.UUID(int=0x1F1E))
OTHER_TENANT = str(uuid.UUID(int=0x1F1F))
PLACEHOLDER_TOKEN = "placeholder-grant-token"  # noqa: S105 - not a credential


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    ge.clear_mode_cache()
    yield
    ge.clear_mode_cache()


def _flags(**enabled: bool):
    values = {ge.FLAG_WARN: enabled.get("warn", False), ge.FLAG_DENY: enabled.get("deny", False)}

    async def _strict(flag_key: str, **_: Any) -> bool:
        return values[flag_key]

    return _strict


# ── Deny can be switched on ──────────────────────────────────────────────


async def test_tenant_deny_flag_switches_the_tenant_to_deny(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    with patch("core.feature_flags.is_enabled_strict", _flags(deny=True)):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.DENY


async def test_deny_deployment_default_applies_to_every_tenant(monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "deny")
    with patch("core.feature_flags.is_enabled_strict", _flags()):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.DENY
    assert await resolve_enforcement_mode("") is EnforcementMode.DENY


# ── The denial reaches the run result ────────────────────────────────────


def _state(grant_token: str = "") -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="send the reminder")],
        "agent_id": "agent-f1c",
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": TENANT,
        "grant_token": grant_token,
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


async def _graph_run(run_grant: RunGrant, steps: list[Any], scripted_model: Any):
    scripted_model(steps)
    executed = AsyncMock(return_value={"id": "msg-1", "status": "sent"})
    client = MagicMock()
    client.enforce.return_value = MagicMock(
        allowed=False, reason="No scope grants access to connector 'gmail'.", grant_id="grnt_placeholder"
    )
    with (
        patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed),
        patch("core.langgraph.agent_graph.get_grantex_client", return_value=client),
    ):
        graph = build_agent_graph(
            system_prompt="scripted",
            authorized_tools=["gmail:send_email"],
            connector_config={},
            connector_names=["gmail"],
            confidence_floor=0.0,
            run_grant=run_grant,
        )
        result = await graph.compile().ainvoke(_state(run_grant.token))
    return result, executed


async def test_deny_mode_run_fails_with_the_reason_code(scripted_model):
    result, executed = await _graph_run(
        RunGrant(mode=EnforcementMode.DENY, token=PLACEHOLDER_TOKEN, source="minted"),
        [tool_call("gmail__send_email", to="ap@example.com")],
        scripted_model,
    )
    assert executed.await_count == 0
    assert result["status"] == "failed"
    assert result["error"] == "grant_denied: tool_not_granted"
    assert result["grant_denial"] == {
        "reason": "tool_not_granted",
        "sub_reason": "",
        "grant_id": "grnt_placeholder",
        "connector": "gmail",
        "tool": "send_email",
    }


async def test_warn_mode_run_carries_no_denial(scripted_model):
    result, executed = await _graph_run(
        RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="minted"),
        [tool_call("gmail__send_email", to="ap@example.com"), final({"status": "completed", "confidence": 0.95})],
        scripted_model,
    )
    assert executed.await_count == 1
    assert result["status"] == "completed"
    assert "grant_denial" not in result


async def test_runner_result_carries_the_grant_denial():
    from core.langgraph import runner

    denial = {"reason": "grant_missing", "sub_reason": "mint_failed", "grant_id": "", "connector": "gmail", "tool": "x"}

    class _Compiled:
        async def ainvoke(self, state, config=None):
            return {"status": "failed", "error": "grant_denied: grant_missing", "grant_denial": denial, "messages": []}

    graph = MagicMock()
    graph.compile.return_value = _Compiled()
    with (
        patch("core.billing.metering.gate_agent_run", AsyncMock(return_value=None)),
        patch("core.billing.metering.meter_agent_run", AsyncMock()),
        patch("core.database.get_tenant_session", MagicMock(side_effect=RuntimeError("no database in unit tests"))),
        patch.object(runner, "build_agent_graph", MagicMock(return_value=graph)),
        patch.object(runner, "prefetch_llm_credential", AsyncMock(return_value=None)),
        patch.object(runner, "generate_explanation", AsyncMock(return_value={})),
    ):
        result = await runner.run_agent(
            agent_id=str(uuid.UUID(int=0xA6E)),
            agent_type="analyst",
            domain="ops",
            tenant_id=TENANT,
            system_prompt="scripted",
            authorized_tools=[],
            task_input={"action": "process"},
            run_grant=RunGrant(mode=EnforcementMode.DENY, source="none", missing_sub_reason="mint_failed"),
        )
    assert result["status"] == "failed"
    assert result["grant_denial"] == denial


def test_run_endpoint_writes_the_denial_to_audit_and_response():
    from api.v1 import agents

    src = inspect.getsource(agents.run_agent)
    audit = src[src.index("audit_entry = AuditLog(") : src.index("session.add(audit_entry)")]
    assert '"grant_denial"' in audit
    assert 'response["grant_denial"] = lg_result["grant_denial"]' in src


# ── Per-tenant report ────────────────────────────────────────────────────


def _load_report_module():
    import importlib

    return importlib.import_module("scripts.grant_enforcement_report")


def _event(event: str, tenant: str, reason: str, tool: str = "send_email", ts: str = "2026-09-15T08:00:00Z") -> dict:
    return {
        "event": event,
        "tenant_id": tenant,
        "reason": reason,
        "connector": "gmail",
        "tool": tool,
        "agent_type": "analyst",
        "timestamp": ts,
        "level": "warning",
    }


def test_report_counts_would_deny_and_denied_per_tenant_by_reason_and_call():
    report = _load_report_module()
    lines = [
        json.dumps(_event("grant_enforcement_would_deny", TENANT, "grant_missing", ts="2026-09-14T00:00:00Z")),
        json.dumps(_event("grant_enforcement_would_deny", TENANT, "grant_missing")),
        json.dumps(_event("grant_enforcement_would_deny", TENANT, "tool_not_granted", tool="list_threads")),
        json.dumps({"jsonPayload": _event("grant_enforcement_denied", OTHER_TENANT, "token_invalid")}),
        json.dumps({"event": "agent_run_completed", "tenant_id": TENANT}),
        "not json",
    ]
    reports, skipped = report.build_report(report._entries(io.StringIO("\n".join(lines))))

    assert skipped == 2
    assert (reports[TENANT].would_deny, reports[TENANT].denied) == (3, 0)
    assert reports[TENANT].by_reason[("would_deny", "grant_missing")] == 2
    assert reports[TENANT].by_call[("would_deny", "tool_not_granted", "gmail", "list_threads", "analyst")] == 1
    assert (reports[TENANT].first_seen, reports[TENANT].last_seen) == ("2026-09-14T00:00:00Z", "2026-09-15T08:00:00Z")
    assert (reports[OTHER_TENANT].would_deny, reports[OTHER_TENANT].denied) == (0, 1)


def test_report_filters_one_tenant_and_reads_a_cloud_logging_json_array(capsys, tmp_path):
    report = _load_report_module()
    export = tmp_path / "export.json"
    export.write_text(
        json.dumps(
            [
                {"jsonPayload": _event("grant_enforcement_would_deny", TENANT, "grant_missing")},
                {"jsonPayload": _event("grant_enforcement_would_deny", OTHER_TENANT, "grant_missing")},
            ]
        ),
        encoding="utf-8",
    )
    assert report.main([str(export), "--tenant", TENANT, "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert list(out["tenants"]) == [TENANT]
    assert out["tenants"][TENANT]["would_deny"] == 1
    assert out["tenants"][TENANT]["by_reason"] == [{"count": 1, "outcome": "would_deny", "reason": "grant_missing"}]


def test_report_text_output_names_each_tenant(capsys, tmp_path):
    report = _load_report_module()
    log = tmp_path / "api.log"
    log.write_text(
        json.dumps(_event("grant_enforcement_denied", TENANT, "permission_insufficient")) + "\n", encoding="utf-8"
    )
    assert report.main([str(log)]) == 0
    out = capsys.readouterr().out
    assert f"tenant {TENANT}: would_deny=0 denied=1" in out
    assert "permission_insufficient" in out
