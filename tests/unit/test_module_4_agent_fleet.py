"""Foundation #6 — Module 4 Agent Fleet Management.

Source-pin tests for TC-AGT-001 through TC-AGT-013 (TC-AGT-012/013
are documented duplicates of 007/008 — covered transitively).

Agent CRUD + status lifecycle is the platform's primary surface
— ten of these TCs guard the contracts that every dashboard,
LangGraph runner, and SOP-driven workflow leans on.

Pinned contracts:

- GET /agents pages with default per_page=20, capped at 100,
  ordered desc by created_at, with optional domain/status/
  company_id filters AND RBAC domain restriction.
- _PROMOTE_MAP defines the legal status transitions — only
  shadow→active is supported from the lifecycle endpoint.
  Any new transition must be added to the map deliberately.
- Promote validates BOTH minimum sample count AND accuracy
  floor before flipping shadow→active. Foundation #8 false-
  green prevention: promote MUST refuse with 409 if either
  is missing.
- Rollback finds the previous AgentVersion (NOT the current
  one) and restores prompt + tools + LLM config + confidence
  floor + version. Status is intentionally preserved (rolling
  back code shouldn't unpause a paused agent).
- _PAUSE/_PROMOTE/rollback all emit AgentLifecycleEvent so
  the audit trail and the UI activity feed stay in sync.
- /agents/org-tree filters out deleted/error/broken agents
  (BUG-28) so the org chart doesn't show orphan placeholders.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-AGT-001 — Agent list displays all agents (pagination shape)
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_001_list_endpoint_returns_paginated_response() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '@router.get("/agents", response_model=PaginatedResponse)' in src


def test_tc_agt_001_list_orders_desc_by_created_at() -> None:
    """Newest agents at the top — UI dashboard contract.
    Reversing this would silently rotate page contents under
    every existing list view."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/agents", response_model=', 1)[1].split(
        "@router.", 1
    )[0]
    assert "order_by(Agent.created_at.desc())" in list_block


def test_tc_agt_001_list_per_page_capped_at_100_min_1() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/agents", response_model=', 1)[1].split(
        "@router.", 1
    )[0]
    assert "per_page = min(max(per_page, 1), 100)" in list_block


# ─────────────────────────────────────────────────────────────────
# TC-AGT-002 / TC-AGT-003 — Filter by domain / status
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_002_domain_filter_pinned() -> None:
    """Domain filter param is accepted AND applied to BOTH the
    SELECT and the COUNT(*) — without it, ``total`` would be
    the unfiltered count and the UI's "X of Y" would lie."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/agents", response_model=', 1)[1].split(
        "@router.", 1
    )[0]
    assert "if domain:" in list_block
    assert "Agent.domain == domain" in list_block
    # Both query AND count_query must apply the filter.
    domain_apply_count = list_block.count("Agent.domain == domain")
    assert domain_apply_count >= 2, (
        "domain filter must apply to both query and count_query — "
        "otherwise total is wrong"
    )


def test_tc_agt_003_status_filter_pinned() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/agents", response_model=', 1)[1].split(
        "@router.", 1
    )[0]
    assert "if status:" in list_block
    assert "Agent.status == status" in list_block
    status_apply_count = list_block.count("Agent.status == status")
    assert status_apply_count >= 2


def test_tc_agt_002_rbac_domain_filter_runs_before_explicit_filter() -> None:
    """The user_domains RBAC filter (from JWT) restricts to allowed
    domains. The explicit ?domain= further narrows. If RBAC ran
    AFTER the explicit filter, a CFO could pass ?domain=HR and
    see HR rows. Pin that user_domains is applied first."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/agents", response_model=', 1)[1].split(
        "@router.", 1
    )[0]
    rbac_idx = list_block.find("Agent.domain.in_(user_domains)")
    explicit_idx = list_block.find("if domain:")
    assert rbac_idx > 0 and explicit_idx > 0
    assert rbac_idx < explicit_idx, (
        "RBAC domain filter must run BEFORE the explicit ?domain= "
        "filter so a domain-restricted user can't see other domains"
    )


# ─────────────────────────────────────────────────────────────────
# TC-AGT-004 — Search by name (UI-side filter; smoke for endpoint)
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_004_list_endpoint_returns_dict_with_name() -> None:
    """The UI filters by name client-side from the list response.
    Pin that the agent dict contains the name field — otherwise
    the search input matches nothing."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '"name": agent.name' in src


# ─────────────────────────────────────────────────────────────────
# TC-AGT-005 — Kill switch (pause active agent)
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_005_pause_endpoint_admin_gated() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    pause_block = src.split('"/agents/{agent_id}/pause"', 1)[1][:300]
    assert "require_tenant_admin" in pause_block


def test_tc_agt_005_pause_emits_lifecycle_event() -> None:
    """Every status transition emits an AgentLifecycleEvent —
    audit trail + UI activity feed both depend on this. Pin
    the lifecycle event creation in the pause path."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    pause_block = src.split('"/agents/{agent_id}/pause"', 1)[1].split(
        "@router.", 1
    )[0]
    assert "AgentLifecycleEvent" in pause_block


# ─────────────────────────────────────────────────────────────────
# TC-AGT-006 — Agent detail overview tab (GET /agents/{id})
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_006_get_endpoint_returns_full_agent_dict() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '@router.get("/agents/{agent_id}")' in src
    # The dict-builder must include the fields the Overview tab
    # renders: name, status, domain, created_at.
    for field in ('"id":', '"name":', '"status":', '"domain":',
                  '"created_at":'):
        assert field in src


# ─────────────────────────────────────────────────────────────────
# TC-AGT-007 — Config tab (PATCH /agents/{id})
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_007_patch_endpoint_admin_gated() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    patch_block = src.split("@router.patch(\n", 1)[1][:400]
    assert "require_tenant_admin" in patch_block or "require_scope" in patch_block


def test_tc_agt_007_patch_uses_agentupdate_partial_schema() -> None:
    """PATCH must take AgentUpdate (Optional fields) — NOT
    AgentCreate. Otherwise a partial PATCH would 422 because
    AgentCreate's required fields aren't supplied."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    patch_block = src.split("@router.patch(\n", 1)[1][:1200]
    assert "AgentUpdate" in patch_block


# ─────────────────────────────────────────────────────────────────
# TC-AGT-008 — Prompt tab (PUT /agents/{id})
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_008_put_endpoint_admin_gated() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    put_block = src.split("@router.put(\n", 1)[1][:400]
    assert "require_tenant_admin" in put_block or "require_scope" in put_block


def test_tc_agt_008_put_enforces_prompt_lock_on_active_agents() -> None:
    """Active agents have a prompt lock — PUT must reject with
    400 + the documented message the UI parses for the Clone CTA.
    Cross-pin with TC-CC-009 (Module 22)."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert (
        "Prompt is locked on active agents. Clone this agent to make changes."
        in src
    )
    # And the lock check is gated on prompt_changing AND status=='active'.
    assert 'if prompt_changing and agent.status == "active":' in src


# ─────────────────────────────────────────────────────────────────
# TC-AGT-009 — Promote shadow to active
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_009_promote_endpoint_admin_gated() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    promote_block = src.split('"/agents/{agent_id}/promote"', 1)[1][:300]
    assert "require_tenant_admin" in promote_block


def test_tc_agt_009_promote_map_only_shadow_to_active() -> None:
    """The legal-transition map is closed: shadow → active only.
    Adding any other transition (e.g. paused → active) must go
    through code review, not slip in via a typo."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '_PROMOTE_MAP: dict[str, str] = {' in src
    promote_map_block = src.split("_PROMOTE_MAP: dict[str, str] = {", 1)[1].split(
        "}", 1
    )[0]
    assert '"shadow": "active"' in promote_map_block
    # No other entries in the map — count the colons.
    assert promote_map_block.count(":") == 1, (
        "_PROMOTE_MAP must contain ONLY shadow→active. Adding new "
        "transitions requires explicit code review."
    )


def test_tc_agt_009_promote_validates_min_samples() -> None:
    """Foundation #8 false-green prevention: promote MUST refuse
    with 409 when shadow_sample_count < shadow_min_samples.
    Without this, an agent with 0 samples could go straight
    to active."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert (
        "if agent.shadow_sample_count < agent.shadow_min_samples:" in src
    )
    assert "cannot promote until minimum is met" in src


def test_tc_agt_009_promote_validates_accuracy_floor() -> None:
    """Sample count alone isn't enough — accuracy floor must
    also be met. Both gates required to flip shadow→active."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert (
        "if agent.shadow_accuracy_current < agent.shadow_accuracy_floor:" in src
    )
    assert "is below floor" in src


# ─────────────────────────────────────────────────────────────────
# TC-AGT-010 — Rollback to previous version
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_010_rollback_endpoint_admin_gated() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    rollback_block = src.split('"/agents/{agent_id}/rollback"', 1)[1][:300]
    assert "require_tenant_admin" in rollback_block


def test_tc_agt_010_rollback_picks_previous_not_current_version() -> None:
    """The previous-version query MUST exclude the current
    version. Otherwise a "rollback" would no-op (rolling back
    to itself)."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "AgentVersion.version != agent.version" in src
    # And it picks the most recent of the remaining versions.
    assert "order_by(AgentVersion.created_at.desc())" in src


def test_tc_agt_010_rollback_409_when_no_previous_version() -> None:
    """If there's no prior version (first-ever release), 409 with
    the documented message — NOT a 500 / silent no-op."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "No previous version to rollback to" in src
    assert "HTTPException(409" in src.split("rollback_agent", 1)[1][:1500]


def test_tc_agt_010_rollback_emits_lifecycle_event() -> None:
    """The rollback emits a lifecycle event so the activity feed
    shows "rolled back from version X to Y". Status is preserved
    (to_status = old_status) — rolling back code shouldn't
    unpause a paused agent."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    rollback_block = src.split("async def rollback_agent", 1)[1][:2500]
    assert "AgentLifecycleEvent(" in rollback_block
    assert "to_status=old_status" in rollback_block
    assert "Rolled back from version" in rollback_block


# ─────────────────────────────────────────────────────────────────
# TC-AGT-011 — Shadow tab
# ─────────────────────────────────────────────────────────────────


def test_tc_agt_011_shadow_min_samples_default_pinned() -> None:
    """shadow_min_samples default = 10 (Uday 2026-04-23 Bug 1
    fix — was 20, stretched validation into a 20-click flow).
    If this drifts back to 20, the QA validation flow regresses."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    assert "shadow_min_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=10)" in src


def test_tc_agt_011_shadow_accuracy_floor_default_pinned() -> None:
    """shadow_accuracy_floor default = 0.800 (BUG-012 fix —
    0.95 was unreachable for LLM agents)."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    assert 'default=Decimal("0.800")' in src


def test_tc_agt_011_shadow_promotion_floor_check_uses_current_accuracy() -> None:
    """The promote gate reads shadow_accuracy_current — the
    rolling average. Pin the field name so a refactor can't
    silently flip to a different (e.g. last-sample) metric."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "agent.shadow_accuracy_current" in src


# ─────────────────────────────────────────────────────────────────
# Org tree — BUG-28 deleted/error/broken filter
# ─────────────────────────────────────────────────────────────────


def test_org_tree_filters_out_deleted_error_broken_agents() -> None:
    """BUG-28: org-tree was showing deleted/broken agent placeholders.
    The status filter must exclude all three. Pin the exact set."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert 'Agent.status.notin_(["deleted", "error", "broken"])' in src
