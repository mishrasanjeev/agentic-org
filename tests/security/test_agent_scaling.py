"""Scaling tests FT-SCALE-001 to FT-SCALE-015."""
import pytest
from auth.scopes import validate_clone_scopes
from scaling.lifecycle import LifecycleManager

class TestAgentLifecycle:
    def test_ft_scale_003_shadow_pass(self):
        lm = LifecycleManager()
        assert lm.can_transition("shadow", "review_ready")

    def test_ft_scale_004_shadow_fail(self):
        lm = LifecycleManager()
        assert lm.can_transition("shadow", "shadow_failing")

    def test_ft_scale_010_clone_scope_ceiling(self):
        parent = ["tool:banking_api:write:queue_payment:capped:500000"]
        child = ["tool:banking_api:write:queue_payment:capped:1000000"]
        violations = validate_clone_scopes(parent, child)
        assert len(violations) > 0

    def test_ft_scale_015_skip_shadow_blocked(self):
        lm = LifecycleManager()
        assert not lm.can_transition("draft", "active")
