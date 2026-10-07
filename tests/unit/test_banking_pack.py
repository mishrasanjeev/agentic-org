# SPDX-License-Identifier: Apache-2.0
"""The banking pack: five agent templates, each held to a review condition and a confidence floor."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from core.agents.packs.installer import get_pack_detail, install_pack, list_packs, uninstall_pack

PACK_DIR = Path(__file__).resolve().parents[2] / "core" / "agents" / "packs" / "banking"
EXPECTED_TYPES = {
    "loan_underwriting_analyst",
    "kyc_reviewer",
    "collections_agent",
    "complaint_handler",
    "bank_reconciliation_analyst",
}


def test_the_banking_pack_is_discovered_and_installable():
    packs = {pack["name"]: pack for pack in list_packs()}
    assert "banking" in packs
    pack = packs["banking"]
    assert pack["installable"] is True and pack["install_disabled_reason"] == ""
    assert {agent["type"] for agent in pack["agents"]} == EXPECTED_TYPES
    assert {"KYC_AML", "fair_practices_code", "grievance_redressal"} <= set(pack["compliance"])


def test_every_template_is_held_to_a_review_condition_and_a_floor():
    config = yaml.safe_load((PACK_DIR / "config.yaml").read_text(encoding="utf-8"))
    for agent in config["agents"]:
        assert agent["hitl_condition"].strip(), agent["type"]
        assert Decimal(str(agent["confidence_floor"])) >= Decimal("0.85"), agent["type"]
        assert agent["tools"], agent["type"]
        prompt = PACK_DIR / agent["prompt_file"]
        assert prompt.exists(), agent["type"]
        text = prompt.read_text(encoding="utf-8")
        # Each prompt names its tools, returns one JSON object and leaves the decision to a human.
        for tool in agent["tools"]:
            assert tool in text, (agent["type"], tool)
        assert "Return one JSON object" in text and "human" in text.lower(), agent["type"]


def test_the_templates_use_only_tools_the_platform_knows():
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

    known = {"knowledge_base_search"}
    for tools in _AGENT_TYPE_DEFAULT_TOOLS.values():
        known.update(tools)
    config = yaml.safe_load((PACK_DIR / "config.yaml").read_text(encoding="utf-8"))
    for agent in config["agents"]:
        unknown = sorted(set(agent["tools"]) - known)
        assert not unknown, (agent["type"], unknown)


def test_the_pack_installs_its_agents_in_shadow_and_uninstalls_cleanly():
    detail = get_pack_detail("banking")
    assert detail is not None and len(detail["agents"]) == 5 and len(detail["workflows"]) == 2
    result = install_pack("banking", "tenant-banking-001")
    assert result["status"] == "installed" and len(result["agents_created"]) == 5
    assert all(agent["mode"] == "shadow" for agent in result["agents_created"])
    removed = uninstall_pack("banking", "tenant-banking-001")
    assert removed["status"] == "uninstalled" and len(removed["agents_removed"]) == 5
