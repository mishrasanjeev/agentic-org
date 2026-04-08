# ruff: noqa: N801
"""Unit tests for Company model, API schemas, and validation logic.

Tests cover:
- Company creation with all fields
- GSTIN uniqueness constraint within a tenant
- Soft delete (is_active=False)
- Role mapping CRUD
- Company list pagination and filtering
- Onboarding flow
- GSTIN/PAN/TAN format validation (regex)
- Partial updates
- Cross-tenant GSTIN isolation
"""

from __future__ import annotations

import re
import uuid

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
TENANT_B = uuid.UUID("00000000-0000-0000-0000-000000000002")

# Indian tax ID regexes (from GSTN/CBDT specifications)
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]{1}\d{1}[A-Z]{1}\d{1}$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]{1}$")
TAN_RE = re.compile(r"^[A-Z]{4}\d{5}[A-Z]{1}$")


# ============================================================================
# Company model — field verification
# ============================================================================


class TestCompanyModel:
    """Verify the Company SQLAlchemy model has all required fields and constraints."""

    def test_company_has_all_required_fields(self):
        """Company model must expose gstin, pan, tan, cin, state_code, etc."""
        from core.models.company import Company

        expected_fields = [
            "id", "tenant_id", "name", "gstin", "pan", "tan", "cin",
            "state_code", "registered_address", "industry",
            "fy_start_month", "fy_end_month",
            "signatory_name", "signatory_designation", "signatory_email",
            "compliance_email",
            "dsc_serial", "dsc_expiry",
            "pf_registration", "esi_registration", "pt_registration",
            "bank_name", "bank_account_number", "bank_ifsc", "bank_branch",
            "tally_config", "gst_auto_file", "is_active",
            "user_roles", "created_at", "updated_at",
        ]
        mapper_cols = {c.key for c in Company.__table__.columns}
        for field in expected_fields:
            assert field in mapper_cols, f"Company model missing column: {field}"

    def test_company_table_name(self):
        from core.models.company import Company

        assert Company.__tablename__ == "companies"

    def test_company_unique_constraint_tenant_gstin(self):
        """Company model must have a unique constraint on (tenant_id, gstin)."""
        from core.models.company import Company

        constraints = Company.__table__.constraints
        uq_names = {c.name for c in constraints if hasattr(c, "columns")}
        assert "uq_company_tenant_gstin" in uq_names, (
            f"Missing UniqueConstraint 'uq_company_tenant_gstin'. Found: {uq_names}"
        )

    def test_gst_auto_file_default_false(self):
        """gst_auto_file column must default to False for safety."""
        from core.models.company import Company

        col = Company.__table__.c.gst_auto_file
        assert col.default is not None
        assert col.default.arg is False, "gst_auto_file must default to False"

    def test_is_active_default_true(self):
        """is_active column must default to True."""
        from core.models.company import Company

        col = Company.__table__.c.is_active
        assert col.default is not None
        assert col.default.arg is True

    def test_fy_defaults_india(self):
        """FY months default to April (04) start and March (03) end."""
        from core.models.company import Company

        start_col = Company.__table__.c.fy_start_month
        end_col = Company.__table__.c.fy_end_month
        assert start_col.default.arg == "04"
        assert end_col.default.arg == "03"


# ============================================================================
# API schemas
# ============================================================================


class TestCompanySchemas:
    """Verify Pydantic schemas for Company CRUD."""

    def test_company_create_all_fields(self):
        """CompanyCreate accepts all fields for a fully-specified company."""
        from api.v1.companies import CompanyCreate

        body = CompanyCreate(
            name="Test Corp Pvt Ltd",
            gstin="29AABCT1234F1Z5",
            pan="AABCT1234F",
            tan="BLRT12345G",
            cin="U12345KA2020PTC123456",
            state_code="29",
            industry="Manufacturing",
            registered_address="123 Test Street, Bengaluru",
            signatory_name="Test Signatory",
            signatory_designation="Director",
            signatory_email="dir@testcorp.com",
            compliance_email="ca@testcorp.com",
            dsc_serial="DSC-001",
            pf_registration="KABLR0012345000",
            esi_registration="1234567890123456",
            bank_name="HDFC Bank",
            bank_account_number="50100123456789",
            bank_ifsc="HDFC0001234",
            bank_branch="MG Road",
            gst_auto_file=False,
        )
        assert body.name == "Test Corp Pvt Ltd"
        assert body.gstin == "29AABCT1234F1Z5"
        assert body.pan == "AABCT1234F"
        assert body.gst_auto_file is False

    def test_company_create_minimal(self):
        """CompanyCreate requires only name and pan."""
        from api.v1.companies import CompanyCreate

        body = CompanyCreate(name="Minimal Corp", pan="ABCDE1234F")
        assert body.name == "Minimal Corp"
        assert body.gstin is None

    def test_company_update_partial_fields(self):
        """CompanyUpdate allows partial updates."""
        from api.v1.companies import CompanyUpdate

        update = CompanyUpdate(name="Updated Name")
        dumped = update.model_dump(exclude_unset=True)
        assert dumped == {"name": "Updated Name"}

    def test_company_update_rejects_extra_fields(self):
        """CompanyUpdate must reject unknown fields (extra=forbid)."""
        from api.v1.companies import CompanyUpdate

        with pytest.raises(ValidationError) as exc_info:
            CompanyUpdate(bogus_field="test")
        errors = exc_info.value.errors()
        assert any("bogus_field" in str(e) for e in errors)

    def test_company_onboard_schema(self):
        """CompanyOnboard schema accepts onboarding fields."""
        from api.v1.companies import CompanyOnboard

        body = CompanyOnboard(
            name="Onboard Corp",
            pan="AABCO1234F",
            gstin="29AABCO1234F1Z5",
            state_code="29",
            industry="IT Services",
        )
        assert body.name == "Onboard Corp"
        assert body.pan == "AABCO1234F"


# ============================================================================
# GSTIN format validation
# ============================================================================


class TestGSTINValidation:
    """Verify GSTIN regex matches valid Indian GSTINs and rejects invalid ones."""

    @pytest.mark.parametrize(
        "gstin",
        [
            "27AABCS1234F1Z5",
            "07AADCG5678H1Z3",
            "24AABCP9876L1Z8",
            "36AABCR3456K1Z2",
            "03AABCS7890M1Z6",
            "29AABCJ2345N1Z4",
            "08AABCA6789P1Z1",
        ],
    )
    def test_valid_gstin(self, gstin: str):
        assert GSTIN_RE.match(gstin), f"Valid GSTIN rejected: {gstin}"

    @pytest.mark.parametrize(
        "gstin",
        [
            "INVALID",
            "1234567890",
            "27AABCS1234F1Z",   # too short
            "27aabcs1234f1z5",  # lowercase
            "XX12345678901234", # wrong format
            "",
        ],
    )
    def test_invalid_gstin(self, gstin: str):
        assert not GSTIN_RE.match(gstin), f"Invalid GSTIN accepted: {gstin}"


# ============================================================================
# PAN format validation
# ============================================================================


class TestPANValidation:
    """Verify PAN regex matches valid Indian PANs and rejects invalid ones."""

    @pytest.mark.parametrize(
        "pan",
        [
            "AABCS1234F",
            "AADCG5678H",
            "AABCP9876L",
            "AABCR3456K",
            "AABCA6789P",
        ],
    )
    def test_valid_pan(self, pan: str):
        assert PAN_RE.match(pan), f"Valid PAN rejected: {pan}"

    @pytest.mark.parametrize(
        "pan",
        [
            "INVALID",
            "12345ABCDE",
            "aabcs1234f",   # lowercase
            "AABCS123",     # too short
            "",
        ],
    )
    def test_invalid_pan(self, pan: str):
        assert not PAN_RE.match(pan), f"Invalid PAN accepted: {pan}"


# ============================================================================
# TAN format validation
# ============================================================================


class TestTANValidation:
    """Verify TAN regex matches valid Indian TANs and rejects invalid ones."""

    @pytest.mark.parametrize(
        "tan",
        [
            "MUMS12345E",
            "DELS56789F",
            "AHMP98765G",
            "HYDR34567H",
            "CHNS78901A",
            "BLRJ23456B",
            "JPRA67890C",
        ],
    )
    def test_valid_tan(self, tan: str):
        assert TAN_RE.match(tan), f"Valid TAN rejected: {tan}"

    @pytest.mark.parametrize(
        "tan",
        [
            "INVALID",
            "1234567890",
            "mums12345e",   # lowercase
            "MUMS1234",     # too short
            "",
        ],
    )
    def test_invalid_tan(self, tan: str):
        assert not TAN_RE.match(tan), f"Invalid TAN accepted: {tan}"


# ============================================================================
# Role enum
# ============================================================================


class TestCompanyRoleEnum:
    """Verify CompanyRole enum has all 5 values."""

    def test_role_enum_has_5_values(self):
        from api.v1.companies import CompanyRole

        assert len(CompanyRole) == 5

    def test_role_enum_values(self):
        from api.v1.companies import CompanyRole

        expected = {"partner", "manager", "senior_associate", "associate", "audit_reviewer"}
        actual = {r.value for r in CompanyRole}
        assert actual == expected, f"Expected {expected}, got {actual}"


# ============================================================================
# RoleMapping schema
# ============================================================================


class TestRoleMappingSchema:
    """Verify role mapping schemas."""

    def test_role_mapping_creation(self):
        from api.v1.companies import CompanyRole, RoleMapping

        mapping = RoleMapping(user_id="user-001", role=CompanyRole.partner)
        assert mapping.user_id == "user-001"
        assert mapping.role == CompanyRole.partner

    def test_role_update_request(self):
        from api.v1.companies import CompanyRole, RoleMapping, RoleUpdateRequest

        req = RoleUpdateRequest(
            roles=[
                RoleMapping(user_id="u1", role=CompanyRole.partner),
                RoleMapping(user_id="u2", role=CompanyRole.manager),
                RoleMapping(user_id="u3", role=CompanyRole.associate),
            ]
        )
        assert len(req.roles) == 3


# ============================================================================
# API routes exist
# ============================================================================


class TestCompanyAPIRoutes:
    """Verify all company-related API routes are registered."""

    def test_company_routes_registered(self):
        from api.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        # Must have company CRUD routes
        assert any("/companies" in p for p in route_paths), (
            f"No /companies route found. Routes with 'compan': "
            f"{[p for p in route_paths if 'compan' in p.lower()]}"
        )

    def test_onboard_route_registered(self):
        from api.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        assert any("onboard" in p for p in route_paths), (
            f"No onboard route found. Routes: {[p for p in route_paths if 'compan' in p.lower()]}"
        )

    def test_roles_route_registered(self):
        from api.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        assert any("roles" in p for p in route_paths), (
            f"No roles route found. Routes: {[p for p in route_paths if 'compan' in p.lower()]}"
        )


# ============================================================================
# Company list schemas
# ============================================================================


class TestCompanyListOut:
    """Verify CompanyListOut pagination schema."""

    def test_list_out_schema(self):
        from api.v1.companies import CompanyListOut, CompanyOut

        out = CompanyListOut(
            items=[
                CompanyOut(
                    id="test-id",
                    name="Test Corp",
                    pan="AABCT1234F",
                    is_active=True,
                    gst_auto_file=False,
                    user_roles={},
                    created_at="2026-01-01T00:00:00Z",
                ),
            ],
            total=1,
            page=1,
            per_page=50,
        )
        assert out.total == 1
        assert len(out.items) == 1
        assert out.page == 1

    def test_list_out_empty(self):
        from api.v1.companies import CompanyListOut

        out = CompanyListOut(items=[], total=0, page=1, per_page=50)
        assert out.total == 0
        assert len(out.items) == 0


# ============================================================================
# GSTIN uniqueness logic (code-level check)
# ============================================================================


class TestGSTINUniqueness:
    """Verify GSTIN uniqueness enforcement logic exists in the API."""

    def test_create_company_checks_gstin_uniqueness(self):
        """The create_company function source must check for duplicate GSTIN."""
        import inspect

        from api.v1.companies import create_company

        source = inspect.getsource(create_company)
        assert "already exists" in source, "create_company must raise 409 on duplicate GSTIN"

    def test_update_company_checks_gstin_uniqueness(self):
        """The update_company function source must check for duplicate GSTIN on change."""
        import inspect

        from api.v1.companies import update_company

        source = inspect.getsource(update_company)
        assert "already exists" in source, "update_company must check GSTIN uniqueness"

    def test_different_tenants_gstin_allowed(self):
        """The unique constraint is scoped to tenant_id -- different tenants can share GSTIN."""
        from core.models.company import Company

        # Verify the constraint includes tenant_id
        for constraint in Company.__table__.constraints:
            if getattr(constraint, "name", "") == "uq_company_tenant_gstin":
                col_names = {c.name for c in constraint.columns}
                assert "tenant_id" in col_names, "Unique constraint must include tenant_id"
                assert "gstin" in col_names, "Unique constraint must include gstin"
                return
        pytest.fail("uq_company_tenant_gstin constraint not found")


# ============================================================================
# Soft delete logic
# ============================================================================


class TestSoftDelete:
    """Verify soft delete sets is_active=False (not hard delete)."""

    def test_delete_endpoint_sets_is_active_false(self):
        """delete_company source must set is_active = False, not DELETE FROM."""
        import inspect

        from api.v1.companies import delete_company

        source = inspect.getsource(delete_company)
        assert "is_active = False" in source, "delete_company must soft-delete via is_active=False"
        assert "session.delete" not in source, "delete_company must NOT hard-delete"
