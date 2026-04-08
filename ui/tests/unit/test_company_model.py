"""Tests for Company model and validation."""
from __future__ import annotations
import re
import pytest

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")
TAN_RE = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]{1}$")

class TestCompanyModel:
    def test_importable(self):
        from core.models.company import Company
        assert Company.__tablename__ == "companies"

    def test_has_tenant_id(self):
        from core.models.company import Company
        assert hasattr(Company, "tenant_id")

    def test_has_tax_fields(self):
        from core.models.company import Company
        for f in ("gstin", "pan", "tan", "cin", "state_code"):
            assert hasattr(Company, f)

    def test_has_bank_fields(self):
        from core.models.company import Company
        for f in ("bank_name", "bank_account_number", "bank_ifsc"):
            assert hasattr(Company, f)

    def test_has_tally_gst(self):
        from core.models.company import Company
        assert hasattr(Company, "tally_config")
        assert hasattr(Company, "gst_auto_file")

    def test_has_user_roles(self):
        from core.models.company import Company
        assert hasattr(Company, "user_roles")

    def test_in_init(self):
        from core.models import Company
        assert Company.__tablename__ == "companies"

class TestGSTIN:
    @pytest.mark.parametrize("g", ["27AABCS1234F1Z5", "07AADCG5678H1Z3", "29AABCJ2345N1Z4"])
    def test_valid(self, g):
        assert GSTIN_RE.match(g)

    @pytest.mark.parametrize("g", ["INVALID", "27aabcs1234f1z5", ""])
    def test_invalid(self, g):
        assert not GSTIN_RE.match(g)

class TestPAN:
    @pytest.mark.parametrize("p", ["AABCS1234F", "AADCG5678H"])
    def test_valid(self, p):
        assert PAN_RE.match(p)

    @pytest.mark.parametrize("p", ["12345", ""])
    def test_invalid(self, p):
        assert not PAN_RE.match(p)

class TestTAN:
    @pytest.mark.parametrize("t", ["MUMS12345E", "DELS56789F"])
    def test_valid(self, t):
        assert TAN_RE.match(t)

    @pytest.mark.parametrize("t", ["12345", ""])
    def test_invalid(self, t):
        assert not TAN_RE.match(t)

class TestRoles:
    def test_five_roles(self):
        from api.v1.companies import CompanyRole
        assert {r.value for r in CompanyRole} == {"partner", "manager", "senior_associate", "associate", "audit_reviewer"}

class TestDefaults:
    def test_gst_auto_file(self):
        from core.models.company import Company
        assert Company.__table__.columns["gst_auto_file"].default is not None
