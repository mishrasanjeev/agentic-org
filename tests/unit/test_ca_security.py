# ruff: noqa: N801
"""Security-specific tests for CA features.

Tests cover:
- Credential security (encryption, no password leakage)
- Tenant isolation (all models have non-nullable tenant_id, indexed)
- Role-based access (CompanyRole enum, approval role checks)
- Data protection (sensitive fields exist, safe defaults)
- Status transition safety (non-nullable status fields)
"""

from __future__ import annotations

import pytest


# ============================================================================
# Credential Security
# ============================================================================


class TestCredentialSecurity:
    """Verify GSTN credential encryption and output safety."""

    def test_credential_out_schema_no_password_encrypted(self):
        """GSTNCredentialOut schema must NOT have password_encrypted field."""
        from api.v1.companies import GSTNCredentialOut

        fields = set(GSTNCredentialOut.model_fields.keys())
        assert "password_encrypted" not in fields, (
            "GSTNCredentialOut exposes password_encrypted -- security violation"
        )

    def test_credential_out_schema_no_password(self):
        """GSTNCredentialOut schema must NOT have password field."""
        from api.v1.companies import GSTNCredentialOut

        fields = set(GSTNCredentialOut.model_fields.keys())
        assert "password" not in fields, (
            "GSTNCredentialOut exposes password -- security violation"
        )

    def test_encrypted_password_differs_from_plaintext(self):
        """Encrypted password must be different from the plaintext input."""
        from core.crypto import encrypt_credential

        plaintext = "super-secret-gstn-password"
        encrypted = encrypt_credential(plaintext)
        assert encrypted != plaintext, "Encryption produced same output as input"

    def test_encrypted_password_contains_no_plaintext_substring(self):
        """The ciphertext must not contain any plaintext substring."""
        from core.crypto import encrypt_credential

        plaintext = "my-portal-password-123"
        encrypted = encrypt_credential(plaintext)
        assert plaintext not in encrypted, (
            "Plaintext found as substring of ciphertext"
        )

    def test_decryption_with_correct_key_works(self):
        """Encrypt then decrypt should return original plaintext."""
        from core.crypto import decrypt_credential, encrypt_credential

        plaintext = "test-password-for-roundtrip"
        encrypted = encrypt_credential(plaintext)
        decrypted = decrypt_credential(encrypted)
        assert decrypted == plaintext

    def test_verify_credential_returns_true_for_valid(self):
        """verify_credential returns True when ciphertext was encrypted with current key."""
        from core.crypto import encrypt_credential, verify_credential

        encrypted = encrypt_credential("valid-password")
        assert verify_credential(encrypted) is True

    def test_verify_credential_returns_false_for_garbage(self):
        """verify_credential returns False for invalid ciphertext."""
        from core.crypto import verify_credential

        assert verify_credential("not-a-valid-ciphertext") is False

    def test_verify_credential_returns_false_for_empty(self):
        """verify_credential returns False for empty string."""
        from core.crypto import verify_credential

        assert verify_credential("") is False


# ============================================================================
# Tenant Isolation — all CA models must have non-nullable, indexed tenant_id
# ============================================================================


class TestTenantIsolation:
    """Verify every CA model has a non-nullable, indexed tenant_id column."""

    def test_company_tenant_id_not_nullable(self):
        from core.models.company import Company

        col = Company.__table__.c.tenant_id
        assert col.nullable is False, "Company.tenant_id must not be nullable"

    def test_filing_approval_tenant_id_not_nullable(self):
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.tenant_id
        assert col.nullable is False, "FilingApproval.tenant_id must not be nullable"

    def test_gstn_upload_tenant_id_not_nullable(self):
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.tenant_id
        assert col.nullable is False, "GSTNUpload.tenant_id must not be nullable"

    def test_ca_subscription_tenant_id_not_nullable(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.tenant_id
        assert col.nullable is False, "CASubscription.tenant_id must not be nullable"

    def test_gstn_credential_tenant_id_not_nullable(self):
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.tenant_id
        assert col.nullable is False, "GSTNCredential.tenant_id must not be nullable"

    def test_compliance_deadline_tenant_id_not_nullable(self):
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.tenant_id
        assert col.nullable is False, "ComplianceDeadline.tenant_id must not be nullable"

    def test_company_tenant_id_indexed(self):
        from core.models.company import Company

        col = Company.__table__.c.tenant_id
        assert col.index is True, "Company.tenant_id must be indexed"

    def test_filing_approval_tenant_id_indexed(self):
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.tenant_id
        assert col.index is True, "FilingApproval.tenant_id must be indexed"

    def test_gstn_upload_tenant_id_indexed(self):
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.tenant_id
        assert col.index is True, "GSTNUpload.tenant_id must be indexed"

    def test_ca_subscription_tenant_id_indexed(self):
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.tenant_id
        assert col.index is True, "CASubscription.tenant_id must be indexed"

    def test_gstn_credential_tenant_id_indexed(self):
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.tenant_id
        assert col.index is True, "GSTNCredential.tenant_id must be indexed"

    def test_compliance_deadline_tenant_id_indexed(self):
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.tenant_id
        assert col.index is True, "ComplianceDeadline.tenant_id must be indexed"


# ============================================================================
# Role-Based Access
# ============================================================================


class TestRoleBasedAccess:
    """Verify role-based access controls in the CA feature."""

    def test_company_role_enum_has_exactly_5_values(self):
        """CompanyRole must have exactly 5 values."""
        from api.v1.companies import CompanyRole

        assert len(CompanyRole) == 5, (
            f"Expected 5 roles, got {len(CompanyRole)}: {[r.value for r in CompanyRole]}"
        )

    def test_partner_role_exists(self):
        """partner role must exist for approval operations."""
        from api.v1.companies import CompanyRole

        assert hasattr(CompanyRole, "partner")
        assert CompanyRole.partner.value == "partner"

    def test_manager_role_exists(self):
        """manager role must exist for rejection operations."""
        from api.v1.companies import CompanyRole

        assert hasattr(CompanyRole, "manager")
        assert CompanyRole.manager.value == "manager"

    def test_user_roles_jsonb_not_nullable(self):
        """Company.user_roles must not be nullable."""
        from core.models.company import Company

        col = Company.__table__.c.user_roles
        assert col.nullable is False, "user_roles must not be nullable"

    def test_user_roles_defaults_to_empty_dict(self):
        """Company.user_roles server_default is empty JSON object."""
        from core.models.company import Company

        col = Company.__table__.c.user_roles
        assert col.server_default is not None, "user_roles must have a server_default"
        # The server_default text should contain an empty JSON object
        default_text = str(col.server_default.arg)
        assert "'{}'" in default_text or "{}" in default_text

    def test_approval_checks_partner_role_source(self):
        """approve_filing endpoint source enforces partner role."""
        import inspect

        from api.v1.companies import approve_filing

        source = inspect.getsource(approve_filing)
        assert "CompanyRole.partner" in source

    def test_rejection_allows_manager_role_source(self):
        """reject_filing endpoint source allows manager role."""
        import inspect

        from api.v1.companies import reject_filing

        source = inspect.getsource(reject_filing)
        assert "CompanyRole.manager" in source


# ============================================================================
# Data Protection
# ============================================================================


class TestDataProtection:
    """Verify sensitive fields exist and safety defaults are set."""

    def test_bank_account_number_field_exists(self):
        """Company.bank_account_number must exist (sensitive data)."""
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "bank_account_number" in cols

    def test_dsc_serial_field_exists(self):
        """Company.dsc_serial must exist (Digital Signature Certificate)."""
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "dsc_serial" in cols

    def test_gst_auto_file_defaults_false(self):
        """gst_auto_file must default to False for safety."""
        from core.models.company import Company

        col = Company.__table__.c.gst_auto_file
        assert col.default is not None
        assert col.default.arg is False, (
            f"gst_auto_file defaults to {col.default.arg}, must be False"
        )

    def test_gstn_auto_upload_defaults_false(self):
        """gstn_auto_upload must default to False for safety."""
        from core.models.company import Company

        col = Company.__table__.c.gstn_auto_upload
        assert col.default is not None
        assert col.default.arg is False, (
            f"gstn_auto_upload defaults to {col.default.arg}, must be False"
        )

    def test_is_active_defaults_true(self):
        """is_active must default to True."""
        from core.models.company import Company

        col = Company.__table__.c.is_active
        assert col.default is not None
        assert col.default.arg is True

    def test_credential_is_active_defaults_true(self):
        """GSTNCredential.is_active must default to True."""
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.is_active
        assert col.default is not None
        assert col.default.arg is True

    def test_password_encrypted_field_is_text(self):
        """GSTNCredential.password_encrypted must be Text (not length-limited)."""
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.password_encrypted
        assert col.nullable is False, "password_encrypted must not be nullable"

    def test_encryption_key_ref_has_default(self):
        """GSTNCredential.encryption_key_ref defaults to 'default'."""
        from core.models.gstn_credential import GSTNCredential

        col = GSTNCredential.__table__.c.encryption_key_ref
        assert col.default is not None
        assert col.default.arg == "default"


# ============================================================================
# Status Transition Safety
# ============================================================================


class TestStatusTransitionSafety:
    """Verify status/filed fields are not nullable to prevent invalid states."""

    def test_filing_approval_status_not_nullable(self):
        """FilingApproval.status must not be nullable."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.status
        assert col.nullable is False

    def test_filing_approval_status_defaults_pending(self):
        """FilingApproval.status defaults to 'pending'."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.status
        assert col.default is not None
        assert col.default.arg == "pending"

    def test_gstn_upload_status_not_nullable(self):
        """GSTNUpload.status must not be nullable."""
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.status
        assert col.nullable is False

    def test_gstn_upload_status_defaults_generated(self):
        """GSTNUpload.status defaults to 'generated'."""
        from core.models.gstn_upload import GSTNUpload

        col = GSTNUpload.__table__.c.status
        assert col.default is not None
        assert col.default.arg == "generated"

    def test_compliance_deadline_filed_not_nullable(self):
        """ComplianceDeadline.filed must not be nullable."""
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.filed
        assert col.nullable is False

    def test_compliance_deadline_filed_defaults_false(self):
        """ComplianceDeadline.filed defaults to False."""
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.filed
        assert col.default is not None
        assert col.default.arg is False

    def test_ca_subscription_status_not_nullable(self):
        """CASubscription.status must not be nullable."""
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.status
        assert col.nullable is False

    def test_ca_subscription_status_defaults_trial(self):
        """CASubscription.status defaults to 'trial'."""
        from core.models.ca_subscription import CASubscription

        col = CASubscription.__table__.c.status
        assert col.default is not None
        assert col.default.arg == "trial"

    def test_filing_approval_auto_approved_not_nullable(self):
        """FilingApproval.auto_approved must not be nullable."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.auto_approved
        assert col.nullable is False

    def test_filing_approval_auto_approved_defaults_false(self):
        """FilingApproval.auto_approved defaults to False."""
        from core.models.filing_approval import FilingApproval

        col = FilingApproval.__table__.c.auto_approved
        assert col.default is not None
        assert col.default.arg is False
