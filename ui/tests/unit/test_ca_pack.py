"""Tests for CA Firm Industry Pack."""
from __future__ import annotations


class TestCAPack:
    def test_importable(self):
        from core.agents.packs.ca import CA_PACK
        assert CA_PACK["id"] == "ca-firm"

    def test_five_agents(self):
        from core.agents.packs.ca import CA_PACK
        assert len(CA_PACK["agents"]) == 5

    def test_five_workflows(self):
        from core.agents.packs.ca import CA_PACK
        assert len(CA_PACK["workflows"]) == 5

    def test_pricing(self):
        from core.agents.packs.ca import CA_PACK
        assert CA_PACK["pricing"]["inr_monthly_per_client"] == 4999
        assert CA_PACK["pricing"]["usd_monthly_per_client"] == 59

    def test_gst_agent_tools(self):
        from core.agents.packs.ca import CA_PACK
        gst = next(a for a in CA_PACK["agents"] if "GST" in a["name"])
        assert "gstn:fetch_gstr2a" in gst["tools"]
        assert "gstn:file_gstr3b" in gst["tools"]

    def test_tds_agent_tools(self):
        from core.agents.packs.ca import CA_PACK
        tds = next(a for a in CA_PACK["agents"] if "TDS" in a["name"])
        assert "income_tax:file_26q_return" in tds["tools"]

    def test_all_finance(self):
        from core.agents.packs.ca import CA_PACK
        for a in CA_PACK["agents"]:
            assert a["domain"] == "finance"

    def test_in_list_packs(self):
        from core.agents.packs.installer import list_packs
        assert "ca-firm" in [p["id"] for p in list_packs()]
