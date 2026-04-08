# ruff: noqa: N801
"""Regression tests for CA-specific bugs.

Every test anchors a specific data/model invariant so that any regression
is caught immediately.  Tests are self-contained and run without a database.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Tax ID regexes (same as production validation)
# ---------------------------------------------------------------------------

GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]{1}\d{1}[A-Z]{1}\d{1}$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]{1}$")


# ============================================================================
# BUG: Company API was returning in-memory mock data instead of DB-backed data
# FIX: companies.py now uses SQLAlchemy queries against the Company model
# ============================================================================


class TestCompanyAPIUsesDB:
    """Company API must use database queries, not in-memory dicts."""

    def test_create_company_uses_sqlalchemy(self):
        """create_company source must use SQLAlchemy session, not a dict."""
        import inspect

        from api.v1.companies import create_company

        source = inspect.getsource(create_company)
        assert "session" in source, "create_company must use a DB session"
        assert "session.add" in source, "create_company must add to DB session"
        assert "session.flush" in source, "create_company must flush the session"

    def test_list_companies_uses_sqlalchemy(self):
        """list_companies source must use SQLAlchemy select(), not list iteration."""
        import inspect

        from api.v1.companies import list_companies

        source = inspect.getsource(list_companies)
        assert "select" in source, "list_companies must use SQLAlchemy select()"
        assert "session" in source, "list_companies must use a DB session"

    def test_get_company_uses_sqlalchemy(self):
        """get_company must query from database."""
        import inspect

        from api.v1.companies import get_company

        source = inspect.getsource(get_company)
        assert "select" in source
        assert "session" in source

    def test_no_in_memory_store(self):
        """companies.py must NOT define a global _companies dict or list."""
        import inspect

        import api.v1.companies as mod

        source = inspect.getsource(mod)
        # These patterns indicate an in-memory store bug
        assert "_companies: dict" not in source, "Found in-memory _companies dict"
        assert "_companies: list" not in source, "Found in-memory _companies list"
        assert "COMPANIES_STORE" not in source, "Found in-memory COMPANIES_STORE"


# ============================================================================
# BUG: Seed data was missing companies or had wrong count
# FIX: seed_ca_demo.py now has exactly 7 companies
# ============================================================================


class TestSeedData:
    """Verify seed data has exactly 7 companies with valid data."""

    def test_seed_has_7_companies(self):
        from core.seed_ca_demo import DEMO_COMPANIES

        assert len(DEMO_COMPANIES) == 7, (
            f"Expected 7 demo companies, got {len(DEMO_COMPANIES)}: "
            f"{[c['name'] for c in DEMO_COMPANIES]}"
        )

    def test_all_companies_have_gstin(self):
        """Every demo company must have a GSTIN."""
        from core.seed_ca_demo import DEMO_COMPANIES

        for company in DEMO_COMPANIES:
            assert company.get("gstin"), f"Company '{company['name']}' missing GSTIN"

    def test_all_companies_have_valid_gstin_format(self):
        """Every GSTIN in seed data must match the Indian GSTIN format."""
        from core.seed_ca_demo import DEMO_COMPANIES

        for company in DEMO_COMPANIES:
            gstin = company["gstin"]
            assert GSTIN_RE.match(gstin), (
                f"Company '{company['name']}' has invalid GSTIN: {gstin}"
            )

    def test_all_companies_have_valid_pan_format(self):
        """Every PAN in seed data must match the Indian PAN format."""
        from core.seed_ca_demo import DEMO_COMPANIES

        for company in DEMO_COMPANIES:
            pan = company["pan"]
            assert PAN_RE.match(pan), (
                f"Company '{company['name']}' has invalid PAN: {pan}"
            )

    def test_all_companies_have_pan(self):
        """Every demo company must have a PAN."""
        from core.seed_ca_demo import DEMO_COMPANIES

        for company in DEMO_COMPANIES:
            assert company.get("pan"), f"Company '{company['name']}' missing PAN"

    def test_all_companies_have_industry(self):
        """Every demo company must have an industry."""
        from core.seed_ca_demo import DEMO_COMPANIES

        for company in DEMO_COMPANIES:
            assert company.get("industry"), f"Company '{company['name']}' missing industry"

    def test_company_names(self):
        """All 7 expected companies are present."""
        from core.seed_ca_demo import DEMO_COMPANIES

        names = {c["name"] for c in DEMO_COMPANIES}
        expected = {
            "Sharma Manufacturing Pvt Ltd",
            "Gupta Traders",
            "Patel Pharma Ltd",
            "Reddy Constructions",
            "Singh Logistics Pvt Ltd",
            "Joshi IT Solutions Pvt Ltd",
            "Agarwal Textiles",
        }
        assert names == expected, f"Expected {expected}, got {names}"


# ============================================================================
# BUG: Demo user credentials were missing or wrong
# FIX: seed_ca_demo.py now defines DEMO_USER_EMAIL and DEMO_USER_PASSWORD
# ============================================================================


class TestDemoUser:
    """Demo user must exist with correct credentials."""

    def test_demo_user_email(self):
        from core.seed_ca_demo import DEMO_USER_EMAIL

        assert DEMO_USER_EMAIL == "demo@cafirm.agenticorg.ai"

    def test_demo_user_password(self):
        from core.seed_ca_demo import DEMO_USER_PASSWORD

        assert DEMO_USER_PASSWORD == "demo123!"

    def test_demo_user_role(self):
        from core.seed_ca_demo import DEMO_USER_ROLE

        assert DEMO_USER_ROLE == "admin"

    def test_demo_user_name(self):
        from core.seed_ca_demo import DEMO_USER_NAME

        assert DEMO_USER_NAME == "Demo Partner"

    def test_demo_tenant_slug(self):
        from core.seed_ca_demo import DEMO_TENANT_SLUG

        assert DEMO_TENANT_SLUG == "demo-ca-firm"


# ============================================================================
# BUG: Company model missing fields (gstin, pan, tan, cin, etc.)
# FIX: Company model now has all required fields
# ============================================================================


class TestCompanyModelFields:
    """Company model must have all the fields required for Indian compliance."""

    def test_has_tax_id_fields(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        tax_fields = {"gstin", "pan", "tan", "cin", "state_code"}
        missing = tax_fields - cols
        assert not missing, f"Company model missing tax fields: {missing}"

    def test_has_signatory_fields(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        signatory_fields = {"signatory_name", "signatory_designation", "signatory_email"}
        missing = signatory_fields - cols
        assert not missing, f"Company model missing signatory fields: {missing}"

    def test_has_bank_fields(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        bank_fields = {"bank_name", "bank_account_number", "bank_ifsc", "bank_branch"}
        missing = bank_fields - cols
        assert not missing, f"Company model missing bank fields: {missing}"

    def test_has_compliance_fields(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        compliance_fields = {
            "pf_registration", "esi_registration", "pt_registration",
            "dsc_serial", "dsc_expiry", "compliance_email",
        }
        missing = compliance_fields - cols
        assert not missing, f"Company model missing compliance fields: {missing}"

    def test_has_tally_config(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "tally_config" in cols

    def test_has_user_roles(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "user_roles" in cols


# ============================================================================
# BUG: gst_auto_file defaulted to True (dangerous for compliance)
# FIX: Default is now False
# ============================================================================


class TestGSTAutoFileDefault:
    """gst_auto_file must default to False for safety."""

    def test_model_default_false(self):
        from core.models.company import Company

        col = Company.__table__.c.gst_auto_file
        assert col.default is not None
        assert col.default.arg is False, (
            f"gst_auto_file default is {col.default.arg}, must be False"
        )

    def test_schema_default_false(self):
        from api.v1.companies import CompanyCreate

        body = CompanyCreate(name="Test", pan="ABCDE1234F")
        assert body.gst_auto_file is False


# ============================================================================
# BUG: CompanyRole enum was missing values
# FIX: Enum now has all 5 values
# ============================================================================


class TestRoleEnumComplete:
    """CompanyRole enum must have all 5 values for CA firm hierarchy."""

    def test_has_5_values(self):
        from api.v1.companies import CompanyRole

        assert len(CompanyRole) == 5

    def test_has_partner(self):
        from api.v1.companies import CompanyRole

        assert CompanyRole.partner.value == "partner"

    def test_has_manager(self):
        from api.v1.companies import CompanyRole

        assert CompanyRole.manager.value == "manager"

    def test_has_senior_associate(self):
        from api.v1.companies import CompanyRole

        assert CompanyRole.senior_associate.value == "senior_associate"

    def test_has_associate(self):
        from api.v1.companies import CompanyRole

        assert CompanyRole.associate.value == "associate"

    def test_has_audit_reviewer(self):
        from api.v1.companies import CompanyRole

        assert CompanyRole.audit_reviewer.value == "audit_reviewer"
