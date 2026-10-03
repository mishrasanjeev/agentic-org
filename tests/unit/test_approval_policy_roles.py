"""Policy roles must map to an RBAC role that can make approval decisions."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.v1.approval_policies import StepIn, _validate_steps
from core.rbac import APPROVAL_POLICY_ROLES, ROLE_SCOPES, is_approval_policy_role


def test_policy_roles_follow_approval_write_authority() -> None:
    expected = {
        role
        for role, scopes in ROLE_SCOPES.items()
        if "approvals:write" in scopes or "agenticorg:admin" in scopes
    }
    assert APPROVAL_POLICY_ROLES == expected
    assert {"admin", "cfo", "domain_lead", "developer"} <= APPROVAL_POLICY_ROLES


@pytest.mark.parametrize("role", ["unknown", "auditor", "analyst", "merchant", "CFO", " cfo "])
def test_policy_write_rejects_unmapped_or_read_only_role(role: str) -> None:
    assert not is_approval_policy_role(role)
    with pytest.raises(HTTPException) as exc:
        _validate_steps([StepIn(sequence=1, approver_role=role)])
    assert exc.value.status_code == 400
    assert "invalid approver_role" in exc.value.detail


@pytest.mark.parametrize("role", ["admin", "cfo", "domain_lead", "developer"])
def test_policy_write_accepts_approval_capable_role(role: str) -> None:
    _validate_steps([StepIn(sequence=1, approver_role=role)])
