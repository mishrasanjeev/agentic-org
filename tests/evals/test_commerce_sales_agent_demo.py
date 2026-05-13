from __future__ import annotations

import json
from pathlib import Path

import pytest

from demos.commerce_sales_agent_demo import format_demo_output, run_demo

EXPECTED_HAPPY_PATH_TOOLS = [
    "grantex_commerce:catalog_search",
    "grantex_commerce:catalog_get_item",
    "grantex_commerce:inventory_check",
    "grantex_commerce:cart_create",
    "grantex_commerce:consent_request",
    "grantex_commerce:consent_exchange",
    "grantex_commerce:payment_create_intent",
    "grantex_commerce:checkout_create",
    "grantex_commerce:payment_get_status",
]

PROVIDER_MARKERS = ("stripe", "plural", "pine", "provider_credentials", "credential_payload")


@pytest.mark.asyncio
async def test_demo_completes_happy_path_with_grantex_aliases_only() -> None:
    result = await run_demo()

    assert result["scope"] == "internal_sandbox_only"
    assert result["audit_summary"]["tool_sequence"] == EXPECTED_HAPPY_PATH_TOOLS
    assert result["audit_summary"]["no_direct_provider_calls"] is True
    assert all(step["tool_alias"].startswith("grantex_commerce:") for step in result["steps"])


@pytest.mark.asyncio
async def test_demo_requires_consent_and_passport_before_payment_intent() -> None:
    result = await run_demo()
    sequence = result["audit_summary"]["tool_sequence"]

    assert sequence.index("grantex_commerce:consent_request") < sequence.index(
        "grantex_commerce:consent_exchange"
    )
    assert sequence.index("grantex_commerce:consent_exchange") < sequence.index(
        "grantex_commerce:payment_create_intent"
    )
    assert sequence.index("grantex_commerce:payment_create_intent") < sequence.index(
        "grantex_commerce:checkout_create"
    )


@pytest.mark.asyncio
async def test_demo_never_exposes_direct_provider_paths_or_credentials() -> None:
    result = await run_demo()
    serialized = json.dumps(result, sort_keys=True).lower()

    assert not any(marker in serialized for marker in PROVIDER_MARKERS)
    assert "eval.passport.jwt" not in serialized
    assert "demo_agent_assertion" not in serialized
    assert result["audit_summary"]["no_provider_credential_handling"] is True
    assert result["audit_summary"]["final_payment_confirmation_user_controlled"] is True


@pytest.mark.asyncio
async def test_demo_negative_scenarios_refuse_or_respond_cautiously() -> None:
    result = await run_demo()
    scenarios = {scenario["name"]: scenario for scenario in result["negative_scenarios"]}

    assert scenarios["missing_consent_checkout"]["behavior"] == "refused"
    assert scenarios["missing_consent_checkout"]["error"] == "consent_required"
    assert scenarios["unsupported_emi_discount"]["behavior"] == "refused"
    assert scenarios["unsupported_emi_discount"]["error"] == "unsupported_commerce_claim"
    assert scenarios["stale_inventory"]["behavior"] == "cautious"
    assert "guarantee availability" in scenarios["stale_inventory"]["message"].lower()
    assert scenarios["stale_inventory"]["tool_sequence"] == ["grantex_commerce:inventory_check"]
    assert scenarios["unknown_inventory"]["behavior"] == "cautious"
    assert scenarios["unknown_inventory"]["tool_sequence"] == ["grantex_commerce:inventory_check"]


@pytest.mark.asyncio
async def test_demo_output_is_readable_and_mentions_core_steps() -> None:
    output = format_demo_output(await run_demo())

    assert "Product discovery" in output
    assert "Consent request" in output
    assert "Passport exchange" in output
    assert "Checkout handoff" in output
    assert "Payment status polling" in output
    assert "Negative mini-demo" in output


def test_demo_doc_states_internal_sandbox_and_pr_dependency() -> None:
    doc = Path("docs/commerce-agent-demo.md").read_text(encoding="utf-8").lower()

    assert "internal sandbox only" in doc
    assert "pr #326" in doc
    assert "no direct stripe, plural, pine" in doc
    assert "no provider credential handling" in doc
