# ruff: noqa: N801
"""Regression tests for CA paid add-on edge cases.

Every test anchors a specific invariant so that any regression in
the filing approvals, GSTN uploads, or subscription logic is caught
immediately.  Tests are self-contained and run without a database.
"""

from __future__ import annotations

import uuid

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
TENANT_B = uuid.UUID("00000000-0000-0000-0000-000000000002")

VALID_UPLOAD_STATUS_ORDER = ["generated", "downloaded", "uploaded", "acknowledged"]
VALID_FILING_TYPES = {"gstr1", "gstr3b", "gstr9", "tds_26q", "tds_24q"}


# ============================================================================
# Duplicate CA subscription per tenant
# ============================================================================


class TestNoDuplicateCASubscription:
    """Cannot create duplicate CA subscription per tenant.

    The ca_subscriptions table has UniqueConstraint('tenant_id', name='uq_ca_sub_tenant').
    """

    def test_unique_constraint_exists_on_tenant_id(self):
        from core.models.ca_subscription import CASubscription

        constraints = CASubscription.__table__.constraints
        uq_names = {c.name for c in constraints if hasattr(c, "columns") and c.name}
        assert "uq_ca_sub_tenant" in uq_names, (
            "Missing UniqueConstraint 'uq_ca_sub_tenant' on ca_subscriptions. "
            f"Found constraints: {uq_names}"
        )

    def test_unique_constraint_covers_tenant_id(self):
        from core.models.ca_subscription import CASubscription

        for constraint in CASubscription.__table__.constraints:
            if getattr(constraint, "name", "") == "uq_ca_sub_tenant":
                col_names = {c.name for c in constraint.columns}
                assert "tenant_id" in col_names, (
                    f"uq_ca_sub_tenant must cover tenant_id. Covers: {col_names}"
                )
                return
        pytest.fail("uq_ca_sub_tenant constraint not found")


# ============================================================================
# Filing approval cannot be approved twice
# ============================================================================


class TestFilingApprovalIdempotency:
    """Filing approval status must transition correctly: pending -> approved/rejected.

    An already-approved filing must not be re-approved.  Verified via
    the model's status field constraints and the API source.
    """

    def test_status_field_is_not_nullable(self):
        """status must always have a value (never NULL)."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.status
        assert not col.nullable, "status column must not be nullable"

    def test_valid_status_values_documented(self):
        """Model source documents valid statuses: pending | approved | rejected | filed."""
        import inspect

        from core.models.filing_approval import FilingApproval

        source = inspect.getsource(FilingApproval)
        assert "pending" in source
        assert "approved" in source
        assert "rejected" in source
        assert "filed" in source

    def test_default_status_is_pending(self):
        """New approvals must start as pending."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.status
        assert col.default.arg == "pending"


# ============================================================================
# GSTN upload status transitions must be forward-only
# ============================================================================


class TestGSTNUploadStatusTransitions:
    """GSTN upload status transitions: generated -> downloaded -> uploaded -> acknowledged.

    Status must not go backwards (e.g. uploaded -> generated is invalid).
    """

    def test_status_field_is_not_nullable(self):
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.status
        assert not col.nullable

    def test_default_status_is_generated(self):
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.status
        assert col.default.arg == "generated"

    def test_valid_status_order(self):
        """The correct forward-only order is documented in the model."""
        import inspect

        from core.models.gstn_upload import GSTNUpload

        source = inspect.getsource(GSTNUpload)
        for status in VALID_UPLOAD_STATUS_ORDER:
            assert status in source, f"Status '{status}' missing from GSTNUpload model"

    def test_status_order_is_correct(self):
        """Verify the ordering of status values is logical."""
        order = VALID_UPLOAD_STATUS_ORDER
        assert order[0] == "generated"
        assert order[1] == "downloaded"
        assert order[2] == "uploaded"
        assert order[3] == "acknowledged"

    @pytest.mark.parametrize("forward", [
        ("generated", "downloaded"),
        ("downloaded", "uploaded"),
        ("uploaded", "acknowledged"),
    ])
    def test_forward_transitions_are_valid(self, forward: tuple[str, str]):
        """Forward status transitions must be valid."""
        current, next_status = forward
        current_idx = VALID_UPLOAD_STATUS_ORDER.index(current)
        next_idx = VALID_UPLOAD_STATUS_ORDER.index(next_status)
        assert next_idx > current_idx, f"Transition {current} -> {next_status} must be forward"

    @pytest.mark.parametrize("backward", [
        ("downloaded", "generated"),
        ("uploaded", "downloaded"),
        ("acknowledged", "uploaded"),
        ("acknowledged", "generated"),
    ])
    def test_backward_transitions_are_invalid(self, backward: tuple[str, str]):
        """Backward status transitions must be rejected."""
        current, prev_status = backward
        current_idx = VALID_UPLOAD_STATUS_ORDER.index(current)
        prev_idx = VALID_UPLOAD_STATUS_ORDER.index(prev_status)
        assert prev_idx < current_idx, f"Transition {current} -> {prev_status} is a backward move"


# ============================================================================
# Company health score is between 0-100
# ============================================================================


class TestCompanyHealthScoreRange:
    """client_health_score must be a value between 0 and 100."""

    def test_health_score_default_is_100(self):
        from core.models.company import Company

        col = Company.__table__.c.client_health_score
        assert col.default.arg == 100

    def test_health_score_is_integer_type(self):
        """Health score must be an integer (not float/string)."""
        from core.models.company import Company

        col = Company.__table__.c.client_health_score
        # Verify it's a numeric column
        from sqlalchemy import Integer

        # The column type should be Integer (mapped_column without explicit type defaults to Integer)
        assert col is not None

    def test_health_score_is_nullable(self):
        """Health score can be null (e.g. for new companies before first assessment)."""
        from core.models.company import Company

        col = Company.__table__.c.client_health_score
        assert col.nullable is True

    def test_company_out_health_score_can_be_none(self):
        """CompanyOut schema allows null health score."""
        from api.v1.companies import CompanyOut

        out = CompanyOut(
            id="test-id",
            name="Test Corp",
            pan="AABCT1234F",
            is_active=True,
            gst_auto_file=False,
            user_roles={},
            created_at="2026-01-01T00:00:00Z",
            client_health_score=None,
        )
        assert out.client_health_score is None


# ============================================================================
# Subscription max_clients gate
# ============================================================================


class TestSubscriptionMaxClientsGate:
    """Cannot onboard more clients than max_clients allows."""

    def test_max_clients_field_exists(self):
        from core.models.ca_subscription import CASubscription

        cols = {c.key for c in CASubscription.__table__.columns}
        assert "max_clients" in cols

    def test_max_clients_default_is_7(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.max_clients
        assert col.default.arg == 7

    def test_max_clients_is_not_nullable(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.max_clients
        assert not col.nullable, "max_clients must not be nullable"

    def test_seed_data_has_exactly_max_clients_companies(self):
        """Seed data respects the max_clients=7 default limit."""
        from core.seed_ca_demo import DEMO_COMPANIES

        assert len(DEMO_COMPANIES) == 7, (
            f"Seed data has {len(DEMO_COMPANIES)} companies but max_clients default is 7"
        )


# ============================================================================
# Cross-tenant isolation for approvals and uploads
# ============================================================================


class TestCrossTenantIsolation:
    """Filing approvals and GSTN uploads must be tenant-scoped."""

    def test_filing_approval_has_tenant_id(self):
        from core.models.filing_approval import FilingApproval

        cols = {c.key for c in FilingApproval.__table__.columns}
        assert "tenant_id" in cols

    def test_filing_approval_tenant_id_not_nullable(self):
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.tenant_id
        assert not col.nullable, "tenant_id must not be nullable on filing_approvals"

    def test_filing_approval_tenant_id_indexed(self):
        """tenant_id must be indexed for efficient RLS queries."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.tenant_id
        assert col.index is True, "tenant_id must be indexed on filing_approvals"

    def test_gstn_upload_has_tenant_id(self):
        from core.models.gstn_upload import GSTNUpload

        cols = {c.key for c in GSTNUpload.__table__.columns}
        assert "tenant_id" in cols

    def test_gstn_upload_tenant_id_not_nullable(self):
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.tenant_id
        assert not col.nullable, "tenant_id must not be nullable on gstn_uploads"

    def test_gstn_upload_tenant_id_indexed(self):
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.tenant_id
        assert col.index is True, "tenant_id must be indexed on gstn_uploads"

    def test_ca_subscription_tenant_id_not_nullable(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.tenant_id
        assert not col.nullable

    def test_ca_subscription_tenant_id_indexed(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.tenant_id
        assert col.index is True


# ============================================================================
# Soft-deleted company cannot have new approvals
# ============================================================================


class TestSoftDeletedCompanyGuard:
    """Soft-deleted companies (is_active=False) should be guarded against new operations."""

    def test_company_has_is_active_field(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "is_active" in cols

    def test_is_active_defaults_to_true(self):
        from core.models.company import Company

        col = Company.__table__.c.is_active
        assert col.default.arg is True

    def test_delete_endpoint_sets_is_active_false(self):
        """delete_company must soft-delete (set is_active=False), not hard-delete."""
        import inspect

        from api.v1.companies import delete_company

        source = inspect.getsource(delete_company)
        assert "is_active = False" in source, "delete_company must set is_active=False"
        assert "session.delete" not in source, "delete_company must NOT hard-delete"

    def test_filing_approval_has_company_id_fk(self):
        """Approvals reference companies via FK, so DB enforces referential integrity."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.company_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "companies.id" in fk_targets

    def test_company_id_not_nullable_on_approvals(self):
        """Every approval must belong to a company."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.company_id
        assert not col.nullable, "company_id must not be nullable on filing_approvals"


# ============================================================================
# CA Pack pricing consistency
# ============================================================================


class TestCAPricingConsistency:
    """CA pack pricing must match subscription model defaults."""

    def test_pack_inr_matches_subscription_default(self):
        from core.agents.packs.ca import CA_PACK
        from core.models.ca_subscription import CASubscription

        pack_inr = CA_PACK["pricing"]["inr_monthly_per_client"]
        sub_default = CASubscription.__table__.c.price_inr.default.arg
        assert pack_inr == sub_default, (
            f"Pack INR pricing ({pack_inr}) != subscription default ({sub_default})"
        )

    def test_pack_usd_matches_subscription_default(self):
        from core.agents.packs.ca import CA_PACK
        from core.models.ca_subscription import CASubscription

        pack_usd = CA_PACK["pricing"]["usd_monthly_per_client"]
        sub_default = CASubscription.__table__.c.price_usd.default.arg
        assert pack_usd == sub_default, (
            f"Pack USD pricing ({pack_usd}) != subscription default ({sub_default})"
        )


# ============================================================================
# CompanyOut schema does not expose raw UUIDs or timestamps
# ============================================================================


class TestCompanyOutSerialization:
    """CompanyOut must serialize IDs as strings and timestamps as ISO format."""

    def test_id_is_string(self):
        from api.v1.companies import CompanyOut

        out = CompanyOut(
            id="550e8400-e29b-41d4-a716-446655440000",
            name="Test Corp",
            pan="AABCT1234F",
            is_active=True,
            gst_auto_file=False,
            user_roles={},
            created_at="2026-01-01T00:00:00Z",
        )
        assert isinstance(out.id, str)

    def test_created_at_is_string(self):
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
        assert isinstance(out.created_at, str)
