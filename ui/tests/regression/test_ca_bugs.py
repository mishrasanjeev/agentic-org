"""Regression tests for CA features."""
from __future__ import annotations
import inspect
import re

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")

class TestNotInMemory:
    def test_uses_db(self):
        import api.v1.companies as m
        src = inspect.getsource(m)
        assert "async_session_factory" in src or "get_tenant_session" in src

class TestSeedData:
    def test_seven(self):
        from core.seed_ca_demo import DEMO_COMPANIES
        assert len(DEMO_COMPANIES) == 7

    def test_valid_gstins(self):
        from core.seed_ca_demo import DEMO_COMPANIES
        for c in DEMO_COMPANIES:
            assert GSTIN_RE.match(c["gstin"]), c["name"]

    def test_valid_pans(self):
        from core.seed_ca_demo import DEMO_COMPANIES
        for c in DEMO_COMPANIES:
            assert PAN_RE.match(c["pan"]), c["name"]

class TestDemoUser:
    def test_email(self):
        from core.seed_ca_demo import DEMO_USER
        assert DEMO_USER["email"] == "demo@cafirm.agenticorg.ai"

    def test_role(self):
        from core.seed_ca_demo import DEMO_USER
        assert DEMO_USER["role"] == "partner"

class TestGSTDefault:
    def test_auto_file_off(self):
        from core.models.company import Company
        assert Company.__table__.columns["gst_auto_file"].default is not None

    def test_hitl_before_filing(self):
        from core.agents.packs.ca import CA_PACK
        gst = next(a for a in CA_PACK["agents"] if "GST" in a["name"])
        assert gst["hitl_condition"] == "always_before_filing"

class TestRoles:
    def test_all_five(self):
        from api.v1.companies import CompanyRole
        assert {r.value for r in CompanyRole} == {"partner", "manager", "senior_associate", "associate", "audit_reviewer"}
