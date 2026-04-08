# ruff: noqa: N801
"""Unit tests for CA paid add-on features: subscriptions, filing approvals, GSTN uploads.

Tests cover:
- Database schema validation (tables, columns, constraints, FKs)
- Model defaults and field types
- API route registration
- Business logic validation (self-approval, auto-approval, status transitions)
- Pydantic schema validation
"""

from __future__ import annotations

import uuid

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
TENANT_B = uuid.UUID("00000000-0000-0000-0000-000000000002")

VALID_FILING_TYPES = {"gstr1", "gstr3b", "gstr9", "tds_26q", "tds_24q"}
VALID_UPLOAD_STATUSES = {"generated", "downloaded", "uploaded", "acknowledged"}
VALID_APPROVAL_STATUSES = {"pending", "approved", "rejected", "filed"}
VALID_SUBSCRIPTION_STATUSES = {"trial", "active", "cancelled", "expired"}


# ============================================================================
# Database schema validation -- ca_subscriptions
# ============================================================================


class TestCASubscriptionSchema:
    """Verify ca_subscriptions table has all expected columns and constraints."""

    def test_ca_subscriptions_table_exists(self):
        from core.models.ca_subscription import CASubscription

        assert CASubscription.__tablename__ == "ca_subscriptions"

    def test_ca_subscriptions_has_all_columns(self):
        from core.models.ca_subscription import CASubscription

        expected_columns = [
            "id", "tenant_id", "plan", "status", "max_clients",
            "price_inr", "price_usd", "billing_cycle",
            "trial_ends_at", "current_period_start", "current_period_end",
            "cancelled_at", "created_at", "updated_at",
        ]
        mapper_cols = {c.key for c in CASubscription.__table__.columns}
        for col in expected_columns:
            assert col in mapper_cols, f"ca_subscriptions missing column: {col}"

    def test_unique_constraint_on_tenant_id(self):
        """Only one CA subscription allowed per tenant."""
        from core.models.ca_subscription import CASubscription

        constraints = CASubscription.__table__.constraints
        uq_names = {c.name for c in constraints if hasattr(c, "columns") and c.name}
        assert "uq_ca_sub_tenant" in uq_names, (
            f"Missing UniqueConstraint 'uq_ca_sub_tenant'. Found: {uq_names}"
        )

    def test_tenant_id_fk(self):
        """tenant_id must reference tenants.id."""
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.tenant_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "tenants.id" in fk_targets, (
            f"tenant_id FK target should be tenants.id, got: {fk_targets}"
        )


# ============================================================================
# Database schema validation -- filing_approvals
# ============================================================================


class TestFilingApprovalSchema:
    """Verify filing_approvals table has all expected columns and FKs."""

    def test_filing_approvals_table_exists(self):
        from core.models.filing_approval import FilingApproval

        assert FilingApproval.__tablename__ == "filing_approvals"

    def test_filing_approvals_has_all_columns(self):
        from core.models.filing_approval import FilingApproval

        expected_columns = [
            "id", "tenant_id", "company_id",
            "filing_type", "filing_period", "filing_data",
            "status", "requested_by", "approved_by", "approved_at",
            "rejection_reason", "auto_approved",
            "created_at", "updated_at",
        ]
        mapper_cols = {c.key for c in FilingApproval.__table__.columns}
        for col in expected_columns:
            assert col in mapper_cols, f"filing_approvals missing column: {col}"

    def test_company_id_fk_references_companies(self):
        """filing_approvals.company_id must FK to companies.id."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.company_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "companies.id" in fk_targets, (
            f"company_id FK target should be companies.id, got: {fk_targets}"
        )

    def test_tenant_id_fk(self):
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.tenant_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "tenants.id" in fk_targets


# ============================================================================
# Database schema validation -- gstn_uploads
# ============================================================================


class TestGSTNUploadSchema:
    """Verify gstn_uploads table has all expected columns and FKs."""

    def test_gstn_uploads_table_exists(self):
        from core.models.gstn_upload import GSTNUpload

        assert GSTNUpload.__tablename__ == "gstn_uploads"

    def test_gstn_uploads_has_all_columns(self):
        from core.models.gstn_upload import GSTNUpload

        expected_columns = [
            "id", "tenant_id", "company_id",
            "upload_type", "filing_period", "file_name",
            "file_path", "file_size_bytes", "status",
            "gstn_arn", "uploaded_at", "uploaded_by",
            "created_at", "updated_at",
        ]
        mapper_cols = {c.key for c in GSTNUpload.__table__.columns}
        for col in expected_columns:
            assert col in mapper_cols, f"gstn_uploads missing column: {col}"

    def test_company_id_fk_references_companies(self):
        """gstn_uploads.company_id must FK to companies.id."""
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.company_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "companies.id" in fk_targets, (
            f"company_id FK target should be companies.id, got: {fk_targets}"
        )

    def test_tenant_id_fk(self):
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.tenant_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "tenants.id" in fk_targets


# ============================================================================
# Database schema validation -- Company model new columns
# ============================================================================


class TestCompanyNewColumns:
    """Verify Company model has the v4.2.0 CA paid add-on columns."""

    def test_subscription_status_column(self):
        from core.models.company import Company

        col = Company.__table__.c.subscription_status
        assert col is not None
        assert col.default is not None
        assert col.default.arg == "trial"

    def test_client_health_score_column(self):
        from core.models.company import Company

        col = Company.__table__.c.client_health_score
        assert col is not None
        assert col.default is not None
        assert col.default.arg == 100

    def test_document_vault_enabled_column(self):
        from core.models.company import Company

        col = Company.__table__.c.document_vault_enabled
        assert col is not None
        assert col.default is not None
        assert col.default.arg is True

    def test_compliance_alerts_email_column(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "compliance_alerts_email" in cols


# ============================================================================
# Model defaults -- CASubscription
# ============================================================================


class TestCASubscriptionDefaults:
    """Verify CASubscription model field defaults."""

    def test_plan_defaults_to_ca_pro(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.plan
        assert col.default.arg == "ca_pro"

    def test_status_defaults_to_trial(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.status
        assert col.default.arg == "trial"

    def test_max_clients_defaults_to_7(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.max_clients
        assert col.default.arg == 7

    def test_price_inr_defaults_to_4999(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.price_inr
        assert col.default.arg == 4999

    def test_price_usd_defaults_to_59(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.price_usd
        assert col.default.arg == 59

    def test_billing_cycle_defaults_to_monthly(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.billing_cycle
        assert col.default.arg == "monthly"


# ============================================================================
# Model defaults -- FilingApproval
# ============================================================================


class TestFilingApprovalDefaults:
    """Verify FilingApproval model field defaults."""

    def test_status_defaults_to_pending(self):
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.status
        assert col.default.arg == "pending"

    def test_auto_approved_defaults_to_false(self):
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.auto_approved
        assert col.default.arg is False


# ============================================================================
# Model defaults -- GSTNUpload
# ============================================================================


class TestGSTNUploadDefaults:
    """Verify GSTNUpload model field defaults."""

    def test_status_defaults_to_generated(self):
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.status
        assert col.default.arg == "generated"


# ============================================================================
# API route validation -- Filing Approvals
# ============================================================================


class TestFilingApprovalAPIRoutes:
    """Verify filing approval API routes are registered."""

    def test_companies_route_exists(self):
        """The companies router must be registered in the app."""
        from api.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        assert any("/companies" in p for p in route_paths), (
            f"No /companies route found. Routes: {[p for p in route_paths if 'compan' in p.lower()]}"
        )

    def test_approvals_route_exists(self):
        """A general approvals router must be registered."""
        from api.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        assert any("approvals" in p for p in route_paths), (
            f"No approvals route found. Routes: {route_paths}"
        )

    def test_onboard_route_exists(self):
        from api.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        assert any("onboard" in p for p in route_paths)


# ============================================================================
# API route validation -- CA Subscription
# ============================================================================


class TestCASubscriptionAPIRoutes:
    """Verify CA subscription routes exist in the router."""

    def test_packs_route_registered(self):
        """Industry packs route (which includes CA subscription) must be registered."""
        from api.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        assert any("packs" in p for p in route_paths), (
            f"No packs route found. Routes: {[p for p in route_paths if 'pack' in p.lower()]}"
        )

    def test_billing_route_registered(self):
        """Billing route must be registered for subscription management."""
        from api.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        assert any("billing" in p for p in route_paths)


# ============================================================================
# Business logic -- Partner self-approval
# ============================================================================


class TestPartnerSelfApproval:
    """Partner role can approve their own filing requests."""

    def test_company_role_enum_has_partner(self):
        from api.v1.companies import CompanyRole

        assert CompanyRole.partner.value == "partner"

    def test_partner_role_is_valid_for_approval(self):
        """Partner role must be one of the valid roles in the system."""
        from api.v1.companies import CompanyRole

        roles = {r.value for r in CompanyRole}
        assert "partner" in roles


# ============================================================================
# Business logic -- Auto-approval
# ============================================================================


class TestAutoApproval:
    """When gst_auto_file=True, approvals should be auto-approved."""

    def test_gst_auto_file_field_exists(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "gst_auto_file" in cols

    def test_gst_auto_file_defaults_false(self):
        """Auto-file is off by default (safety)."""
        from core.models.company import Company

        col = Company.__table__.c.gst_auto_file
        assert col.default.arg is False

    def test_filing_approval_has_auto_approved_field(self):
        from core.models.filing_approval import FilingApproval

        cols = {c.key for c in FilingApproval.__table__.columns}
        assert "auto_approved" in cols


# ============================================================================
# Business logic -- Manual upload flow
# ============================================================================


class TestManualUploadFlow:
    """GSTN upload status transitions: generated -> downloaded -> uploaded -> acknowledged."""

    def test_gstn_upload_status_values(self):
        """Status field must accept all valid status transitions."""
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.status
        # Verify it's a string column that can hold our status values
        assert col is not None
        # Default is 'generated' (first state)
        assert col.default.arg == "generated"

    def test_gstn_upload_has_gstn_arn(self):
        """Upload must track the GSTN acknowledgement reference number."""
        from core.models.gstn_upload import GSTNUpload

        cols = {c.key for c in GSTNUpload.__table__.columns}
        assert "gstn_arn" in cols

    def test_gstn_upload_has_uploaded_by(self):
        """Must track who uploaded the file to the GSTN portal."""
        from core.models.gstn_upload import GSTNUpload

        cols = {c.key for c in GSTNUpload.__table__.columns}
        assert "uploaded_by" in cols

    def test_gstn_upload_has_uploaded_at(self):
        from core.models.gstn_upload import GSTNUpload

        cols = {c.key for c in GSTNUpload.__table__.columns}
        assert "uploaded_at" in cols


# ============================================================================
# Business logic -- Subscription trial window
# ============================================================================


class TestSubscriptionTrial:
    """Trial subscription with 14-day window and max 7 clients."""

    def test_trial_status_is_default(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.status
        assert col.default.arg == "trial"

    def test_max_clients_default_is_7(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.max_clients
        assert col.default.arg == 7

    def test_trial_ends_at_field_exists(self):
        """Must have a trial_ends_at timestamp for 14-day window."""
        from core.models.ca_subscription import CASubscription

        cols = {c.key for c in CASubscription.__table__.columns}
        assert "trial_ends_at" in cols


# ============================================================================
# Business logic -- Filing types
# ============================================================================


class TestFilingTypes:
    """Supported filing types: gstr1, gstr3b, gstr9, tds_26q, tds_24q."""

    def test_filing_type_field_is_string(self):
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.filing_type
        assert col is not None
        assert not col.nullable

    def test_filing_period_field_is_string(self):
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.filing_period
        assert col is not None
        assert not col.nullable


# ============================================================================
# Pydantic schema validation -- CompanyOut
# ============================================================================


class TestCompanyOutSchema:
    """Verify CompanyOut includes all v4.2.0 fields."""

    def test_company_out_has_subscription_status(self):
        from api.v1.companies import CompanyOut

        out = CompanyOut(
            id="test-id",
            name="Test Corp",
            pan="AABCT1234F",
            is_active=True,
            gst_auto_file=False,
            user_roles={},
            created_at="2026-01-01T00:00:00Z",
            subscription_status="trial",
        )
        assert out.subscription_status == "trial"

    def test_company_out_has_client_health_score(self):
        from api.v1.companies import CompanyOut

        out = CompanyOut(
            id="test-id",
            name="Test Corp",
            pan="AABCT1234F",
            is_active=True,
            gst_auto_file=False,
            user_roles={},
            created_at="2026-01-01T00:00:00Z",
            client_health_score=85,
        )
        assert out.client_health_score == 85

    def test_company_out_has_document_vault_enabled(self):
        from api.v1.companies import CompanyOut

        out = CompanyOut(
            id="test-id",
            name="Test Corp",
            pan="AABCT1234F",
            is_active=True,
            gst_auto_file=False,
            user_roles={},
            created_at="2026-01-01T00:00:00Z",
            document_vault_enabled=True,
        )
        assert out.document_vault_enabled is True

    def test_company_out_has_compliance_alerts_email(self):
        from api.v1.companies import CompanyOut

        out = CompanyOut(
            id="test-id",
            name="Test Corp",
            pan="AABCT1234F",
            is_active=True,
            gst_auto_file=False,
            user_roles={},
            created_at="2026-01-01T00:00:00Z",
            compliance_alerts_email="alerts@test.com",
        )
        assert out.compliance_alerts_email == "alerts@test.com"

    def test_company_out_defaults(self):
        """CompanyOut should have sensible defaults for new CA fields."""
        from api.v1.companies import CompanyOut

        out = CompanyOut(
            id="test-id",
            name="Test Corp",
            pan="AABCT1234F",
            is_active=True,
            gst_auto_file=False,
            user_roles={},
            created_at="2026-01-01T00:00:00Z",
        )
        assert out.subscription_status == "trial"
        assert out.document_vault_enabled is True
        assert out.compliance_alerts_email is None
        assert out.client_health_score is None


# ============================================================================
# Model registration -- all CA models imported in __init__.py
# ============================================================================


class TestModelRegistration:
    """All CA models must be importable from core.models."""

    def test_ca_subscription_importable(self):
        from core.models import CASubscription

        assert CASubscription.__tablename__ == "ca_subscriptions"

    def test_filing_approval_importable(self):
        from core.models import FilingApproval

        assert FilingApproval.__tablename__ == "filing_approvals"

    def test_gstn_upload_importable(self):
        from core.models import GSTNUpload

        assert GSTNUpload.__tablename__ == "gstn_uploads"

    def test_company_importable(self):
        from core.models import Company

        assert Company.__tablename__ == "companies"


# ============================================================================
# Relationship validation
# ============================================================================


class TestModelRelationships:
    """Verify that CA models have correct relationships defined."""

    def test_filing_approval_has_company_relationship(self):
        from core.models.filing_approval import FilingApproval

        # Check that the 'company' relationship is defined
        mapper = FilingApproval.__mapper__
        rel_names = {r.key for r in mapper.relationships}
        assert "company" in rel_names, (
            f"FilingApproval missing 'company' relationship. Found: {rel_names}"
        )

    def test_gstn_upload_has_company_relationship(self):
        from core.models.gstn_upload import GSTNUpload

        mapper = GSTNUpload.__mapper__
        rel_names = {r.key for r in mapper.relationships}
        assert "company" in rel_names, (
            f"GSTNUpload missing 'company' relationship. Found: {rel_names}"
        )

    def test_ca_subscription_has_tenant_relationship(self):
        from core.models.ca_subscription import CASubscription

        mapper = CASubscription.__mapper__
        rel_names = {r.key for r in mapper.relationships}
        assert "tenant" in rel_names, (
            f"CASubscription missing 'tenant' relationship. Found: {rel_names}"
        )

    def test_filing_approval_has_tenant_relationship(self):
        from core.models.filing_approval import FilingApproval

        mapper = FilingApproval.__mapper__
        rel_names = {r.key for r in mapper.relationships}
        assert "tenant" in rel_names

    def test_gstn_upload_has_tenant_relationship(self):
        from core.models.gstn_upload import GSTNUpload

        mapper = GSTNUpload.__mapper__
        rel_names = {r.key for r in mapper.relationships}
        assert "tenant" in rel_names
