"""Foundation #6 — Module 8 Approval Queue / HITL (6 TCs).

Source-pin tests for TC-HITL-001 through TC-HITL-006.

The approval queue is the human-in-the-loop control surface
for every workflow that can't auto-decide. Six of six TCs are
P0 (RBAC, decision integrity, audit trail).

Pinned contracts:

- /approvals lists tenant-scoped HITL items, paginated, default
  per_page=20 capped at 100, ordered desc by created_at.
- Default filter: only ``pending`` items unless ?status= is
  given. Expired pending items are excluded (they shouldn't
  show in the queue once they've timed out).
- /approvals?priority= filter applies to BOTH query AND count.
- RBAC domain filter via Agent subquery — auditor / admin see
  all, domain roles only see their domain's HITL items.
- Decide endpoint: requires authenticated user (sub claim),
  rejects already-resolved items with 409 + the prior status,
  rejects expired with 410, rejects without assignee_role with
  422, and enforces _can_decide(role, domain) — admin must
  match the assignee level (P3.2: admin is NOT a blanket
  bypass).
- Delegation override: if the direct check fails but an active
  delegation exists from a user whose role would allow the
  decision, the current user acts on behalf of the delegator;
  decision_by records the actual decider AND the delegated_from.
- Role hierarchy is closed: staff=10, manager=20, auditor=25,
  cfo/chro/cmo/coo/cbo=30, ceo=50, admin=100. Adding a new
  role MUST be deliberate (otherwise unknown roles map to
  level 0 and silently lose all approval authority).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-HITL-001 — View pending approvals
# ─────────────────────────────────────────────────────────────────


def test_tc_hitl_001_list_endpoint_returns_paginated_response() -> None:
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    assert '@router.get("/approvals", response_model=PaginatedResponse)' in src


def test_tc_hitl_001_list_per_page_capped_at_100() -> None:
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/approvals"', 1)[1].split(
        "@router.", 1
    )[0]
    assert "per_page = min(max(per_page, 1), 100)" in list_block


def test_tc_hitl_001_default_filters_to_pending_only() -> None:
    """When no ?status= is given, only pending items show. The
    UI's default tab shows pending; if this default flipped,
    the queue would surface decided items by default."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/approvals"', 1)[1].split(
        "@router.", 1
    )[0]
    assert "if not status:" in list_block
    assert 'HITLQueue.status == "pending"' in list_block


def test_tc_hitl_001_pending_excludes_expired_items() -> None:
    """Expired pending items shouldn't show — they've timed out
    and need to be re-queued or escalated. Pin the
    expires_at-IS-NULL-OR-greater-than-now branch so a refactor
    can't silently widen the visible queue."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/approvals"', 1)[1].split(
        "@router.", 1
    )[0]
    assert "HITLQueue.expires_at.is_(None)" in list_block
    assert "HITLQueue.expires_at > now" in list_block


def test_tc_hitl_001_dict_carries_required_fields_for_ui() -> None:
    """Every field the queue UI renders. Field drops would
    silently empty UI cells."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    for field in ('"id":', '"workflow_run_id":', '"agent_id":',
                  '"title":', '"trigger_type":', '"priority":',
                  '"status":', '"assignee_role":',
                  '"decision_options":', '"context":', '"decision":',
                  '"decision_by":', '"decision_at":',
                  '"decision_notes":', '"expires_at":'):
        assert field in src, f"_hitl_to_dict missing {field}"


# ─────────────────────────────────────────────────────────────────
# TC-HITL-002 / TC-HITL-003 — Approve / Reject
# ─────────────────────────────────────────────────────────────────


def test_tc_hitl_002_decide_endpoint_pinned() -> None:
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    assert '@router.post("/approvals/{hitl_id}/decide")' in src


def test_tc_hitl_002_decide_requires_sub_claim() -> None:
    """P2.1 — every decision must be attributable to a real
    user. Missing 'sub' (or agenticorg:user_id) is 401, NEVER
    silently anonymous."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    assert "Cannot identify user — missing 'sub' claim" in src


def test_tc_hitl_002_decide_rejects_already_resolved_with_409() -> None:
    """A second decide on the same item is 409 with the prior
    status surfaced — Foundation #8 false-green prevention.
    Silently overwriting would drop the audit trail."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    assert (
        '409, f"HITL item already resolved with status \'{item.status}\'"' in src
    )


def test_tc_hitl_003_decide_rejects_expired_with_410() -> None:
    """410 (Gone) is the right status — the resource is no
    longer available. Pin so a refactor can't silently turn it
    into 200 (silently approve a stale item)."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    assert '410, "HITL item has expired"' in src


def test_tc_hitl_003_decide_rejects_missing_assignee_role_with_422() -> None:
    """P2.1: never approve without an assignee role. If a HITL
    item somehow landed with no role, 422 forces fix-up over
    silently letting any admin push the button."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    assert (
        "HITL item has no assignee_role — cannot validate authorization" in src
    )


# ─────────────────────────────────────────────────────────────────
# TC-HITL-004 — Filter by priority
# ─────────────────────────────────────────────────────────────────


def test_tc_hitl_004_priority_filter_applies_to_both_query_and_count() -> None:
    """If priority filter only applied to query, total would
    show the unfiltered count and pagination math drifts.
    Cross-pin TC-AGT-002 / TC-WF-001."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/approvals"', 1)[1].split(
        "@router.", 1
    )[0]
    assert "if priority:" in list_block
    priority_apply = list_block.count("HITLQueue.priority == priority")
    assert priority_apply >= 2, (
        "priority filter must apply to BOTH query and count_base"
    )


def test_tc_hitl_004_status_filter_applies_to_both_query_and_count() -> None:
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/approvals"', 1)[1].split(
        "@router.", 1
    )[0]
    assert "if status:" in list_block
    status_apply = list_block.count("HITLQueue.status == status")
    assert status_apply >= 2


# ─────────────────────────────────────────────────────────────────
# TC-HITL-005 — Decided tab shows history
# ─────────────────────────────────────────────────────────────────


def test_tc_hitl_005_decided_status_can_be_queried() -> None:
    """The "Decided" tab passes ?status=approved or =rejected.
    Pin that the status filter is supported (the same path the
    UI uses to get history)."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/approvals"', 1)[1].split(
        "@router.", 1
    )[0]
    # The handler accepts ``status: str | None`` — pinning the
    # parameter signature so it can't be removed silently.
    assert "status: str | None = None" in list_block


def test_tc_hitl_005_decision_attribution_fields_exist() -> None:
    """The history view shows decision_by + decision_at +
    decision_notes — pin those columns are in the dict shape
    so the UI can render the audit trail."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    for field in ('"decision":', '"decision_by":',
                  '"decision_at":', '"decision_notes":'):
        assert field in src, f"_hitl_to_dict missing {field}"


# ─────────────────────────────────────────────────────────────────
# TC-HITL-006 — Role-based approval visibility (CFO RBAC)
# ─────────────────────────────────────────────────────────────────


def test_tc_hitl_006_role_hierarchy_pinned_with_documented_levels() -> None:
    """The role hierarchy is the basis for ALL approval
    authorization. Pinning the levels by role name + integer
    so a refactor can't silently flip the precedence (e.g.
    auditor jumping above CFO)."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    for entry in (
        '"staff": 10,',
        '"manager": 20,',
        '"auditor": 25,',
        '"cfo": 30,',
        '"chro": 30,',
        '"cmo": 30,',
        '"coo": 30,',
        '"cbo": 30,',
        '"ceo": 50,',
        '"admin": 100,',
    ):
        assert entry in src, f"_ROLE_HIERARCHY missing entry: {entry}"


def test_tc_hitl_006_unknown_role_returns_level_zero() -> None:
    """Unknown roles map to level 0 — silently lose ALL
    approval authority. Foundation #8 false-green prevention:
    if an unknown role had ANY default level, a typo could
    grant approval rights to a non-existent role."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    assert "_ROLE_HIERARCHY.get((role or \"\").lower(), 0)" in src
    # And the can-decide check rejects level-0 roles with the
    # documented "unknown role" message.
    assert "unknown role" in src


def test_tc_hitl_006_admin_must_match_assignee_level() -> None:
    """P3.2: admin is NOT a blanket bypass. An admin must still
    have user_lvl >= required_lvl — admin level is 100 so
    they pass for almost everything, but the check is in
    place so a future refactor that lowers admin level still
    enforces the rule."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    assert (
        "P3.2: admin must still match the assignee role to DECIDE" in src
    )
    assert (
        'if user_role.lower() == "admin":' in src
    )


def test_tc_hitl_006_domain_check_runs_after_role_check() -> None:
    """Even an authorized role can't decide on items in domains
    they're not assigned to. Pin the order: role gate first,
    domain gate second — a CFO can't approve HR HITL items
    even though the level matches."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    can_decide = src.split("def _can_decide(", 1)[1].split(
        "\n\ndef ", 1
    )[0]
    role_idx = can_decide.find("user_lvl < required_lvl")
    domain_idx = can_decide.find("agent_domain not in user_domains")
    assert role_idx > 0 and domain_idx > 0
    assert role_idx < domain_idx, (
        "role gate must run BEFORE domain gate so the failure "
        "message identifies the right reason"
    )


def test_tc_hitl_006_delegation_override_records_actor_attribution() -> None:
    """When the direct check fails but a delegation grants
    permission, the record shows the actual decider AND the
    delegated_from — Foundation #8 false-green prevention:
    we never lose attribution, even on delegated decisions."""
    src = (REPO / "api" / "v1" / "approvals.py").read_text(encoding="utf-8")
    assert "Delegation override" in src
    assert "delegated_from" in src
    assert "acting on behalf of" in src
    assert "UserDelegation.revoked_at.is_(None)" in src
    # Delegation expiry windows must be respected.
    assert "UserDelegation.starts_at <= now" in src
    assert "delegation.ends_at" in src
