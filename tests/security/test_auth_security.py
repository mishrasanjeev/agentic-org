"""Security tests SEC-AUTH-001 to SEC-AUTH-008."""

from auth.scopes import check_scope


class TestScopeEnforcementSecurity:
    def test_sec_auth_002_cross_domain_denied(self):
        """AP agent cannot call HR tools."""
        scopes = ["tool:oracle_fusion:read:purchase_order"]
        allowed, _ = check_scope(scopes, "darwinbox", "read", "get_employee")
        assert not allowed

    def test_sec_auth_006_scope_elevation_ignored(self):
        """Cannot elevate scope via parameters."""
        scopes = ["tool:oracle_fusion:read:purchase_order"]
        allowed, _ = check_scope(scopes, "oracle_fusion", "write", "journal_entry")
        assert not allowed
