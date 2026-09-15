# SPDX-License-Identifier: Apache-2.0
"""HITL conditions are checked against the grammar when they are saved.

PRD §7 F-4 remainder: an unparseable condition used to be accepted by the
agent and SOP APIs and then trigger human review on every run. With
``AGENTICORG_HITL_CONDITION_VALIDATION=reject`` the save answers 422 with the
parse reason; ``warn`` logs and counts instead; ``off`` (default) is unchanged.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from fastapi import HTTPException
from prometheus_client import REGISTRY

from core.config import Settings, settings
from core.langgraph.hitl_condition import (
    evaluate_hitl_condition,
    screen_condition_on_save,
    validate_hitl_condition,
)

TENANT = "00000000-0000-0000-0000-000000000001"
METRIC = "agenticorg_hitl_condition_parse_failures_total"
PACKS_DIR = Path(__file__).resolve().parents[2] / "core" / "agents" / "packs"


def _count(stage: str, reason: str, outcome: str) -> float:
    value = REGISTRY.get_sample_value(METRIC, {"stage": stage, "reason": reason, "outcome": outcome})
    return value or 0.0


@pytest.fixture
def mode(monkeypatch):
    def _set(value: str) -> None:
        monkeypatch.setattr(settings, "hitl_condition_validation", value)

    return _set


# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------

VALID = [
    "",
    "   ",
    "confidence < 0.88",
    "total > 500000 OR einvoice_failed==true",
    "amount > 100 AND status == 'mismatch'",
    "NOT (amount > 100)",
    "not amount > 100 or plan in ['enterprise', 'pro']",
    "summary.risk_score < 40",
    "items[0] >= -1",
    "0 < amount <= 10",
    "region not in ('eu', 'uk')",
    "needs_review == True",
    "always",
    "always_before_filing",
    "ALWAYS_BEFORE_DISPOSITION",
]

INVALID = [
    ("high_value_procurement", "not_a_comparison"),
    ("amount > 100 OR needs_review", "not_a_comparison"),
    ("NOT fraud_indicator", "not_a_comparison"),
    ("amount >> ??", "syntax_error"),
    ("invoice amount exceeds five lakh", "syntax_error"),
    ("amount > 100 OR", "syntax_error"),
    ("amount = 100", "syntax_error"),
    ("len(items) > 1", "unsupported_syntax"),
    ("__import__('os') == 1", "unsupported_syntax"),
    ("amount > 5 * 2", "unsupported_syntax"),
    ("(x := 1) == 1", "unsupported_syntax"),
    ("status is None", "unsupported_operator"),
    ("always check big invoices", "syntax_error"),
    ("(" * 500 + "a > 1" + ")" * 500, "syntax_error"),
]


@pytest.mark.parametrize("condition", VALID)
def test_valid_conditions_parse(condition):
    result = validate_hitl_condition(condition)
    assert result.ok, result.detail


def test_none_is_no_condition():
    assert validate_hitl_condition(None).ok


@pytest.mark.parametrize(("condition", "reason"), INVALID)
def test_unparseable_conditions_return_a_reason_code(condition, reason):
    result = validate_hitl_condition(condition)
    assert not result.ok
    assert result.reason == reason
    assert result.detail


def test_non_string_condition_is_refused():
    result = validate_hitl_condition({"amount": 5})
    assert (result.ok, result.reason) == (False, "syntax_error")


def test_bare_label_reason_says_how_to_fix_it():
    detail = validate_hitl_condition("high_value_procurement").detail
    assert "high_value_procurement == True" in detail


@pytest.mark.parametrize(("condition", "_reason"), INVALID)
def test_every_condition_refused_at_save_still_fails_closed_at_run(condition, _reason):
    """Save-time refusal never disagrees with the runtime in the unsafe
    direction: whatever is refused would have triggered review anyway."""
    triggered, _ = evaluate_hitl_condition(condition, {"amount": 1, "status": "ok", "items": [1]})
    assert triggered


# ---------------------------------------------------------------------------
# Modes and metric
# ---------------------------------------------------------------------------


def test_off_mode_accepts_and_does_not_count():
    before = _count("save", "not_a_comparison", "warned") + _count("save", "not_a_comparison", "rejected")
    assert screen_condition_on_save("high_value_procurement", mode="off", surface="test") is None
    after = _count("save", "not_a_comparison", "warned") + _count("save", "not_a_comparison", "rejected")
    assert after == before


def test_warn_mode_accepts_logs_and_counts():
    before = _count("save", "syntax_error", "warned")
    with patch("core.langgraph.hitl_condition.logger") as log:
        assert screen_condition_on_save("amount >> ??", mode="warn", surface="test") is None
    assert _count("save", "syntax_error", "warned") == before + 1
    log.warning.assert_called_once()
    assert log.warning.call_args.args[0] == "hitl_condition_unparseable_on_save"
    assert log.warning.call_args.kwargs["reason"] == "syntax_error"


def test_reject_mode_returns_the_failure_and_counts():
    before = _count("save", "not_a_comparison", "rejected")
    failure = screen_condition_on_save("high_value_procurement", mode="reject", surface="test")
    assert failure is not None and failure.reason == "not_a_comparison"
    assert _count("save", "not_a_comparison", "rejected") == before + 1


def test_reject_mode_accepts_valid_conditions_without_counting():
    before = _count("save", "syntax_error", "rejected")
    assert screen_condition_on_save("amount > 100 OR confidence < 0.9", mode="reject", surface="test") is None
    assert _count("save", "syntax_error", "rejected") == before


def test_unknown_mode_fails_closed():
    failure = screen_condition_on_save("confidence < 0.88", mode="enforce", surface="test")
    assert failure is not None and failure.reason == "invalid_mode"


def test_setting_defaults_off_and_refuses_unknown_values(monkeypatch):
    monkeypatch.delenv("AGENTICORG_HITL_CONDITION_VALIDATION", raising=False)
    assert Settings(_env_file=None).hitl_condition_validation == "off"
    monkeypatch.setenv("AGENTICORG_HITL_CONDITION_VALIDATION", "warn")
    assert Settings(_env_file=None).hitl_condition_validation == "warn"
    monkeypatch.setenv("AGENTICORG_HITL_CONDITION_VALIDATION", "block")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_runtime_parse_failure_is_counted_and_still_triggers():
    before = _count("run", "not_a_comparison", "fail_closed")
    triggered, reason = evaluate_hitl_condition("high_value_procurement", {"total_po_value": 10})
    assert triggered and "fail closed" in reason
    assert _count("run", "not_a_comparison", "fail_closed") == before + 1


def test_runtime_missing_field_is_not_a_parse_failure():
    reasons = ("syntax_error", "unsupported_syntax", "unsupported_operator", "not_a_comparison")
    before = sum(_count("run", r, "fail_closed") for r in reasons)
    triggered, _ = evaluate_hitl_condition("amount > 5", {"other": 1})
    assert triggered
    assert sum(_count("run", r, "fail_closed") for r in reasons) == before


# ---------------------------------------------------------------------------
# Save surfaces
# ---------------------------------------------------------------------------


def _unreachable_session(*_a, **_k):
    raise AssertionError("the save must be refused before touching the database")


class _ReachedDatabaseError(Exception):
    """Raised by the session double to prove validation let the save through."""


def _reached_session(*_a, **_k):
    raise _ReachedDatabaseError


@pytest.mark.asyncio
async def test_create_agent_rejects_unparseable_condition_with_422_and_reason(mode):
    from api.v1.agents import create_agent
    from core.schemas.api import AgentCreate

    mode("reject")
    body = AgentCreate(
        name="n", agent_type="ap_processor", domain="finance", hitl_policy={"condition": "high_value_procurement"}
    )
    with patch("api.v1.agents.get_tenant_session", side_effect=_unreachable_session):
        with pytest.raises(HTTPException) as exc:
            await create_agent(body=body, tenant_id=TENANT)
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "invalid_hitl_condition"
    assert exc.value.detail["reason"] == "not_a_comparison"
    assert "high_value_procurement" in exc.value.detail["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["off", "warn"])
async def test_create_agent_accepts_unparseable_condition_when_not_rejecting(mode, flag):
    from api.v1.agents import create_agent
    from core.schemas.api import AgentCreate

    mode(flag)
    body = AgentCreate(
        name="n", agent_type="ap_processor", domain="finance", hitl_policy={"condition": "high_value_procurement"}
    )
    with patch("api.v1.agents.get_tenant_session", side_effect=_reached_session):
        with pytest.raises(_ReachedDatabaseError):
            await create_agent(body=body, tenant_id=TENANT)


@pytest.mark.asyncio
async def test_create_agent_accepts_valid_condition_in_reject_mode(mode):
    from api.v1.agents import create_agent
    from core.schemas.api import AgentCreate

    mode("reject")
    body = AgentCreate(
        name="n", agent_type="ap_processor", domain="finance", hitl_policy={"condition": "amount > 500000"}
    )
    with patch("api.v1.agents.get_tenant_session", side_effect=_reached_session):
        with pytest.raises(_ReachedDatabaseError):
            await create_agent(body=body, tenant_id=TENANT)


@pytest.mark.asyncio
async def test_replace_agent_rejects_unparseable_condition(mode):
    from api.v1.agents import replace_agent
    from core.schemas.api import AgentCreate

    mode("reject")
    body = AgentCreate(name="n", agent_type="ap_processor", domain="finance", hitl_policy={"condition": "amount >> ??"})
    with patch("api.v1.agents.get_tenant_session", side_effect=_unreachable_session):
        with pytest.raises(HTTPException) as exc:
            await replace_agent(agent_id=uuid.uuid4(), body=body, tenant_id=TENANT)
    assert exc.value.status_code == 422
    assert exc.value.detail["reason"] == "syntax_error"


@pytest.mark.asyncio
async def test_replace_agent_without_hitl_policy_is_not_screened(mode):
    from api.v1.agents import replace_agent
    from core.schemas.api import AgentCreate

    mode("reject")
    body = AgentCreate(name="n", agent_type="ap_processor", domain="finance")
    with patch("api.v1.agents.get_tenant_session", side_effect=_reached_session):
        with pytest.raises(_ReachedDatabaseError):
            await replace_agent(agent_id=uuid.uuid4(), body=body, tenant_id=TENANT)


@pytest.mark.asyncio
async def test_update_agent_rejects_unparseable_condition_and_leaves_agent_unchanged(mode):
    from api.v1.agents import update_agent
    from core.schemas.api import AgentUpdate

    mode("reject")
    agent = MagicMock()
    agent.status = "shadow"
    agent.domain = "finance"
    agent.hitl_condition = "amount > 500000"
    result = MagicMock()
    result.scalar_one_or_none.return_value = agent
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    body = AgentUpdate(hitl_policy={"condition": "len(items) > 1"})
    with patch("api.v1.agents.get_tenant_session", return_value=ctx):
        with pytest.raises(HTTPException) as exc:
            await update_agent(agent_id=uuid.uuid4(), body=body, tenant_id=TENANT, user_domains=None, user={})
    assert exc.value.status_code == 422
    assert exc.value.detail["reason"] == "unsupported_syntax"
    assert agent.hitl_condition == "amount > 500000"


@pytest.mark.asyncio
async def test_sop_deploy_rejects_unparseable_generated_condition(mode):
    """The SOP parser emits free-text conditions that are OR-joined into one
    expression; an unparseable one must be refused, not deployed."""
    from api.v1.sop import deploy_sop_agent

    mode("reject")
    config = {
        "agent_name": "Invoice Processor",
        "agent_type": "ap_processor",
        "domain": "finance",
        "hitl_conditions": ["amount > 500000", "invoice is from a new vendor"],
    }
    with patch("api.v1.agents.get_tenant_session", side_effect=_unreachable_session):
        with pytest.raises(HTTPException) as exc:
            await deploy_sop_agent(body={"config": config}, tenant_id=TENANT, user_domains=None, caller=None)
    assert exc.value.status_code == 422
    assert exc.value.detail["reason"] == "syntax_error"


@pytest.mark.asyncio
async def test_generate_deploy_rejects_unparseable_generated_condition(mode):
    from api.v1.agents import generate_agent

    mode("reject")
    suggestion = {
        "employee_name": "Generated",
        "agent_type": "ap_processor",
        "domain": "finance",
        "system_prompt": "You are generated.",
        "hitl_condition": "high value invoices",
    }
    gen = AsyncMock(return_value={"suggestions": [suggestion]})
    with (
        patch("core.agent_generator.generate_agent_config", gen),
        patch("api.v1.agents.get_tenant_session", side_effect=_unreachable_session),
    ):
        with pytest.raises(HTTPException) as exc:
            await generate_agent(
                body={"description": "process supplier invoices", "deploy": True},
                tenant_id=TENANT,
                user_domains=None,
                caller=None,
            )
    assert exc.value.status_code == 422
    assert exc.value.detail["reason"] == "syntax_error"


# ---------------------------------------------------------------------------
# Shipped packs
# ---------------------------------------------------------------------------


def _shipped_pack_conditions() -> list[tuple[str, str]]:
    from core.agents.packs.ca import CA_PACK
    from core.agents.packs.insurance import INSURANCE_PACK

    found: list[tuple[str, str]] = []
    for config in sorted(PACKS_DIR.glob("*/config.yaml")):
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        for agent in data.get("agents", []):
            if isinstance(agent, dict) and agent.get("hitl_condition"):
                found.append((f"{config.parent.name}/{agent.get('type')}", agent["hitl_condition"]))
    for pack in (CA_PACK, INSURANCE_PACK):
        for agent in pack.get("agents", []):
            if agent.get("hitl_condition"):
                found.append((f"{pack['id']}:{agent.get('name')}", agent["hitl_condition"]))
    return found


def test_shipped_packs_have_hitl_conditions_to_check():
    assert len(_shipped_pack_conditions()) >= 15


@pytest.mark.parametrize(("where", "condition"), _shipped_pack_conditions())
def test_shipped_pack_conditions_parse(where, condition):
    result = validate_hitl_condition(condition)
    assert result.ok, f"{where}: {result.reason} {result.detail}"


PACK_OUTPUT_CASES = [
    (
        "insurance/underwriting_analyst",
        {"underwriting_summary": {"authority_status": "within", "recommendation": "bind", "risk_score": 72}},
        {"underwriting_summary": {"authority_status": "referral", "recommendation": "bind", "risk_score": 72}},
    ),
    (
        "insurance/claims_adjudicator",
        {
            "claims_summary": {
                "loss_amount": 900,
                "reserve": 1000,
                "fraud_score": 10,
                "coverage_verified": True,
            }
        },
        {
            "claims_summary": {
                "loss_amount": 900,
                "reserve": 1000,
                "fraud_score": 75,
                "coverage_verified": True,
            }
        },
    ),
    (
        "insurance/policy_manager",
        {"policy_summary": {"action": "renewal"}},
        {"policy_summary": {"action": "cancellation"}},
    ),
    (
        "manufacturing/supply_chain_optimizer",
        {"supply_chain_summary": {"total_po_value": 12000, "vendor_flags": 0}},
        {"supply_chain_summary": {"total_po_value": 45000, "vendor_flags": 0}},
    ),
]


@pytest.mark.parametrize(("where", "routine", "risky"), PACK_OUTPUT_CASES)
def test_fixed_pack_conditions_trigger_on_risky_output_only(where, routine, risky):
    """The former bare labels triggered on every run; the replacements are
    real expressions over the output keys each prompt's output_format names."""
    condition = dict(_shipped_pack_conditions())[where]
    assert evaluate_hitl_condition(condition, routine) == (False, "")
    assert evaluate_hitl_condition(condition, risky)[0]
    assert evaluate_hitl_condition(condition, {})[0], "missing summary must fail closed"
