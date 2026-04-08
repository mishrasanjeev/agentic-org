# ruff: noqa: N801
"""Unit tests for Phase 2 CA features: GSTN Credential Vault, Compliance
Deadlines, cron logic, bulk approval, Tally detect, and partner dashboard.

Tests cover:
- Database schema validation (tables, columns, constraints, FKs)
- Model defaults and field types
- Encryption round-trip (core.crypto)
- Cron deadline generation logic
- CompanyRole bulk approval roles
- Tally config JSONB field
- Partner dashboard KPI fields (health_score, subscription_status)
- Model registration in core.models.__init__
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
COMPANY_A = uuid.UUID("00000000-0000-0000-0000-000000000010")


# ============================================================================
# a) GSTN Credential Vault -- schema validation
# ============================================================================


class TestGSTNCredentialSchema:
    """Verify gstn_credentials table has all expected columns and constraints."""

    def test_gstn_credentials_table_exists(self):
        from core.models.gstn_credential import GSTNCredential

        assert GSTNCredential.__tablename__ == "gstn_credentials"

    def test_gstn_credentials_has_all_columns(self):
        from core.models.gstn_credential import GSTNCredential

        expected_columns = [
            "id", "tenant_id", "company_id", "gstin", "username",
            "password_encrypted", "encryption_key_ref", "portal_type",
            "is_active", "last_verified_at", "last_login_at",
            "created_at", "updated_at",
        ]
        mapper_cols = {c.key for c in GSTNCredential.__table__.columns}
        for col in expected_columns:
            assert col in mapper_cols, f"gstn_credentials missing column: {col}"

    def test_unique_constraint_company_portal_type(self):
        """Only one credential per company per portal type."""
        from core.models.gstn_credential import GSTNCredential

        constraints = GSTNCredential.__table__.constraints
        uq_names = {c.name for c in constraints if hasattr(c, "columns") and c.name}
        assert "uq_gstn_cred_company" in uq_names, (
            f"Missing UniqueConstraint 'uq_gstn_cred_company'. Found: {uq_names}"
        )

    def test_unique_constraint_covers_company_and_portal(self):
        """uq_gstn_cred_company must span (company_id, portal_type)."""
        from core.models.gstn_credential import GSTNCredential

        for constraint in GSTNCredential.__table__.constraints:
            if getattr(constraint, "name", "") == "uq_gstn_cred_company":
                col_names = {c.name for c in constraint.columns}
                assert "company_id" in col_names
                assert "portal_type" in col_names
                return
        pytest.fail("uq_gstn_cred_company constraint not found")

    def test_company_id_fk_references_companies(self):
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.company_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "companies.id" in fk_targets, (
            f"company_id FK target should be companies.id, got: {fk_targets}"
        )

    def test_tenant_id_fk_references_tenants(self):
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.tenant_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "tenants.id" in fk_targets, (
            f"tenant_id FK target should be tenants.id, got: {fk_targets}"
        )


class TestGSTNCredentialDefaults:
    """Verify GSTNCredential model field defaults."""

    def test_portal_type_defaults_to_gstn(self):
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.portal_type
        assert col.default is not None
        assert col.default.arg == "gstn"

    def test_is_active_defaults_to_true(self):
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.is_active
        assert col.default is not None
        assert col.default.arg is True

    def test_encryption_key_ref_defaults_to_default(self):
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.encryption_key_ref
        assert col.default is not None
        assert col.default.arg == "default"


class TestGSTNCredentialEncryption:
    """Verify encrypt/decrypt/verify round-trip from core.crypto."""

    def test_encrypt_credential_returns_non_empty(self):
        from core.crypto import encrypt_credential

        result = encrypt_credential("my-secret-password")
        assert result
        assert result != "my-secret-password"

    def test_decrypt_round_trip(self):
        from core.crypto import decrypt_credential, encrypt_credential

        plaintext = "portal-password-2026!"
        ciphertext = encrypt_credential(plaintext)
        assert decrypt_credential(ciphertext) == plaintext

    def test_verify_credential_valid(self):
        from core.crypto import encrypt_credential, verify_credential

        ciphertext = encrypt_credential("test-verify")
        assert verify_credential(ciphertext) is True

    def test_verify_credential_garbage(self):
        from core.crypto import verify_credential

        assert verify_credential("this-is-not-valid-ciphertext") is False


# ============================================================================
# b) Compliance Deadline model -- schema validation
# ============================================================================


class TestComplianceDeadlineSchema:
    """Verify compliance_deadlines table has all expected columns and constraints."""

    def test_compliance_deadlines_table_exists(self):
        from core.models.compliance_deadline import ComplianceDeadline

        assert ComplianceDeadline.__tablename__ == "compliance_deadlines"

    def test_compliance_deadlines_has_all_columns(self):
        from core.models.compliance_deadline import ComplianceDeadline

        expected_columns = [
            "id", "tenant_id", "company_id", "deadline_type",
            "filing_period", "due_date", "alert_7d_sent", "alert_1d_sent",
            "filed", "filed_at", "created_at", "updated_at",
        ]
        mapper_cols = {c.key for c in ComplianceDeadline.__table__.columns}
        for col in expected_columns:
            assert col in mapper_cols, f"compliance_deadlines missing column: {col}"

    def test_unique_constraint_deadline_company_type_period(self):
        """One deadline per (company, type, period)."""
        from core.models.compliance_deadline import ComplianceDeadline

        constraints = ComplianceDeadline.__table__.constraints
        uq_names = {c.name for c in constraints if hasattr(c, "columns") and c.name}
        assert "uq_deadline_company_type_period" in uq_names, (
            f"Missing UniqueConstraint 'uq_deadline_company_type_period'. Found: {uq_names}"
        )

    def test_unique_constraint_covers_correct_columns(self):
        from core.models.compliance_deadline import ComplianceDeadline

        for constraint in ComplianceDeadline.__table__.constraints:
            if getattr(constraint, "name", "") == "uq_deadline_company_type_period":
                col_names = {c.name for c in constraint.columns}
                assert "company_id" in col_names
                assert "deadline_type" in col_names
                assert "filing_period" in col_names
                return
        pytest.fail("uq_deadline_company_type_period constraint not found")

    def test_company_id_fk_references_companies(self):
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.company_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "companies.id" in fk_targets

    def test_tenant_id_fk_references_tenants(self):
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.tenant_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "tenants.id" in fk_targets


class TestComplianceDeadlineDefaults:
    """Verify ComplianceDeadline model field defaults."""

    def test_alert_7d_sent_defaults_false(self):
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.alert_7d_sent
        assert col.default is not None
        assert col.default.arg is False

    def test_alert_1d_sent_defaults_false(self):
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.alert_1d_sent
        assert col.default is not None
        assert col.default.arg is False

    def test_filed_defaults_false(self):
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.filed
        assert col.default is not None
        assert col.default.arg is False


# ============================================================================
# c) Cron job logic -- monthly and quarterly deadline generation
# ============================================================================


class TestCronMonthlyDeadlines:
    """Test _compute_monthly_deadlines generates correct deadlines."""

    def test_generates_3_months_of_deadlines(self):
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        results = _compute_monthly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
            months_ahead=3,
        )
        # 3 months x 4 deadline types = 12
        assert len(results) == 12

    def test_monthly_deadline_types(self):
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        results = _compute_monthly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        types_found = {r["deadline_type"] for r in results}
        assert types_found == {"gstr1", "gstr3b", "pf_ecr", "esi_return"}

    def test_gstr1_due_on_11th(self):
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        results = _compute_monthly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        gstr1_records = [r for r in results if r["deadline_type"] == "gstr1"]
        for rec in gstr1_records:
            assert rec["due_date"].day == 11

    def test_gstr3b_due_on_20th(self):
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        results = _compute_monthly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        gstr3b_records = [r for r in results if r["deadline_type"] == "gstr3b"]
        for rec in gstr3b_records:
            assert rec["due_date"].day == 20

    def test_pf_ecr_due_on_15th(self):
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        results = _compute_monthly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        pf_records = [r for r in results if r["deadline_type"] == "pf_ecr"]
        for rec in pf_records:
            assert rec["due_date"].day == 15

    def test_esi_return_due_on_15th(self):
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        results = _compute_monthly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        esi_records = [r for r in results if r["deadline_type"] == "esi_return"]
        for rec in esi_records:
            assert rec["due_date"].day == 15


class TestCronQuarterlyDeadlines:
    """Test _compute_quarterly_deadlines generates correct TDS deadlines."""

    def test_generates_8_quarterly_records(self):
        """4 quarters x 2 types (tds_26q, tds_24q) = 8."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        results = _compute_quarterly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        assert len(results) == 8

    def test_quarterly_deadline_types(self):
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        results = _compute_quarterly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        types_found = {r["deadline_type"] for r in results}
        assert types_found == {"tds_26q", "tds_24q"}

    def test_quarterly_filing_periods(self):
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        results = _compute_quarterly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        periods = {r["filing_period"] for r in results}
        # Indian FY starting Apr 2026 = FY 2026-27
        assert "2026-Q1" in periods
        assert "2026-Q2" in periods
        assert "2026-Q3" in periods
        assert "2026-Q4" in periods

    def test_q1_due_date_july_31(self):
        """Q1 (Apr-Jun) -> TDS due 31 July."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        results = _compute_quarterly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        q1_records = [r for r in results if r["filing_period"] == "2026-Q1"]
        assert len(q1_records) == 2  # tds_26q + tds_24q
        for rec in q1_records:
            assert rec["due_date"] == date(2026, 7, 31)

    def test_q2_due_date_october_31(self):
        """Q2 (Jul-Sep) -> TDS due 31 October."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        results = _compute_quarterly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        q2_records = [r for r in results if r["filing_period"] == "2026-Q2"]
        assert len(q2_records) == 2
        for rec in q2_records:
            assert rec["due_date"] == date(2026, 10, 31)

    def test_q3_due_date_january_31(self):
        """Q3 (Oct-Dec) -> TDS due 31 January."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        results = _compute_quarterly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        q3_records = [r for r in results if r["filing_period"] == "2026-Q3"]
        assert len(q3_records) == 2
        for rec in q3_records:
            assert rec["due_date"] == date(2027, 1, 31)

    def test_q4_due_date(self):
        """Q4 (Jan-Mar) -> TDS due after quarter end (Apr or May 31)."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        results = _compute_quarterly_deadlines(
            company_id=str(COMPANY_A),
            tenant_id=str(TENANT_A),
            today=date(2026, 4, 8),
        )
        q4_records = [r for r in results if r["filing_period"] == "2026-Q4"]
        assert len(q4_records) == 2
        # Q4 end month is March (3), next month is April -> due Apr 30
        for rec in q4_records:
            assert rec["due_date"].month == 4
            assert rec["due_date"].day == 30


# ============================================================================
# d) Company model new field -- gstn_auto_upload
# ============================================================================


class TestCompanyGSTNAutoUpload:
    """Verify Company.gstn_auto_upload column exists and defaults to False."""

    def test_gstn_auto_upload_column_exists(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "gstn_auto_upload" in cols

    def test_gstn_auto_upload_defaults_false(self):
        from core.models.company import Company

        col = Company.__table__.c.gstn_auto_upload
        assert col.default is not None
        assert col.default.arg is False


# ============================================================================
# e) Model registration -- GSTNCredential and ComplianceDeadline
# ============================================================================


class TestPhase2ModelRegistration:
    """All Phase 2 models must be importable from core.models."""

    def test_gstn_credential_importable(self):
        from core.models import GSTNCredential

        assert GSTNCredential.__tablename__ == "gstn_credentials"

    def test_compliance_deadline_importable(self):
        from core.models import ComplianceDeadline

        assert ComplianceDeadline.__tablename__ == "compliance_deadlines"

    def test_company_importable(self):
        from core.models import Company

        assert Company.__tablename__ == "companies"


# ============================================================================
# f) Bulk approval -- CompanyRole.partner and CompanyRole.manager
# ============================================================================


class TestBulkApprovalRoles:
    """Verify partner and manager roles exist for bulk approval flow."""

    def test_partner_role_exists(self):
        from api.v1.companies import CompanyRole

        assert CompanyRole.partner.value == "partner"

    def test_manager_role_exists(self):
        from api.v1.companies import CompanyRole

        assert CompanyRole.manager.value == "manager"

    def test_partner_and_manager_in_roles(self):
        from api.v1.companies import CompanyRole

        roles = {r.value for r in CompanyRole}
        assert "partner" in roles
        assert "manager" in roles


# ============================================================================
# g) Tally detect -- tally_config JSONB field
# ============================================================================


class TestTallyDetect:
    """Verify Company model has tally_config JSONB field for Tally auto-detect."""

    def test_tally_config_column_exists(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "tally_config" in cols

    def test_tally_config_is_nullable(self):
        from core.models.company import Company

        col = Company.__table__.c.tally_config
        assert col.nullable is True


# ============================================================================
# h) Partner dashboard -- health_score and subscription_status fields
# ============================================================================


class TestPartnerDashboardFields:
    """Company model has fields needed for partner dashboard KPI aggregation."""

    def test_client_health_score_column_exists(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "client_health_score" in cols

    def test_client_health_score_default_100(self):
        from core.models.company import Company

        col = Company.__table__.c.client_health_score
        assert col.default is not None
        assert col.default.arg == 100

    def test_subscription_status_column_exists(self):
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "subscription_status" in cols

    def test_subscription_status_default_trial(self):
        from core.models.company import Company

        col = Company.__table__.c.subscription_status
        assert col.default is not None
        assert col.default.arg == "trial"

    def test_health_score_nullable(self):
        """Health score should be nullable for newly onboarded companies."""
        from core.models.company import Company

        col = Company.__table__.c.client_health_score
        assert col.nullable is True

    def test_subscription_status_not_nullable(self):
        """Subscription status must always have a value."""
        from core.models.company import Company

        col = Company.__table__.c.subscription_status
        assert col.nullable is False


# ============================================================================
# Relationship validation -- Phase 2 models
# ============================================================================


class TestPhase2Relationships:
    """Verify Phase 2 models have correct relationships defined."""

    def test_gstn_credential_has_company_relationship(self):
        from core.models.gstn_credential import GSTNCredential

        mapper = GSTNCredential.__mapper__
        rel_names = {r.key for r in mapper.relationships}
        assert "company" in rel_names, (
            f"GSTNCredential missing 'company' relationship. Found: {rel_names}"
        )

    def test_gstn_credential_has_tenant_relationship(self):
        from core.models.gstn_credential import GSTNCredential

        mapper = GSTNCredential.__mapper__
        rel_names = {r.key for r in mapper.relationships}
        assert "tenant" in rel_names

    def test_compliance_deadline_has_company_relationship(self):
        from core.models.compliance_deadline import ComplianceDeadline

        mapper = ComplianceDeadline.__mapper__
        rel_names = {r.key for r in mapper.relationships}
        assert "company" in rel_names, (
            f"ComplianceDeadline missing 'company' relationship. Found: {rel_names}"
        )

    def test_compliance_deadline_has_tenant_relationship(self):
        from core.models.compliance_deadline import ComplianceDeadline

        mapper = ComplianceDeadline.__mapper__
        rel_names = {r.key for r in mapper.relationships}
        assert "tenant" in rel_names
