# ruff: noqa: N801
"""Tests for the CA (Chartered Accountant) Industry Pack.

Verifies:
- CA pack is discoverable via list_packs()
- Pack has correct agents, workflows, and pricing
- Install/uninstall lifecycle works correctly
- Agent tool assignments are correct
"""

from __future__ import annotations

import pytest

from core.agents.packs.installer import (
    get_installed_packs,
    get_pack_detail,
    install_pack,
    list_packs,
    uninstall_pack,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_installed_store() -> None:
    """Reset the in-memory installed-packs store between tests."""
    from core.agents.packs import installer

    installer._installed.clear()


@pytest.fixture(autouse=True)
def _reset_store():
    _clear_installed_store()
    yield
    _clear_installed_store()


def _find_ca_pack() -> dict | None:
    """Find the CA pack from list_packs() by name or display_name."""
    packs = list_packs()
    for p in packs:
        name = p.get("name", "")
        display = p.get("display_name", "").lower()
        if name == "ca-firm" or "chartered accountant" in display:
            return p
    return None


def _get_ca_pack_id() -> str:
    """Return the pack ID used for install/uninstall."""
    from core.agents.packs.ca import CA_PACK

    return str(CA_PACK["id"])


# ============================================================================
# Discovery
# ============================================================================


class TestCAPackDiscovery:
    """CA pack appears in list_packs() and is retrievable via get_pack_detail()."""

    def test_ca_pack_appears_in_list_packs(self):
        """CA pack must be discoverable via list_packs()."""
        pack = _find_ca_pack()
        assert pack is not None, (
            f"CA pack not found in list_packs(). Available: "
            f"{[p['name'] for p in list_packs()]}"
        )

    def test_ca_pack_detail_exists(self):
        """get_pack_detail returns full config for the CA pack."""
        pack_id = _get_ca_pack_id()
        detail = get_pack_detail(pack_id)
        assert detail is not None, f"get_pack_detail('{pack_id}') returned None"
        assert "agents" in detail
        assert "workflows" in detail


# ============================================================================
# Agents
# ============================================================================


class TestCAPackAgents:
    """CA pack must have exactly 5 agents with correct configurations."""

    def test_ca_pack_has_5_agents(self):
        """CA pack contains 5 agents."""
        from core.agents.packs.ca import CA_PACK

        agents = CA_PACK["agents"]
        assert len(agents) == 5, f"Expected 5 agents, got {len(agents)}: {[a['name'] for a in agents]}"

    def test_agent_names(self):
        """All 5 expected agents are present."""
        from core.agents.packs.ca import CA_PACK

        names = {a["name"] for a in CA_PACK["agents"]}
        expected = {
            "GST Filing Agent",
            "TDS Compliance Agent",
            "Bank Reconciliation Agent",
            "FP&A Analyst Agent",
            "AR Collections Agent",
        }
        assert names == expected, f"Agent mismatch: expected {expected}, got {names}"

    def test_all_agents_have_domain_finance(self):
        """Every CA agent must be in the finance domain."""
        from core.agents.packs.ca import CA_PACK

        for agent in CA_PACK["agents"]:
            assert agent["domain"] == "finance", (
                f"Agent '{agent['name']}' has domain '{agent['domain']}', expected 'finance'"
            )


# ============================================================================
# GST Filing Agent tools
# ============================================================================


class TestGSTFilingAgentTools:
    """GST Filing Agent must have all GSTN + Tally tools."""

    def test_gst_filing_agent_tools(self):
        from core.agents.packs.ca import CA_PACK

        gst_agent = next(a for a in CA_PACK["agents"] if a["name"] == "GST Filing Agent")
        tools = gst_agent["tools"]

        # Must have GSTN tools
        assert "gstn:fetch_gstr2a" in tools
        assert "gstn:push_gstr1_data" in tools
        assert "gstn:file_gstr3b" in tools
        assert "gstn:file_gstr9" in tools
        assert "gstn:generate_einvoice_irn" in tools
        assert "gstn:generate_eway_bill" in tools

        # Must have Tally tools
        assert "tally:get_trial_balance" in tools
        assert "tally:generate_gst_report" in tools

    def test_gst_filing_agent_has_8_tools(self):
        from core.agents.packs.ca import CA_PACK

        gst_agent = next(a for a in CA_PACK["agents"] if a["name"] == "GST Filing Agent")
        assert len(gst_agent["tools"]) == 8

    def test_gst_filing_agent_hitl_always(self):
        """GST Filing Agent must require HITL before filing."""
        from core.agents.packs.ca import CA_PACK

        gst_agent = next(a for a in CA_PACK["agents"] if a["name"] == "GST Filing Agent")
        assert gst_agent["hitl_condition"] == "always_before_filing"


# ============================================================================
# TDS Compliance Agent tools
# ============================================================================


class TestTDSComplianceAgentTools:
    """TDS Compliance Agent must have all Income Tax + Tally tools."""

    def test_tds_compliance_agent_tools(self):
        from core.agents.packs.ca import CA_PACK

        tds_agent = next(a for a in CA_PACK["agents"] if a["name"] == "TDS Compliance Agent")
        tools = tds_agent["tools"]

        # Income Tax tools
        assert "income_tax:file_26q_return" in tools
        assert "income_tax:file_24q_return" in tools
        assert "income_tax:check_tds_credit_in_26as" in tools
        assert "income_tax:download_form_16a" in tools
        assert "income_tax:pay_tax_challan" in tools

        # Tally tools
        assert "tally:get_ledger_balance" in tools
        assert "tally:post_voucher" in tools

    def test_tds_compliance_agent_has_7_tools(self):
        from core.agents.packs.ca import CA_PACK

        tds_agent = next(a for a in CA_PACK["agents"] if a["name"] == "TDS Compliance Agent")
        assert len(tds_agent["tools"]) == 7

    def test_tds_compliance_agent_hitl_always(self):
        """TDS agent must also require HITL before filing."""
        from core.agents.packs.ca import CA_PACK

        tds_agent = next(a for a in CA_PACK["agents"] if a["name"] == "TDS Compliance Agent")
        assert tds_agent["hitl_condition"] == "always_before_filing"


# ============================================================================
# Workflows
# ============================================================================


class TestCAPackWorkflows:
    """CA pack must have exactly 5 workflows."""

    def test_ca_pack_has_5_workflows(self):
        from core.agents.packs.ca import CA_PACK

        workflows = CA_PACK["workflows"]
        assert len(workflows) == 5, f"Expected 5 workflows, got {len(workflows)}: {workflows}"

    def test_workflow_names(self):
        from core.agents.packs.ca import CA_PACK

        expected = {
            "gstr_filing_monthly",
            "tds_quarterly_filing",
            "bank_recon_daily",
            "month_end_close",
            "tax_calendar",
        }
        actual = set(CA_PACK["workflows"])
        assert actual == expected, f"Workflow mismatch: expected {expected}, got {actual}"


# ============================================================================
# Pricing
# ============================================================================


class TestCAPackPricing:
    """CA pack pricing must be INR 4999 and USD 59 per client per month."""

    def test_inr_pricing(self):
        from core.agents.packs.ca import CA_PACK

        pricing = CA_PACK["pricing"]
        assert pricing["inr_monthly_per_client"] == 4999

    def test_usd_pricing(self):
        from core.agents.packs.ca import CA_PACK

        pricing = CA_PACK["pricing"]
        assert pricing["usd_monthly_per_client"] == 59

    def test_pricing_in_list_packs(self):
        """Pricing should be surfaced when queried via list_packs."""
        pack = _find_ca_pack()
        assert pack is not None
        pricing = pack.get("pricing", {})
        assert pricing.get("inr_monthly_per_client") == 4999
        assert pricing.get("usd_monthly_per_client") == 59


# ============================================================================
# Install / Uninstall lifecycle
# ============================================================================


class TestCAPackInstallUninstall:
    """Install and uninstall lifecycle for the CA pack."""

    def test_install_creates_agents_in_shadow_mode(self):
        pack_id = _get_ca_pack_id()
        result = install_pack(pack_id, "tenant-ca-001")
        assert result["status"] == "installed"
        assert len(result["agents_created"]) >= 1
        for agent in result["agents_created"]:
            assert agent["mode"] == "shadow", f"Agent installed in mode '{agent['mode']}', expected 'shadow'"

    def test_install_creates_5_agents(self):
        pack_id = _get_ca_pack_id()
        result = install_pack(pack_id, "tenant-ca-002")
        assert len(result["agents_created"]) == 5

    def test_install_idempotent(self):
        """Installing the same pack twice returns already_installed."""
        pack_id = _get_ca_pack_id()
        install_pack(pack_id, "tenant-ca-003")
        result2 = install_pack(pack_id, "tenant-ca-003")
        assert result2["status"] == "already_installed"

    def test_uninstall_removes_agents(self):
        pack_id = _get_ca_pack_id()
        install_pack(pack_id, "tenant-ca-004")
        result = uninstall_pack(pack_id, "tenant-ca-004")
        assert result["status"] == "uninstalled"
        assert len(result["agents_removed"]) >= 1
        assert pack_id not in get_installed_packs("tenant-ca-004")

    def test_uninstall_not_installed(self):
        """Uninstalling a pack that is not installed returns not_installed."""
        pack_id = _get_ca_pack_id()
        result = uninstall_pack(pack_id, "tenant-ca-005")
        assert result["status"] == "not_installed"

    def test_install_per_tenant_isolation(self):
        """Installing CA pack for tenant A does not affect tenant B."""
        pack_id = _get_ca_pack_id()
        install_pack(pack_id, "tenant-A")
        assert pack_id in get_installed_packs("tenant-A")
        assert pack_id not in get_installed_packs("tenant-B")
