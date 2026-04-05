"""Tests for industry pack installer and API."""

from __future__ import annotations

import pytest

from core.agents.packs.installer import (
    get_installed_packs,
    get_pack_detail,
    install_pack,
    list_packs,
    uninstall_pack,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _clear_installed_store() -> None:
    """Reset the in-memory installed-packs store between tests."""
    from core.agents.packs import installer
    installer._installed.clear()


@pytest.fixture(autouse=True)
def _reset_store():
    _clear_installed_store()
    yield
    _clear_installed_store()


# ── tests ────────────────────────────────────────────────────────────────────

def test_list_packs_returns_4():
    """All 4 industry packs (healthcare, insurance, legal, manufacturing) are discovered."""
    packs = list_packs()
    names = {p["name"] for p in packs}
    assert len(packs) >= 4
    assert {"healthcare", "legal", "insurance", "manufacturing"} <= names


def test_install_creates_agents_in_shadow():
    """Installing a pack creates agents in shadow mode."""
    result = install_pack("healthcare", "tenant-001")
    assert result["status"] == "installed"
    assert len(result["agents_created"]) >= 1
    for agent in result["agents_created"]:
        assert agent["mode"] == "shadow"


def test_uninstall_removes_agents():
    """Uninstalling a pack removes agents and workflows."""
    install_pack("legal", "tenant-002")
    result = uninstall_pack("legal", "tenant-002")
    assert result["status"] == "uninstalled"
    assert len(result["agents_removed"]) >= 1
    assert "legal" not in get_installed_packs("tenant-002")


def test_healthcare_has_hipaa_prompts():
    """Healthcare pack config includes HIPAA compliance markers."""
    detail = get_pack_detail("healthcare")
    assert detail is not None
    # Check compliance field
    compliance = detail.get("compliance", [])
    assert any("HIPAA" in str(c).upper() for c in compliance), "Healthcare pack should reference HIPAA"


def test_pack_detail_returns_config():
    """get_pack_detail returns full config for a valid pack."""
    detail = get_pack_detail("insurance")
    assert detail is not None
    assert detail["name"] == "insurance"
    assert "agents" in detail
    assert "workflows" in detail
    assert len(detail["agents"]) >= 1


def test_installed_packs_per_tenant():
    """Installed packs are tracked per tenant — no cross-tenant leakage."""
    install_pack("healthcare", "tenant-A")
    install_pack("legal", "tenant-B")

    assert "healthcare" in get_installed_packs("tenant-A")
    assert "legal" not in get_installed_packs("tenant-A")

    assert "legal" in get_installed_packs("tenant-B")
    assert "healthcare" not in get_installed_packs("tenant-B")
