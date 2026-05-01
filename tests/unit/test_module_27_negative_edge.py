"""Foundation #6 — Module 27 Negative & Edge Cases.

Source-pin tests for TC-NEG-001 through TC-NEG-010 (TC-NEG-002
is flagged 'duplicate' of TC-NEG-001 in the matrix and is
already covered by 001's pin).

The negative-path module is where Foundation #8's false-green
prevention earns its keep — every "what if the input is
malformed/expired/oversized?" question must produce a clear,
documented failure shape, never a silent 200/500.

Pinned contracts:

- Expired/tampered/revoked JWTs raise ValueError with the
  documented "Token has been revoked" or "Local token validation
  failed: …" prefix. The Grantex middleware surfaces these as
  401/403 to the client.
- AgentCreate.name has Field(..., max_length=255). Pydantic
  rejects 256+ char names with 422 — NOT silently truncate.
- AgentUpdate handles non-existent ids with 404 (not 5xx).
- DELETE /agents/{id} refuses non-deletable statuses with 409 +
  the documented message ("Pause or retire the agent first.").
- Audit log entry MUST be written BEFORE the row delete so the
  audit trail isn't lost when the deletion succeeds.
- Concurrent agent creation is gated by tenant-scoped uniqueness
  + per-(name, version) constraints at the model layer.
- UI logout removes BOTH token AND user from localStorage so
  the back button can't restore an authenticated state.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-NEG-001 (covers 002 too — flagged duplicate)
# Access API with expired / tampered token
# ─────────────────────────────────────────────────────────────────


def test_tc_neg_001_validate_local_token_rejects_revoked() -> None:
    """Revoked tokens raise ValueError with the documented
    "Token has been revoked" message. The auth middleware
    catches ValueError and returns 401."""
    src = (REPO / "auth" / "jwt.py").read_text(encoding="utf-8")
    assert 'raise ValueError("Token has been revoked")' in src


def test_tc_neg_001_validate_local_token_surfaces_jwt_error() -> None:
    """Tampered/expired/wrong-issuer tokens raise JWTError, which
    we catch and re-raise as ValueError with a "Local token
    validation failed:" prefix. UI parses this prefix."""
    src = (REPO / "auth" / "jwt.py").read_text(encoding="utf-8")
    assert "except JWTError as e:" in src
    assert 'Local token validation failed:' in src


def test_tc_neg_001_jwt_validation_pins_audience_and_issuer() -> None:
    """audience= and issuer= MUST be set on jwt.decode. Without
    them, an attacker who steals an HS256 secret can forge a
    token that any service in any environment accepts."""
    src = (REPO / "auth" / "jwt.py").read_text(encoding="utf-8")
    assert 'audience="agenticorg-tool-gateway"' in src
    assert "issuer=expected_issuer" in src
    assert '"verify_iss": True' in src


# ─────────────────────────────────────────────────────────────────
# TC-NEG-003 — Create agent with confidence floor out of range
# ─────────────────────────────────────────────────────────────────


def test_tc_neg_003_confidence_floor_default_documented() -> None:
    """confidence_floor defaults to 0.88 (the BUG-012 alignment
    with the model's default). Pydantic typing is plain float,
    so values <0 or >1 ARE accepted at the schema layer — the
    handler enforces the [0, 1] range. Pin the default so a
    refactor can't silently shift it."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    assert "confidence_floor: float = 0.88" in src


def test_tc_neg_003_shadow_accuracy_floor_default_pinned() -> None:
    """shadow_accuracy_floor default is 0.80 (BUG-012 fix —
    0.95 was unreachable for LLM agents). If the default
    moves to 0.95 again, every new agent gets stuck in shadow
    mode forever."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    assert "shadow_accuracy_floor: float = 0.80" in src


# ─────────────────────────────────────────────────────────────────
# TC-NEG-004 — Update non-existent agent
# ─────────────────────────────────────────────────────────────────


def test_tc_neg_004_update_non_existent_agent_returns_404() -> None:
    """PUT/PATCH on a missing agent must 404 (not 500). Pin the
    explicit 404 so a refactor can't silently turn it into a
    different status."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    # The handler raises HTTPException(404, "Agent not found").
    assert 'HTTPException(404, "Agent not found")' in src


# ─────────────────────────────────────────────────────────────────
# TC-NEG-005 — Run agent with empty input
# ─────────────────────────────────────────────────────────────────


def test_tc_neg_005_agent_create_requires_name_and_type() -> None:
    """The required fields on AgentCreate guard against empty
    input — the schema layer rejects payloads missing any of
    name / agent_type / domain with 422 BEFORE the handler runs."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    # Field(..., ...) with the ellipsis = required.
    assert "name: str = Field(..., max_length=255)" in src
    assert "agent_type: str = Field(..., max_length=100)" in src
    assert "domain: str = Field(..., max_length=50)" in src


# ─────────────────────────────────────────────────────────────────
# TC-NEG-006 — Extremely long agent name
# ─────────────────────────────────────────────────────────────────


def test_tc_neg_006_agent_name_max_length_pinned() -> None:
    """name is capped at 255 chars. Pydantic rejects 256+ with
    422. Without this cap, a malicious caller could submit a
    1MB name and (a) bloat the DB (b) blow up the UI list
    rendering."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    assert "name: str = Field(..., max_length=255)" in src


# ─────────────────────────────────────────────────────────────────
# TC-NEG-007 — Special characters in inputs
# ─────────────────────────────────────────────────────────────────


def test_tc_neg_007_agent_name_storage_is_unicode_text_no_escaping() -> None:
    """The model uses String(255) — Postgres stores unicode
    natively. The UI escapes on render (Module 22 contract).
    Pin that the column is plain string (no JSON, no HTML
    encoding) so round-trips don't double-encode."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    # The column declaration uses String(...) somewhere for name.
    assert "name: Mapped[str]" in src
    assert "String" in src


# ─────────────────────────────────────────────────────────────────
# TC-NEG-008 — Concurrent agent creation race
# ─────────────────────────────────────────────────────────────────


def test_tc_neg_008_agent_versions_unique_per_agent_version_pair() -> None:
    """AgentVersion has UniqueConstraint(agent_id, version) so
    two parallel writers can't insert duplicate version rows
    for the same agent. The DB layer wins the race; one of
    the two callers gets an IntegrityError."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    assert 'UniqueConstraint("agent_id", "version")' in src


def test_tc_neg_008_per_agent_advisory_lock_for_serialised_writes() -> None:
    """For agent run-time writes (budget, cost ledger),
    pg_advisory_xact_lock keyed on agent_id ensures concurrent
    requests serialise — see Module 22 cross-cutting test for
    the full pattern. Pin its presence here too as a negative-
    path defence."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in src


# ─────────────────────────────────────────────────────────────────
# TC-NEG-009 — Delete active agent with running workflows
# ─────────────────────────────────────────────────────────────────


def test_tc_neg_009_delete_refuses_non_deletable_status_with_409() -> None:
    """Active / draft / new agents can't be deleted — operator
    must pause or retire first. The 409 + documented message
    is what the UI shows in the confirmation dialog."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert 'deletable_statuses = {"paused", "retired", "inactive", "shadow"}' in src
    assert "Pause or retire the agent first" in src
    # 409 (Conflict) is the right status — not 400.
    assert "raise HTTPException(\n                409," in src or 'HTTPException(409' in src


def test_tc_neg_009_delete_writes_audit_before_row_drop() -> None:
    """The audit log MUST be written BEFORE the agent row is
    deleted. If the order flips, a deletion that succeeds at
    the row level but fails at the audit write would leave no
    trail — Module 12 audit contract requires every mutation
    be visible in the log."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    delete_block = src.split("# ── DELETE /agents/", 1)[1][:3500]
    audit_idx = delete_block.find("AuditLog(")
    delete_idx = delete_block.find("session.delete(")
    assert audit_idx > 0, "AuditLog write missing from delete handler"
    assert delete_idx > 0, "session.delete missing from delete handler"
    assert audit_idx < delete_idx, (
        "AuditLog must be created BEFORE the row delete — "
        "otherwise a successful delete with a failed audit "
        "leaves no trail"
    )


# ─────────────────────────────────────────────────────────────────
# TC-NEG-010 — Browser back button after logout
# ─────────────────────────────────────────────────────────────────


def test_tc_neg_010_logout_clears_both_token_and_user_localstorage() -> None:
    """Logout must clear BOTH the in-memory React state AND any
    legacy localStorage entries left over from pre-PR-F clients.

    SEC-002 (PR-F, 2026-05-01): cookie-first auth means the
    HttpOnly ``agenticorg_session`` cookie is the source of truth.
    Logout calls ``POST /auth/logout`` (backend clears the cookie),
    then resets in-memory state, then runs
    ``_purgeLegacyTokenStorage`` (which removes any pre-PR-F
    ``localStorage.token`` / ``user`` left behind on this client).
    Without the legacy purge, a back-button after upgrade could
    flash a stale "authenticated" UI from the pre-PR-F user blob.
    """
    src = (REPO / "ui" / "src" / "contexts" / "AuthContext.tsx").read_text(
        encoding="utf-8"
    )
    # Find the logout function body (no fixed length — the new
    # implementation is larger because of the await-fetch logout call).
    after_logout = src.split("logout = useCallback", 1)[1]
    # The first }, []) marks the end of the useCallback body.
    logout_block = after_logout.split("}, []);", 1)[0]
    # Server-side logout call clears the HttpOnly cookie.
    assert "/auth/logout" in logout_block
    # In-memory state cleared.
    assert "setUser(null)" in logout_block
    assert "setIsAuthenticated(false)" in logout_block
    # Legacy storage purge runs (so back-button after upgrade
    # doesn't show stale state from a pre-PR-F build).
    assert "_purgeLegacyTokenStorage()" in logout_block
    # The legacy purge helper itself MUST clear both keys.
    purge_block = src.split("function _purgeLegacyTokenStorage", 1)[1].split("\n}", 1)[0]
    assert 'localStorage.removeItem("token")' in purge_block
    assert 'localStorage.removeItem("user")' in purge_block
