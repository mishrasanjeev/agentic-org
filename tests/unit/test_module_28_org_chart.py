"""Foundation #6 — Module 28 Org Chart Hierarchy & Parent Escalation.

Source-pin tests for TC-ORG-CHART-002 through TC-ORG-CHART-008
(TC-ORG-CHART-001 was already auto-mapped to test_qa_matrix.py).

The escalation chain is the platform's last-line-of-defense
routing surface — when a HITL trigger fires, the system must
walk parent_agent_id, skip inactive parents, fall back to a
domain head, and finally hand off to a human. Every link in
that chain must hold.

Pinned contracts:

- Agent.parent_agent_id is a nullable self-FK (so an agent can
  be removed from the chart without breaking the schema).
- TaskRouter.escalate walks up to max_depth=5 hops (defaults).
- _INACTIVE_STATUSES = {"paused", "retired"} — parents in
  these statuses are SKIPPED, not used as targets.
- Cycle detection via a visited set so a malformed parent
  pointer can't infinite-loop.
- Three escalation_type returns: "parent_agent" |
  "domain_head" | "human" — the orchestrator branches on
  these strings.
- Domain-head fallback queries by tenant_id + domain +
  parent_agent_id IS NULL.
- The chain list is the audit trail for why an escalation
  landed where it did.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-ORG-CHART-002 — View hierarchy in agent detail
# ─────────────────────────────────────────────────────────────────


def test_tc_org_chart_002_agent_model_has_parent_self_fk() -> None:
    """parent_agent_id is a nullable self-FK on agents.id with the
    matching SQLAlchemy ``relationship`` set up so a future
    refactor can't silently disconnect the chart."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    assert "parent_agent_id" in src
    # Self-FK: ForeignKey("agents.id"), nullable=True.
    assert 'ForeignKey("agents.id"), nullable=True' in src
    # Self-relationship with remote_side so the ORM can navigate
    # parent → child in either direction.
    assert (
        'relationship("Agent", remote_side="Agent.id", foreign_keys=[parent_agent_id])'
        in src
    )


def test_tc_org_chart_002_agent_create_schema_accepts_parent_id() -> None:
    """The AgentCreate schema must accept parent_agent_id +
    reporting_to so the create flow can populate the chart at
    insert time."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    assert "parent_agent_id: str | None = None" in src
    assert "reporting_to: str | None = None" in src


# ─────────────────────────────────────────────────────────────────
# TC-ORG-CHART-003 — Escalation to parent agent on HITL
# ─────────────────────────────────────────────────────────────────


def test_tc_org_chart_003_escalate_returns_parent_agent_type() -> None:
    """When the immediate parent is active, the escalation
    contract returns escalation_type='parent_agent' with the
    parent UUID. The orchestrator branches on this string —
    renaming silently breaks routing."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert '"escalation_type": "parent_agent"' in src
    assert '"escalated_to": parent.id' in src
    # Must explicitly check for "active" — passing back any other
    # status would route requests to a stopped agent.
    assert 'parent.status == "active"' in src


# ─────────────────────────────────────────────────────────────────
# TC-ORG-CHART-004 — Escalation chain 3 levels deep
# ─────────────────────────────────────────────────────────────────


def test_tc_org_chart_004_default_max_depth_is_five() -> None:
    """Five hops is the contract — deep enough for a real org
    chart, shallow enough to bound the per-escalation work.
    Lifting this without justification risks a runaway walk."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert "max_depth: int = 5" in src


def test_tc_org_chart_004_walk_records_each_hop_in_chain() -> None:
    """The ``chain`` list grows on every hop so the audit trail
    shows exactly where the escalation walked. If a hop doesn't
    record, post-mortem analysis can't tell why a particular
    target was chosen."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert "chain.append(str(parent.id))" in src
    assert "chain.append(str(current.id))" in src


def test_tc_org_chart_004_cycle_detection_via_visited_set() -> None:
    """A malformed parent pointer (A → B → A) must NOT cause an
    infinite walk. The visited set is the defense; pin its
    presence so a refactor can't drop it."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert "visited: set[UUID] = set()" in src
    assert "if parent_id in visited:" in src
    assert "Escalation cycle detected" in src


# ─────────────────────────────────────────────────────────────────
# TC-ORG-CHART-005 — Escalation when parent is paused
# ─────────────────────────────────────────────────────────────────


def test_tc_org_chart_005_inactive_statuses_set_pinned() -> None:
    """Parents in paused/retired status are SKIPPED — the walk
    keeps climbing. New inactive statuses (e.g. "suspended") MUST
    be added to this frozenset deliberately, not silently."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert (
        '_INACTIVE_STATUSES = frozenset({"paused", "retired"})' in src
    )
    assert "if parent.status in _INACTIVE_STATUSES:" in src


def test_tc_org_chart_005_inactive_parent_continues_walk_not_returns() -> None:
    """The skip pattern is ``cursor = parent; continue`` — it
    sets cursor up to the parent and CONTINUES the loop, NOT
    returns. If the continue becomes a return, we'd silently
    route to a paused agent."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    # Tight pin: the inactive branch ends with "cursor = parent\n
    # continue".
    inactive_block = src.split("if parent.status in _INACTIVE_STATUSES:", 1)[1][:400]
    assert "cursor = parent" in inactive_block
    assert "continue" in inactive_block


# ─────────────────────────────────────────────────────────────────
# TC-ORG-CHART-006 — Agent without parent → escalation to human
# ─────────────────────────────────────────────────────────────────


def test_tc_org_chart_006_human_fallback_returns_human_type() -> None:
    """When neither a parent chain NOR a domain head can answer,
    the contract returns escalation_type='human' with
    escalated_to=None. This is what the caller routes to a
    human-operator queue."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert '"escalation_type": "human"' in src
    assert '"escalated_to": None' in src


def test_tc_org_chart_006_domain_head_fallback_pinned() -> None:
    """Between parent-chain and human, the platform first tries
    the domain head (root active agent in the same domain).
    Without this, every escalation in a tenant with shallow
    charts ends in human handoff."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert '"escalation_type": "domain_head"' in src
    assert "TaskRouter.resolve_domain_head" in src


def test_tc_org_chart_006_resolve_domain_head_query_predicates_pinned() -> None:
    """The domain-head query MUST filter by parent_agent_id IS NULL
    (so we get the root) AND by domain (so we don't escalate
    Finance → HR). A missing predicate cross-pollinates roles."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert "Agent.parent_agent_id.is_(None)" in src


# ─────────────────────────────────────────────────────────────────
# TC-ORG-CHART-007 — Update parent_agent_id via PATCH
# ─────────────────────────────────────────────────────────────────


def test_tc_org_chart_007_agent_update_schema_accepts_parent_id() -> None:
    """The AgentUpdate schema must accept parent_agent_id so the
    PATCH flow can move agents around the chart without a full
    replacement."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    # AgentUpdate has parent_agent_id as Optional + None default
    # (the second occurrence in the file after AgentCreate).
    assert src.count("parent_agent_id: str | None = None") >= 2


# ─────────────────────────────────────────────────────────────────
# TC-ORG-CHART-008 — Remove parent (set to null)
# ─────────────────────────────────────────────────────────────────


def test_tc_org_chart_008_parent_id_is_nullable_so_remove_works() -> None:
    """Setting parent_agent_id to NULL must be valid at the
    schema level — that's how a user removes an agent from the
    chart. If the column became NOT NULL, the only way to
    'remove' would be to delete the agent."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    # nullable=True on the parent_agent_id column.
    assert "parent_agent_id" in src
    parent_block = src.split("parent_agent_id", 1)[1][:200]
    assert "nullable=True" in parent_block


def test_tc_org_chart_008_starting_agent_not_found_returns_human() -> None:
    """Edge case: if the starting agent_id doesn't exist (e.g.
    deleted mid-flight), escalate must return human fallback,
    not raise. Pin the early-return branch so a future refactor
    can't turn it into a 500."""
    src = (REPO / "core" / "orchestrator" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert "Starting agent" in src and "not found" in src
    assert "current is None" in src
