"""Tool gateway tests — scope, rate limit, idempotency, PII masking."""
import pytest
from auth.scopes import check_scope, parse_scope, validate_clone_scopes
from core.tool_gateway.pii_masker import mask_string

class TestScopeEnforcement:
    def test_read_scope_allowed(self):
        scopes = ["tool:oracle_fusion:read:purchase_order"]
        allowed, _ = check_scope(scopes, "oracle_fusion", "read", "purchase_order")
        assert allowed

    def test_write_scope_denied(self):
        scopes = ["tool:oracle_fusion:read:purchase_order"]
        allowed, _ = check_scope(scopes, "oracle_fusion", "write", "journal_entry")
        assert not allowed

    def test_capped_scope_within_limit(self):
        scopes = ["tool:banking_api:write:queue_payment:capped:500000"]
        allowed, _ = check_scope(scopes, "banking_api", "write", "queue_payment", amount=300000)
        assert allowed

    def test_capped_scope_exceeds(self):
        scopes = ["tool:banking_api:write:queue_payment:capped:500000"]
        allowed, reason = check_scope(scopes, "banking_api", "write", "queue_payment", amount=600000)
        assert not allowed
        assert "cap_exceeded" in reason

    def test_admin_scope(self):
        scopes = ["tool:okta:admin"]
        allowed, _ = check_scope(scopes, "okta", "write", "provision_user")
        assert allowed

class TestCloneScopeCeiling:
    def test_valid_clone(self):
        parent = ["tool:oracle_fusion:read:purchase_order"]
        child = ["tool:oracle_fusion:read:purchase_order"]
        assert validate_clone_scopes(parent, child) == []

    def test_scope_elevation_blocked(self):
        parent = ["tool:banking_api:write:queue_payment:capped:500000"]
        child = ["tool:banking_api:write:queue_payment:capped:1000000"]
        violations = validate_clone_scopes(parent, child)
        assert len(violations) > 0

class TestPIIMasking:
    def test_mask_email(self):
        result = mask_string("user@example.com")
        assert "example.com" not in result

    def test_mask_pan(self):
        result = mask_string("ABCDE1234F")
        assert "ABCDE" not in result

    def test_mask_aadhaar(self):
        result = mask_string("1234 5678 9012")
        assert "XXXX" in result
