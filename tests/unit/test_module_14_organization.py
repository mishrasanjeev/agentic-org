"""Foundation #6 — Module 14 Organization Management.

Source-pin tests for TC-ORG-001 through TC-ORG-006.

Org Management is a tenancy-isolation surface — every endpoint
must be tenant-scoped, every privileged action gated by
``require_tenant_admin``, and member deactivation must soft-delete
(not hard-delete) so audit/decryptable history is preserved.

Pinned contracts:

- All admin endpoints (profile, members, invite, onboarding,
  deactivate) require require_tenant_admin.
- Member queries filter by tenant_id AND status != 'deleted'
  so cross-tenant leakage and soft-deleted user resurfacing
  are both blocked.
- Invite role validation against a documented allowlist —
  arbitrary strings are 400, not silently coerced.
- Duplicate invites (same tenant + same email) are 409, not
  600 = silently overwriting the existing user.
- Self-deactivation is 400 (a tenant_admin must not be able
  to lock themselves out).
- Deactivation is SOFT — status='inactive', NOT a row delete.
- Password policy in accept-invite: 8+ chars + upper + lower
  + digit. Foundation #8 false-green prevention: a weaker
  policy would silently let users set 'password' as their
  password.
- Invite-token claims must carry both ``agenticorg:invite``
  and ``agenticorg:user_id`` — without these the token isn't
  an invite and accept-invite must reject.
- Invite token email must match the invited user's email.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-ORG-001 — View organization profile (admin-only)
# ─────────────────────────────────────────────────────────────────


def test_tc_org_001_profile_endpoint_requires_tenant_admin() -> None:
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert '@router.get("/profile", dependencies=[require_tenant_admin])' in src


def test_tc_org_001_profile_returns_documented_fields() -> None:
    """Pin the profile shape so the UI Settings page can't silently
    show stale or missing fields after a backend rename."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    for field in ('"id"', '"name"', '"slug"', '"plan"',
                  '"data_region"', '"settings"', '"created_at"'):
        assert field in src, f"profile missing key {field}"


# ─────────────────────────────────────────────────────────────────
# TC-ORG-002 — List organization members
# ─────────────────────────────────────────────────────────────────


def test_tc_org_002_members_endpoint_requires_tenant_admin() -> None:
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert '@router.get("/members", dependencies=[require_tenant_admin])' in src


def test_tc_org_002_members_query_is_tenant_scoped_and_filters_deleted() -> None:
    """Members list must filter by tenant_id AND exclude
    status='deleted'. Without the deleted filter, soft-removed
    users resurface on the Settings → Members page — a real UX
    bug that's also a privacy concern (the email and name of a
    user we said we'd remove is back on screen)."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert "User.tenant_id == uuid.UUID(tenant_id)" in src
    assert 'User.status != "deleted"' in src


# ─────────────────────────────────────────────────────────────────
# TC-ORG-003 — Invite new member
# ─────────────────────────────────────────────────────────────────


def test_tc_org_003_invite_endpoint_admin_gated_201_status() -> None:
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert (
        '@router.post("/invite", status_code=201, '
        'dependencies=[require_tenant_admin])'
    ) in src


def test_tc_org_003_invite_role_allowlist_pinned() -> None:
    """Roles that can be invited are a CLOSED set. New roles must
    be added to this allowlist deliberately — preventing a future
    refactor from letting an attacker invite an "owner" or
    "superadmin" via JSON body."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    for role in ('"admin"', '"domain_lead"', '"analyst"',
                 '"auditor"', '"developer"'):
        assert role in src, f"invite allowlist missing {role}"
    # The mismatch reply must surface the allowed list (so callers
    # know what's accepted); silent rejection would force a guessing
    # game.
    assert "Invalid role:" in src
    assert "Allowed:" in src


def test_tc_org_003_duplicate_invite_returns_409() -> None:
    """Inviting an email that already exists in the tenant is a
    conflict, NOT a silent re-create. The 409 forces the UI to
    show "user already exists" rather than e.g. silently
    overwriting their password reset."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert "status_code=409" in src
    assert "User already exists in organization" in src


# ─────────────────────────────────────────────────────────────────
# TC-ORG-004 — Accept invite
# ─────────────────────────────────────────────────────────────────


def test_tc_org_004_accept_invite_validates_password_policy() -> None:
    """8+ chars, upper, lower, digit. Foundation #8 false-green
    prevention: a weaker policy lets users set 'password' or
    '12345678' as their password."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert "len(password) < 8" in src
    assert 'r"[A-Z]"' in src
    assert 'r"[a-z]"' in src
    assert 'r"[0-9]"' in src


def test_tc_org_004_invite_token_claims_validated() -> None:
    """Invite token must carry agenticorg:invite=True AND
    agenticorg:user_id. Without these, accept-invite would
    accept any signed JWT as an invite — a privilege-escalation
    path."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert '"agenticorg:invite"' in src
    assert '"agenticorg:user_id"' in src
    assert "Token is not an invite token" in src


def test_tc_org_004_invite_email_must_match_invited_user() -> None:
    """The token's ``sub`` (invited email) must match the user
    record's email. Otherwise an attacker who steals one
    invite token could activate a different user account."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert "Invite token does not match invited user" in src


def test_tc_org_004_accept_invite_returns_session_token() -> None:
    """On success the response includes ``access_token`` so the
    UI can immediately log the user in. Without this the UX
    forces a redundant login round-trip."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert '"access_token": token' in src
    assert '"token_type": "bearer"' in src


# ─────────────────────────────────────────────────────────────────
# TC-ORG-005 — Deactivate member
# ─────────────────────────────────────────────────────────────────


def test_tc_org_005_deactivate_endpoint_admin_gated() -> None:
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert (
        '@router.delete("/members/{user_id}", '
        'dependencies=[require_tenant_admin])'
    ) in src


def test_tc_org_005_deactivate_is_soft_delete_not_hard_delete() -> None:
    """Deactivation must set ``status = 'inactive'`` — never
    delete the row. The closure plan's mistake #6 forbids hard
    deletes that strip audit / decryptable history; this is the
    canonical example of where the rule applies."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert 'user.status = "inactive"' in src
    # Also assert the handler doesn't call session.delete(user).
    assert "session.delete(user)" not in src
    assert "session.delete(" not in src.split("deactivate_member")[1] if "deactivate_member" in src else True


def test_tc_org_005_self_deactivation_blocked() -> None:
    """A tenant_admin cannot deactivate themselves — protects
    against the lockout that happens when the only admin
    accidentally clicks the wrong row."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert "Cannot deactivate yourself" in src
    assert "user.email == current_user_email" in src


def test_tc_org_005_deactivate_query_is_tenant_scoped() -> None:
    """The deactivate handler queries User by id AND tenant_id —
    so tenant A's admin can't deactivate tenant B's users by
    guessing a UUID."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert "User.tenant_id == uuid.UUID(tenant_id)" in src


# ─────────────────────────────────────────────────────────────────
# TC-ORG-006 — Update onboarding progress
# ─────────────────────────────────────────────────────────────────


def test_tc_org_006_onboarding_endpoint_admin_gated() -> None:
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert (
        '@router.put("/onboarding", dependencies=[require_tenant_admin])'
    ) in src


def test_tc_org_006_onboarding_updates_only_provided_fields() -> None:
    """The update logic must respect partial updates — None values
    in the body must NOT clobber existing settings. Pin the
    is-not-None checks so a future refactor can't silently
    overwrite the entire settings JSONB."""
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert "if body.onboarding_step is not None:" in src
    assert "if body.onboarding_complete is not None:" in src
    # Settings dict starts from the existing tenant.settings, NOT
    # an empty dict — preserves any other settings keys.
    assert "dict(tenant.settings)" in src
