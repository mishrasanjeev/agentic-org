"""Foundation #6 — Module 7 Workflows (5 TCs).

Source-pin tests for TC-WF-001 through TC-WF-005.

Workflows compose multiple agents into a single audited
business process. The contracts here protect the create →
run → step-by-step execution → HITL pause flow that every
SOP-driven module relies on.

Pinned contracts:

- /workflows list endpoint mirrors /agents shape (paginated,
  RBAC domain-filtered, per_page capped at 100).
- /workflows POST validates the definition has a non-empty
  ``steps`` array — both 0-step rejection AND not-an-array
  rejection. Foundation #8: silent acceptance of empty defs
  would let a UI bug create unrunnable workflows.
- 0-step workflows are also rejected at RUN time (defense-in-
  depth — BUG-18/19) so an old empty def can't be invoked.
- Inactive workflows (is_active=False) refuse to run with
  409 — pinning the gate so a future "always allow" refactor
  can't bypass the pause.
- A/B variant routing: when variants exist, the engine
  deterministically picks one by hashing (workflow_id,
  subject_id) so the same caller always sees the same
  variant (campaign coherence).
- HITL step handling: when a step returns waiting_hitl, the
  step row's completed_at stays NULL AND a HITLQueue entry
  is created with the step's title.
- Background execution: /workflows/{id}/run returns
  immediately with run_id+status=running; the actual
  step-by-step engine runs in BackgroundTasks.
- Run details endpoint loads steps via selectinload so a
  single round-trip carries the full execution trace.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-WF-001 — Workflow list page
# ─────────────────────────────────────────────────────────────────


def test_tc_wf_001_list_endpoint_returns_paginated_response() -> None:
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    assert '@router.get("/workflows", response_model=PaginatedResponse)' in src


def test_tc_wf_001_list_per_page_capped_at_100_and_min_1() -> None:
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    list_block = src.split(
        '@router.get("/workflows", response_model=', 1
    )[1].split("@router.", 1)[0]
    assert "per_page = min(max(per_page, 1), 100)" in list_block


def test_tc_wf_001_list_orders_desc_by_created_at() -> None:
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    list_block = src.split(
        '@router.get("/workflows", response_model=', 1
    )[1].split("@router.", 1)[0]
    assert "WorkflowDefinition.created_at.desc()" in list_block


def test_tc_wf_001_list_applies_rbac_domain_filter_to_query_and_count() -> None:
    """Cross-pin with TC-AGT-002: the RBAC user_domains filter
    must apply to BOTH the SELECT and the COUNT(*). Otherwise
    ``total`` lies and pagination math drifts."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    list_block = src.split(
        '@router.get("/workflows", response_model=', 1
    )[1].split("@router.", 1)[0]
    assert "if user_domains is not None:" in list_block
    rbac_apply = list_block.count("WorkflowDefinition.domain.in_(user_domains)")
    assert rbac_apply >= 2, (
        "RBAC domain filter must apply to both query and count_q "
        "— otherwise total is wrong"
    )


# ─────────────────────────────────────────────────────────────────
# TC-WF-002 — Create workflow
# ─────────────────────────────────────────────────────────────────


def test_tc_wf_002_create_endpoint_admin_gated() -> None:
    """Creating a workflow is a control-plane action — must be
    admin-gated. Pin the dependency so the gate can't be lost
    in a refactor."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    create_block = src.split('"/workflows", status_code=201', 1)[1].split(
        "@router.", 1
    )[0]
    assert "require_tenant_admin" in create_block


def test_tc_wf_002_create_validates_steps_array_present() -> None:
    """Definition MUST contain a 'steps' array. Foundation #8
    false-green prevention: silently accepting a def without
    steps would let the UI create a workflow that 5xxs on
    every run."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    assert (
        '"Workflow definition must contain a \'steps\' array"' in src
    )
    assert (
        'isinstance(body.definition.get("steps"), list)' in src
    )


def test_tc_wf_002_create_rejects_zero_step_workflow() -> None:
    """0-step workflows are rejected at create time — and again
    at run time (defense in depth, BUG-18/19)."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    assert '"Workflow must have at least one step"' in src


def test_tc_wf_002_create_validates_company_id_belongs_to_tenant() -> None:
    """If a company_id is supplied, it MUST belong to the calling
    tenant. Without this check, a tenant_admin from tenant A
    could associate a workflow with a company from tenant B's
    namespace by guessing a UUID."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    create_block = src.split('"/workflows", status_code=201', 1)[1].split(
        "@router.", 1
    )[0]
    assert "Company.id == company_uuid, Company.tenant_id == tid" in create_block
    assert 'HTTPException(404, "Company not found")' in create_block


# ─────────────────────────────────────────────────────────────────
# TC-WF-003 — Run workflow manually
# ─────────────────────────────────────────────────────────────────


def test_tc_wf_003_run_endpoint_pinned() -> None:
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    assert '@router.post("/workflows/{wf_id}/run")' in src


def test_tc_wf_003_run_returns_immediate_running_status() -> None:
    """The run endpoint creates a WorkflowRun with status="running"
    and dispatches execution to BackgroundTasks. The HTTP
    response returns immediately with run_id — async/long-running
    work doesn't block the request."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    run_block = src.split('@router.post("/workflows/{wf_id}/run")', 1)[1].split(
        "@router.", 1
    )[0]
    assert 'status="running"' in run_block
    assert "background_tasks.add_task(" in run_block
    assert "_execute_workflow_bg" in run_block
    # Response has the documented shape.
    assert '"run_id": str(run.id)' in run_block
    assert '"status": "running"' in run_block


def test_tc_wf_003_run_404_on_missing_workflow() -> None:
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    run_block = src.split('@router.post("/workflows/{wf_id}/run")', 1)[1].split(
        "@router.", 1
    )[0]
    assert 'HTTPException(404, "Workflow definition not found")' in run_block


def test_tc_wf_003_run_409_on_inactive_workflow() -> None:
    """Inactive workflows refuse to run with 409. Pin the gate so
    a future "always allow" refactor can't silently bypass the
    pause."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    run_block = src.split('@router.post("/workflows/{wf_id}/run")', 1)[1].split(
        "@router.", 1
    )[0]
    assert "if not wf.is_active:" in run_block
    assert 'HTTPException(409, "Workflow definition is inactive")' in run_block


def test_tc_wf_003_run_rejects_zero_step_def_at_runtime() -> None:
    """BUG-18/19 defense-in-depth: even if a 0-step def somehow
    got past create, run-time MUST reject it."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    run_block = src.split('@router.post("/workflows/{wf_id}/run")', 1)[1].split(
        "@router.", 1
    )[0]
    assert (
        '"Cannot run workflow with 0 steps. Add at least one step first."'
        in run_block
    )


def test_tc_wf_003_run_ab_variant_picked_deterministically() -> None:
    """A/B variant routing: same (workflow, subject) tuple always
    routes to the same variant so a user sees a coherent
    experience across calls within a campaign."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    run_block = src.split('@router.post("/workflows/{wf_id}/run")', 1)[1].split(
        "@router.", 1
    )[0]
    assert "from core.workflow_ab import pick_variant" in run_block
    # Subject derives from payload.user_id else tenant_id —
    # ensures consistent routing for the same caller.
    assert (
        '(body.payload or {}).get("user_id") or tenant_id' in run_block
    )


# ─────────────────────────────────────────────────────────────────
# TC-WF-004 — View workflow run details
# ─────────────────────────────────────────────────────────────────


def test_tc_wf_004_run_details_endpoint_pinned() -> None:
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    assert '@router.get("/workflows/runs/{run_id}")' in src


def test_tc_wf_004_run_details_loads_steps_via_selectinload() -> None:
    """Steps are loaded eagerly in a single round-trip via
    selectinload. Pin so a future "lazy load" refactor can't
    silently break the UI's step-by-step view."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    details_block = src.split(
        '@router.get("/workflows/runs/{run_id}")', 1
    )[1].split("@router.", 1)[0]
    assert "selectinload(WorkflowRun.steps)" in details_block


def test_tc_wf_004_run_details_query_is_tenant_scoped() -> None:
    """Cross-tenant run-id guess must NOT leak. Pin the
    tenant_id filter."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    details_block = src.split(
        '@router.get("/workflows/runs/{run_id}")', 1
    )[1].split("@router.", 1)[0]
    assert "WorkflowRun.id == run_id, WorkflowRun.tenant_id == tid" in details_block


# ─────────────────────────────────────────────────────────────────
# TC-WF-005 — Workflow with HITL step
# ─────────────────────────────────────────────────────────────────


def test_tc_wf_005_waiting_hitl_step_keeps_completed_at_null() -> None:
    """A step in waiting_hitl has NO completed_at — the engine
    is genuinely waiting for human input. Setting completed_at
    here would make the step look done in dashboards (and
    silently fail to wait for approval)."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    # The conditional `if step_status != "waiting_hitl" else None`
    # is the contract.
    assert 'step_status != "waiting_hitl"' in src
    # Before that line we set completed_at conditionally.
    assert "completed_at=(" in src and "else None" in src


def test_tc_wf_005_hitl_step_creates_queue_entry_with_documented_title() -> None:
    """Each waiting_hitl step inserts a HITLQueue row. Pin the
    title format because dashboards parse it for "what's
    waiting" — and the trigger_type='workflow_step' so HITL
    handlers know to resume the workflow on approval."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    assert 'if step_status == "waiting_hitl":' in src
    assert 'trigger_type="workflow_step"' in src
    assert 'title=f"Approval required: {step_def.get(\'title\', step_id)}"' in src


def test_tc_wf_005_hitl_step_assignee_role_resolved_with_fallback() -> None:
    """assignee_role pulls from the step result first, then the
    step definition, then 'admin' as the last-resort default —
    so an under-specified workflow still routes the approval
    to someone."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    hitl_section = src.split('if step_status == "waiting_hitl":', 1)[1][:1500]
    assert 'step_result.get(' in hitl_section
    assert '"assignee_role"' in hitl_section
    assert 'step_def.get("assignee_role", "admin")' in hitl_section


def test_tc_wf_005_hitl_step_priority_pulled_from_step_def_with_default() -> None:
    """Step priority defaults to 'normal' if the step doesn't
    set it — high/urgent must be opt-in."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    hitl_section = src.split('if step_status == "waiting_hitl":', 1)[1][:1500]
    assert 'priority=step_def.get("priority", "normal")' in hitl_section


def test_tc_wf_005_hitl_timeout_default_is_four_hours() -> None:
    """Approvals time out after 4h by default — pinning the
    default so a refactor can't silently make approvals
    stale-forever (they'd silently block workflows)."""
    src = (REPO / "api" / "v1" / "workflows.py").read_text(encoding="utf-8")
    hitl_section = src.split('if step_status == "waiting_hitl":', 1)[1][:1500]
    assert 'timeout_h = step_def.get("timeout_hours", 4)' in hitl_section
