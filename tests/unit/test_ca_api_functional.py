# ruff: noqa: N801
"""Functional API tests for CA feature endpoints.

Tests verify endpoint behavior at the schema/model/source level
without requiring a running database (no integration fixtures).
Covers:
- Filing Approvals API (create, auto-approve, reject, list, 403/409)
- GSTN Credentials API (encryption, no-password-in-output, delete=deactivate)
- GSTN Uploads API (create, status transitions, ARN)
- Bulk Approval (mixed success/failure)
- Subscription API (trial creation, already-active 409, reactivation)
- Partner Dashboard (KPIs, deadlines)
- Tally Detect (stub response)
- Compliance Deadlines (generate, list sorted, mark filed)
"""

from __future__ import annotations

import inspect

# ============================================================================
# Filing Approvals API — schemas
# ============================================================================


class TestFilingApprovalSchemas:
    """Verify Filing Approval Pydantic schemas and field defaults."""

    def test_filing_approval_create_required_fields(self):
        """FilingApprovalCreate requires filing_type and filing_period."""
        from api.v1.companies import FilingApprovalCreate

        body = FilingApprovalCreate(
            filing_type="gstr1",
            filing_period="2026-03",
        )
        assert body.filing_type == "gstr1"
        assert body.filing_period == "2026-03"
        assert body.filing_data == {}

    def test_filing_approval_create_with_data(self):
        """FilingApprovalCreate accepts optional filing_data dict."""
        from api.v1.companies import FilingApprovalCreate

        body = FilingApprovalCreate(
            filing_type="gstr3b",
            filing_period="2026-03",
            filing_data={"liability": 50000, "itc_claimed": 20000},
        )
        assert body.filing_data["liability"] == 50000

    def test_filing_approval_out_has_all_fields(self):
        """FilingApprovalOut schema has every expected field."""
        from api.v1.companies import FilingApprovalOut

        fields = set(FilingApprovalOut.model_fields.keys())
        expected = {
            "id", "company_id", "filing_type", "filing_period",
            "filing_data", "status", "requested_by", "approved_by",
            "approved_at", "rejection_reason", "auto_approved",
            "created_at", "updated_at",
        }
        assert expected.issubset(fields), f"Missing fields: {expected - fields}"

    def test_filing_approval_out_defaults(self):
        """FilingApprovalOut defaults for nullable fields."""
        from api.v1.companies import FilingApprovalOut

        out = FilingApprovalOut(
            id="test-id",
            company_id="comp-id",
            filing_type="gstr1",
            filing_period="2026-03",
            status="pending",
            requested_by="user@test.com",
            created_at="2026-01-01T00:00:00Z",
        )
        assert out.approved_by is None
        assert out.approved_at is None
        assert out.rejection_reason is None
        assert out.auto_approved is False
        assert out.filing_data == {}

    def test_filing_approval_list_out_schema(self):
        """FilingApprovalListOut has items and total."""
        from api.v1.companies import FilingApprovalListOut

        out = FilingApprovalListOut(items=[], total=0)
        assert out.total == 0
        assert len(out.items) == 0


# ============================================================================
# Filing Approvals API — endpoint source verification
# ============================================================================


class TestFilingApprovalEndpoints:
    """Verify Filing Approval endpoint logic via source inspection."""

    def test_create_approval_uses_session(self):
        """create_filing_approval must use DB session, not in-memory."""
        from api.v1.companies import create_filing_approval

        source = inspect.getsource(create_filing_approval)
        assert "session" in source
        assert "session.add" in source
        assert "session.flush" in source

    def test_create_always_pending(self):
        """create_filing_approval must always create in pending state.

        Session 4 BUG-002: auto-approval on create skipped the partner review
        UI entirely, which broke the approval workflow. Auto-filing is now a
        downstream concern, not an approval-creation short-circuit.
        """
        from api.v1.companies import create_filing_approval

        source = inspect.getsource(create_filing_approval)
        assert 'status="pending"' in source
        assert "auto_approved=False" in source
        # Must NOT branch status on gst_auto_file at creation time.
        assert '"approved" if is_auto' not in source

    def test_partner_self_approval_checks_role(self):
        """approve_filing checks that user has partner role."""
        from api.v1.companies import approve_filing

        source = inspect.getsource(approve_filing)
        assert "partner" in source
        assert "user_roles" in source

    def test_non_partner_gets_403(self):
        """approve_filing raises 403 for non-partner users."""
        from api.v1.companies import approve_filing

        source = inspect.getsource(approve_filing)
        assert "403" in source
        assert "Only partners can approve filings" in source

    def test_cannot_approve_non_pending(self):
        """approve_filing raises 409 if status is not pending."""
        from api.v1.companies import approve_filing

        source = inspect.getsource(approve_filing)
        assert "409" in source
        assert 'status != "pending"' in source

    def test_cannot_reject_non_pending(self):
        """reject_filing raises 409 if status is not pending."""
        from api.v1.companies import reject_filing

        source = inspect.getsource(reject_filing)
        assert "409" in source
        assert 'status != "pending"' in source

    def test_reject_stores_rejection_reason(self):
        """reject_filing sets rejection_reason on the approval."""
        from api.v1.companies import reject_filing

        source = inspect.getsource(reject_filing)
        assert "rejection_reason" in source

    def test_list_approvals_filters_by_status(self):
        """list_filing_approvals supports status filter."""
        from api.v1.companies import list_filing_approvals

        source = inspect.getsource(list_filing_approvals)
        assert "status" in source
        assert "FilingApproval.status" in source

    def test_list_approvals_filters_by_filing_type(self):
        """list_filing_approvals supports filing_type filter."""
        from api.v1.companies import list_filing_approvals

        source = inspect.getsource(list_filing_approvals)
        assert "filing_type" in source
        assert "FilingApproval.filing_type" in source


# ============================================================================
# GSTN Credentials API — schemas
# ============================================================================


class TestGSTNCredentialSchemas:
    """Verify GSTN Credential Pydantic schemas."""

    def test_credential_create_has_password_field(self):
        """GSTNCredentialCreate includes a password field."""
        from api.v1.companies import GSTNCredentialCreate

        fields = set(GSTNCredentialCreate.model_fields.keys())
        assert "password" in fields

    def test_credential_out_excludes_password_encrypted(self):
        """GSTNCredentialOut must NOT expose password_encrypted."""
        from api.v1.companies import GSTNCredentialOut

        fields = set(GSTNCredentialOut.model_fields.keys())
        assert "password_encrypted" not in fields

    def test_credential_out_excludes_password(self):
        """GSTNCredentialOut must NOT expose password."""
        from api.v1.companies import GSTNCredentialOut

        fields = set(GSTNCredentialOut.model_fields.keys())
        assert "password" not in fields

    def test_credential_out_has_expected_fields(self):
        """GSTNCredentialOut has all expected fields."""
        from api.v1.companies import GSTNCredentialOut

        fields = set(GSTNCredentialOut.model_fields.keys())
        expected = {
            "id", "company_id", "gstin", "username",
            "portal_type", "is_active", "last_verified_at", "created_at",
        }
        assert expected.issubset(fields), f"Missing: {expected - fields}"

    def test_credential_create_default_portal_type(self):
        """GSTNCredentialCreate defaults portal_type to 'gstn'."""
        from api.v1.companies import GSTNCredentialCreate

        body = GSTNCredentialCreate(
            gstin="29AABCT1234F1Z5",
            username="testuser",
            password="secret123",
        )
        assert body.portal_type == "gstn"


# ============================================================================
# GSTN Credentials API — endpoint source verification
# ============================================================================


class TestGSTNCredentialEndpoints:
    """Verify GSTN Credential endpoint logic."""

    def test_create_credential_encrypts_password(self):
        """create_gstn_credential encrypts the password before storage.

        After v4.7.0 the call was migrated from the legacy Fernet
        ``encrypt_credential`` to the tenant-aware
        ``encrypt_for_tenant`` helper, which automatically picks BYOK
        envelope encryption when the tenant has a KMS key configured.
        Either name in the source counts as wired.
        """
        from api.v1.companies import create_gstn_credential

        source = inspect.getsource(create_gstn_credential)
        assert "encrypt_for_tenant" in source or "encrypt_credential" in source
        assert "password_encrypted" in source

    def test_password_is_actually_encrypted(self):
        """encrypt_credential produces different output from input."""
        from core.crypto import encrypt_credential

        plaintext = "my-secret-password"
        encrypted = encrypt_credential(plaintext)
        assert encrypted != plaintext
        assert len(encrypted) > len(plaintext)

    def test_delete_deactivates_not_hard_delete(self):
        """deactivate_gstn_credential sets is_active=False, not session.delete."""
        from api.v1.companies import deactivate_gstn_credential

        source = inspect.getsource(deactivate_gstn_credential)
        assert "is_active = False" in source
        assert "session.delete" not in source

    def test_verify_endpoint_calls_decrypt(self):
        """verify_gstn_credential decrypts the stored ciphertext.

        Migrated to ``decrypt_for_tenant`` in v4.7.0 to support
        BYOK envelope encryption transparently. Either name counts.
        """
        from api.v1.companies import verify_gstn_credential

        source = inspect.getsource(verify_gstn_credential)
        assert "decrypt_for_tenant" in source or "decrypt_credential" in source

    def test_credential_unique_constraint_model(self):
        """GSTNCredential has unique constraint on (company_id, portal_type)."""
        from core.models.gstn_credential import GSTNCredential

        constraints = GSTNCredential.__table__.constraints
        uq_names = {c.name for c in constraints if hasattr(c, "columns")}
        assert "uq_gstn_cred_company" in uq_names


# ============================================================================
# GSTN Uploads API — schemas
# ============================================================================


class TestGSTNUploadSchemas:
    """Verify GSTN Upload Pydantic schemas."""

    def test_upload_create_required_fields(self):
        """GSTNUploadCreate requires upload_type and filing_period."""
        from api.v1.companies import GSTNUploadCreate

        body = GSTNUploadCreate(
            upload_type="gstr1_json",
            filing_period="2026-03",
        )
        assert body.upload_type == "gstr1_json"
        assert body.filing_period == "2026-03"

    def test_upload_status_update_schema(self):
        """GSTNUploadStatusUpdate accepts status and optional gstn_arn."""
        from api.v1.companies import GSTNUploadStatusUpdate

        body = GSTNUploadStatusUpdate(
            status="uploaded",
            gstn_arn="ARN-2026-03-001",
        )
        assert body.status == "uploaded"
        assert body.gstn_arn == "ARN-2026-03-001"

    def test_upload_out_has_all_fields(self):
        """GSTNUploadOut schema has all expected fields."""
        from api.v1.companies import GSTNUploadOut

        fields = set(GSTNUploadOut.model_fields.keys())
        expected = {
            "id", "company_id", "upload_type", "filing_period",
            "file_name", "file_path", "file_size_bytes", "status",
            "gstn_arn", "uploaded_at", "uploaded_by",
            "created_at", "updated_at",
        }
        assert expected.issubset(fields), f"Missing: {expected - fields}"


# ============================================================================
# GSTN Uploads API — endpoint source verification
# ============================================================================


class TestGSTNUploadEndpoints:
    """Verify GSTN Upload endpoint logic."""

    def test_create_upload_defaults_to_generated(self):
        """create_gstn_upload sets initial status to 'generated'."""
        from api.v1.companies import create_gstn_upload

        source = inspect.getsource(create_gstn_upload)
        assert '"generated"' in source

    def test_status_transitions_validated(self):
        """update_gstn_upload validates status against allowed set."""
        from api.v1.companies import update_gstn_upload

        source = inspect.getsource(update_gstn_upload)
        assert "generated" in source
        assert "downloaded" in source
        assert "uploaded" in source
        assert "acknowledged" in source

    def test_upload_status_uploaded_sets_meta(self):
        """update_gstn_upload sets uploaded_at and uploaded_by on status=uploaded."""
        from api.v1.companies import update_gstn_upload

        source = inspect.getsource(update_gstn_upload)
        assert "uploaded_at" in source
        assert "uploaded_by" in source

    def test_arn_set_on_patch(self):
        """update_gstn_upload sets gstn_arn when provided."""
        from api.v1.companies import update_gstn_upload

        source = inspect.getsource(update_gstn_upload)
        assert "gstn_arn" in source

    def test_upload_model_status_default(self):
        """GSTNUpload model status column defaults to 'generated'."""
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.status
        assert col.default is not None
        assert col.default.arg == "generated"


# ============================================================================
# Bulk Approval API — schemas
# ============================================================================


class TestBulkApprovalSchemas:
    """Verify Bulk Approval Pydantic schemas."""

    def test_bulk_approve_request_schema(self):
        """BulkApproveRequest accepts a list of approval_ids."""
        from api.v1.companies import BulkApproveRequest

        body = BulkApproveRequest(approval_ids=["id1", "id2", "id3"])
        assert len(body.approval_ids) == 3

    def test_bulk_approve_response_schema(self):
        """BulkApproveResponse has approved and failed lists."""
        from api.v1.companies import BulkApproveResponse

        resp = BulkApproveResponse(
            approved=["id1"],
            failed=[{"id": "id2", "reason": "not found"}],
        )
        assert len(resp.approved) == 1
        assert len(resp.failed) == 1
        assert resp.failed[0]["reason"] == "not found"

    def test_bulk_approve_empty_request(self):
        """BulkApproveRequest accepts empty list."""
        from api.v1.companies import BulkApproveRequest

        body = BulkApproveRequest(approval_ids=[])
        assert len(body.approval_ids) == 0

    def test_bulk_approve_empty_response(self):
        """BulkApproveResponse accepts empty lists."""
        from api.v1.companies import BulkApproveResponse

        resp = BulkApproveResponse(approved=[], failed=[])
        assert len(resp.approved) == 0
        assert len(resp.failed) == 0


# ============================================================================
# Bulk Approval API — endpoint source verification
# ============================================================================


class TestBulkApprovalEndpoints:
    """Verify Bulk Approval endpoint logic."""

    def test_bulk_approve_checks_pending_status(self):
        """bulk_approve_filings checks approval is pending before approving."""
        from api.v1.companies import bulk_approve_filings

        source = inspect.getsource(bulk_approve_filings)
        assert '"pending"' in source

    def test_bulk_approve_returns_failed_for_non_pending(self):
        """bulk_approve_filings adds to failed list when status is not pending."""
        from api.v1.companies import bulk_approve_filings

        source = inspect.getsource(bulk_approve_filings)
        assert "failed.append" in source
        assert "already" in source.lower()

    def test_bulk_approve_returns_failed_for_not_found(self):
        """bulk_approve_filings adds to failed list when approval not found."""
        from api.v1.companies import bulk_approve_filings

        source = inspect.getsource(bulk_approve_filings)
        assert "Approval not found" in source

    def test_bulk_approve_checks_partner_role(self):
        """bulk_approve_filings checks partner role on the associated company."""
        from api.v1.companies import bulk_approve_filings

        source = inspect.getsource(bulk_approve_filings)
        assert "partner" in source
        assert "user_roles" in source


# ============================================================================
# Subscription API — schemas
# ============================================================================


class TestSubscriptionSchemas:
    """Verify CA Subscription Pydantic schemas."""

    def test_subscription_out_has_all_fields(self):
        """CASubscriptionOut has all expected fields."""
        from api.v1.companies import CASubscriptionOut

        fields = set(CASubscriptionOut.model_fields.keys())
        expected = {
            "id", "tenant_id", "plan", "status", "max_clients",
            "price_inr", "price_usd", "billing_cycle",
            "trial_ends_at", "current_period_start", "current_period_end",
            "cancelled_at", "created_at", "updated_at",
        }
        assert expected.issubset(fields), f"Missing: {expected - fields}"

    def test_subscription_activate_defaults(self):
        """CASubscriptionActivate defaults to ca_pro plan, monthly billing."""
        from api.v1.companies import CASubscriptionActivate

        body = CASubscriptionActivate()
        assert body.plan == "ca_pro"
        assert body.billing_cycle == "monthly"


# ============================================================================
# Subscription API — endpoint source verification
# ============================================================================


class TestSubscriptionEndpoints:
    """Verify CA Subscription endpoint logic."""

    def test_activate_creates_trial(self):
        """activate_ca_subscription creates trial with 14-day period."""
        from api.v1.companies import activate_ca_subscription

        source = inspect.getsource(activate_ca_subscription)
        assert '"trial"' in source
        assert "timedelta(days=14)" in source

    def test_activate_trial_has_7_clients(self):
        """Trial subscription allows max 7 clients."""
        from api.v1.companies import activate_ca_subscription

        source = inspect.getsource(activate_ca_subscription)
        assert "max_clients=7" in source

    def test_activate_already_active_returns_409(self):
        """activate_ca_subscription raises 409 when subscription is already active."""
        from api.v1.companies import activate_ca_subscription

        source = inspect.getsource(activate_ca_subscription)
        assert "409" in source
        assert "already active" in source

    def test_reactivate_from_cancelled(self):
        """activate_ca_subscription reactivates from cancelled/expired."""
        from api.v1.companies import activate_ca_subscription

        source = inspect.getsource(activate_ca_subscription)
        assert '"cancelled"' in source
        assert '"expired"' in source
        assert '"active"' in source

    def test_activate_reconciles_ca_pack_install(self):
        """activate_ca_subscription repairs missing ca-firm install state."""
        from api.v1.companies import activate_ca_subscription

        source = inspect.getsource(activate_ca_subscription)
        assert "_ensure_ca_pack_ready" in source

    def test_subscription_model_defaults(self):
        """CASubscription model has correct defaults."""
        from core.models.ca_subscription import CASubscription

        assert CASubscription.__table__.c.status.default.arg == "trial"
        assert CASubscription.__table__.c.max_clients.default.arg == 7
        assert CASubscription.__table__.c.price_inr.default.arg == 4999
        assert CASubscription.__table__.c.price_usd.default.arg == 59


# ============================================================================
# Partner Dashboard — schemas and endpoint verification
# ============================================================================


class TestPartnerDashboardSchemas:
    """Verify Partner Dashboard schema and logic."""

    def test_partner_dashboard_out_has_all_fields(self):
        """PartnerDashboardOut has all KPI fields."""
        from api.v1.companies import PartnerDashboardOut

        fields = set(PartnerDashboardOut.model_fields.keys())
        expected = {
            "total_clients", "active_clients", "avg_health_score",
            "total_pending_filings", "total_overdue",
            "revenue_per_month_inr", "clients", "upcoming_deadlines",
        }
        assert expected.issubset(fields), f"Missing: {expected - fields}"

    def test_partner_dashboard_counts_active_clients(self):
        """get_partner_dashboard counts active clients from is_active."""
        from api.v1.companies import get_partner_dashboard

        source = inspect.getsource(get_partner_dashboard)
        assert "is_active" in source
        assert "active_clients" in source

    def test_partner_dashboard_computes_avg_health(self):
        """get_partner_dashboard computes average health score."""
        from api.v1.companies import get_partner_dashboard

        source = inspect.getsource(get_partner_dashboard)
        assert "health_score" in source
        assert "avg_health" in source

    def test_partner_dashboard_counts_pending_filings(self):
        """get_partner_dashboard queries pending filing approvals."""
        from api.v1.companies import get_partner_dashboard

        source = inspect.getsource(get_partner_dashboard)
        assert '"pending"' in source
        assert "FilingApproval.status" in source

    def test_partner_dashboard_upcoming_deadlines_within_30_days(self):
        """get_partner_dashboard filters deadlines within 30 days."""
        from api.v1.companies import get_partner_dashboard

        source = inspect.getsource(get_partner_dashboard)
        assert "timedelta(days=30)" in source

    def test_partner_dashboard_revenue_calculation(self):
        """get_partner_dashboard calculates revenue from active companies."""
        from api.v1.companies import get_partner_dashboard

        source = inspect.getsource(get_partner_dashboard)
        assert "4999" in source
        assert "revenue_per_month_inr" in source

    def test_partner_dashboard_reconciles_ca_pack_install(self):
        """get_partner_dashboard repairs missing ca-firm install state."""
        from api.v1.companies import get_partner_dashboard

        source = inspect.getsource(get_partner_dashboard)
        assert "_ensure_ca_pack_ready" in source


class TestCACompanyEndpointReconciliation:
    """Verify CA company endpoints self-heal pack install drift."""

    def test_list_companies_reconciles_ca_pack_install(self):
        """list_companies repairs missing ca-firm install state."""
        from api.v1.companies import list_companies

        source = inspect.getsource(list_companies)
        assert "_ensure_ca_pack_ready" in source

    def test_get_company_reconciles_ca_pack_install(self):
        """get_company repairs missing ca-firm install state."""
        from api.v1.companies import get_company

        source = inspect.getsource(get_company)
        assert "_ensure_ca_pack_ready" in source


# ============================================================================
# Tally Detect — schemas and endpoint verification
# ============================================================================


class TestTallyDetectSchemas:
    """Verify Tally Detect schemas."""

    def test_tally_detect_request_schema(self):
        """TallyDetectRequest accepts either the canonical `bridge_url`
        key or the legacy `tally_bridge_url` alias.

        BUG-005 (Ramesh 2026-04-20): the field was renamed to
        ``bridge_url`` to match the /test-tally request shape. The
        legacy key stays callable via a Pydantic alias so external
        SDK consumers that still send ``tally_bridge_url`` keep
        working. Attributes on the parsed model live under the
        canonical name.
        """
        from api.v1.companies import TallyDetectRequest

        # Canonical shape.
        body = TallyDetectRequest(bridge_url="http://localhost:9000")
        assert body.bridge_url == "http://localhost:9000"
        assert body.bridge_id == ""

        # Legacy alias — still accepted for back-compat.
        legacy = TallyDetectRequest(tally_bridge_url="http://localhost:9000")
        assert legacy.bridge_url == "http://localhost:9000"

    def test_tally_detect_response_has_expected_fields(self):
        """TallyDetectResponse has detected, company_name, gstin, pan."""
        from api.v1.companies import TallyDetectResponse

        fields = set(TallyDetectResponse.model_fields.keys())
        assert "detected" in fields
        assert "company_name" in fields
        assert "gstin" in fields
        assert "pan" in fields

    def test_tally_detect_returns_detected_true(self):
        """tally_detect stub returns detected=True with mock data."""
        from api.v1.companies import tally_detect

        source = inspect.getsource(tally_detect)
        assert "detected=True" in source
        assert "company_name" in source
        assert "gstin" in source
        assert "pan" in source

    def test_tally_detect_response_construction(self):
        """TallyDetectResponse accepts all fields."""
        from api.v1.companies import TallyDetectResponse

        resp = TallyDetectResponse(
            detected=True,
            company_name="Test Co",
            gstin="29AABCU9603R1ZM",
            pan="AABCU9603R",
        )
        assert resp.detected is True
        assert resp.company_name == "Test Co"


# ============================================================================
# Compliance Deadlines — schemas and endpoint verification
# ============================================================================


class TestComplianceDeadlineSchemas:
    """Verify Compliance Deadline schemas and endpoint logic."""

    def test_deadline_out_has_all_fields(self):
        """ComplianceDeadlineOut has all expected fields."""
        from api.v1.companies import ComplianceDeadlineOut

        fields = set(ComplianceDeadlineOut.model_fields.keys())
        expected = {
            "id", "company_id", "deadline_type", "filing_period",
            "due_date", "alert_7d_sent", "alert_1d_sent",
            "filed", "filed_at", "created_at",
        }
        assert expected.issubset(fields), f"Missing: {expected - fields}"

    def test_deadline_list_out_schema(self):
        """ComplianceDeadlineListOut has items and total."""
        from api.v1.companies import ComplianceDeadlineListOut

        out = ComplianceDeadlineListOut(items=[], total=0)
        assert out.total == 0

    def test_generate_creates_deadlines(self):
        """generate_compliance_deadlines creates deadline rows."""
        from api.v1.companies import generate_compliance_deadlines

        source = inspect.getsource(generate_compliance_deadlines)
        assert "session.add" in source
        assert "ComplianceDeadline" in source

    def test_list_deadlines_sorted_by_due_date(self):
        """list_compliance_deadlines orders by due_date."""
        from api.v1.companies import list_compliance_deadlines

        source = inspect.getsource(list_compliance_deadlines)
        assert "order_by" in source
        assert "due_date" in source

    def test_mark_filed_sets_fields(self):
        """mark_deadline_filed sets filed=True and filed_at."""
        from api.v1.companies import mark_deadline_filed

        source = inspect.getsource(mark_deadline_filed)
        assert "filed = True" in source
        assert "filed_at" in source

    def test_mark_filed_409_on_already_filed(self):
        """mark_deadline_filed raises 409 if already filed."""
        from api.v1.companies import mark_deadline_filed

        source = inspect.getsource(mark_deadline_filed)
        assert "409" in source
        assert "already" in source.lower()

    def test_generate_skips_existing_deadlines(self):
        """generate_compliance_deadlines checks for existing before insert."""
        from api.v1.companies import generate_compliance_deadlines

        source = inspect.getsource(generate_compliance_deadlines)
        assert "existing" in source
        assert "scalar_one_or_none" in source
