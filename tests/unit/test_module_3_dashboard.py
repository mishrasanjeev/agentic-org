"""Foundation #6 — Module 3 Dashboard.

Source-pin tests for TC-DASH-001 through TC-DASH-005.

The dashboard is the post-login landing surface — every metric
must be derived from a real API call (no fabricated KPIs).

Pinned contracts:

- The dashboard composes from THREE backend calls (agents,
  approvals, audit) via Promise.allSettled. Partial failures
  surface as visible warnings — never silent zeros.
- Metric derivations: totalAgents = agents.length,
  activeAgents = filter(status=="active"), shadowAgents =
  filter(status=="shadow"), pendingApprovals =
  filter(status=="pending"). Pin the exact derivation strings
  so a refactor can't silently change what "Active Agents"
  counts.
- The /audit + /agents + /approvals endpoints are the data
  contract — every dashboard widget keys off these.
- CFO-domain restriction is enforced at the /agents endpoint
  via the JWT user_domains claim (Module 4 cross-pin).
- Decorative-state ban: P6.1 audit removed the fabricated
  "Deflection Rate 73%" card. Pin the comment so a future
  refactor can't reintroduce hardcoded fake KPIs.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-DASH-001 — Dashboard loads with metrics
# ─────────────────────────────────────────────────────────────────


def test_tc_dash_001_dashboard_fetches_three_apis() -> None:
    """The dashboard composes from /agents, /approvals, /audit.
    Pin the call set so a future refactor can't silently drop
    a data source (which would make a metric quietly zero)."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert 'api.get("/agents")' in src
    assert 'api.get("/approvals")' in src
    assert 'api.get("/audit"' in src


def test_tc_dash_001_uses_promise_allsettled_not_all() -> None:
    """Promise.allSettled is the correct primitive — Promise.all
    would reject on first failure, leaving the dashboard blank.
    With allSettled, one failed call surfaces as a visible
    warning while the other two metrics still render."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "Promise.allSettled" in src
    assert "Promise.all(" not in src.split("fetchAll")[1].split("\n}\n")[0]


def test_tc_dash_001_metric_derivations_pinned() -> None:
    """Pin the EXACT derivation string for each metric so the
    label-to-value mapping can't silently flip. ``Active
    Agents`` MUST count status=='active' — not status!='paused'
    or anything else."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "const totalAgents = agents.length;" in src
    assert 'const activeAgents = agents.filter((a) => a.status === "active").length;' in src
    assert 'const shadowAgents = agents.filter((a) => a.status === "shadow").length;' in src
    assert 'const pendingApprovals = approvals.filter((a) => a.status === "pending").length;' in src


# ─────────────────────────────────────────────────────────────────
# TC-DASH-002 — Charts render
# ─────────────────────────────────────────────────────────────────


def test_tc_dash_002_status_distribution_pie_data() -> None:
    """The pie chart input is built from agent statusCounts. Pin
    the source so a future refactor can't silently feed it
    placeholder data."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "const statusCounts: Record<string, number> = {};" in src
    assert "statusData = Object.entries(statusCounts)" in src


def test_tc_dash_002_domain_distribution_bar_data() -> None:
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "const domainCounts: Record<string, number> = {};" in src
    assert "domainData = Object.entries(domainCounts)" in src


def test_tc_dash_002_confidence_chart_handles_null_floor() -> None:
    """confidence_floor can be null (legacy agents). Pin the
    null-guard so the chart doesn't render NaN bars."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "a.confidence_floor != null" in src


# ─────────────────────────────────────────────────────────────────
# TC-DASH-003 — Recent activity feed
# ─────────────────────────────────────────────────────────────────


def test_tc_dash_003_recent_activity_caps_at_10() -> None:
    """Audit fetch limits to 10 entries — keeps the dashboard
    payload bounded. Without the cap, a tenant with months of
    audit history blows the response size on every page load."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "limit: 10" in src


def test_tc_dash_003_recent_activity_empty_state_pinned() -> None:
    """When auditEntries is empty, show a "No recent activity"
    message — NOT a blank card (which looks like a render bug)."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "No recent activity" in src


# ─────────────────────────────────────────────────────────────────
# TC-DASH-004 — Pending approvals summary
# ─────────────────────────────────────────────────────────────────


def test_tc_dash_004_pending_items_filtered_from_approvals_response() -> None:
    """The "Pending Approvals" widget filters the approvals
    response client-side — same source as the count metric."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert 'const pendingItems = approvals.filter((a) => a.status === "pending");' in src


def test_tc_dash_004_resolved_approvals_excludes_expired() -> None:
    """Resolved % counts BOTH approved and rejected — but NOT
    expired (those weren't acted on; counting them as resolved
    would inflate the apparent throughput)."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert (
        '(a) => a.status !== "pending" && a.status !== "expired"' in src
    )


# ─────────────────────────────────────────────────────────────────
# TC-DASH-005 — CFO sees only finance data (RBAC)
# ─────────────────────────────────────────────────────────────────


def test_tc_dash_005_agents_endpoint_enforces_user_domains_filter() -> None:
    """Cross-pin with TC-CC-001 / TC-AGT-002: domain RBAC is
    enforced at the /agents endpoint via the JWT user_domains
    claim. The dashboard inherits this — when the CFO loads
    the page, the /agents response only contains Finance rows,
    so every derived metric (totalAgents, activeAgents,
    statusCounts, domainCounts) is automatically scoped."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    list_block = src.split('@router.get("/agents", response_model=', 1)[1].split(
        "@router.", 1
    )[0]
    assert "Agent.domain.in_(user_domains)" in list_block
    assert "user_domains: list[str] | None = Depends(get_user_domains)" in src


# ─────────────────────────────────────────────────────────────────
# Cross-pin — decorative-state / fabricated-KPI ban
# ─────────────────────────────────────────────────────────────────


def test_dashboard_documents_decorative_state_removal() -> None:
    """The P6.1 Enterprise Readiness audit removed the fabricated
    "Deflection Rate 73%" card. Pin the explanatory comment so
    a future refactor can't reintroduce hardcoded fake KPIs
    (the closure-plan-banned 'decorative state' pattern)."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert "Deflection Rate 73%" in src
    assert "decorative state" in src or "fabricated KPI" in src


def test_dashboard_partial_failure_surfaces_warning_not_silence() -> None:
    """Foundation #8 false-green prevention: when a fetch fails,
    the dashboard MUST display a warning. Silently rendering
    zeros would let a CFO believe agents are inactive when in
    reality the /agents call timed out."""
    src = (REPO / "ui" / "src" / "pages" / "Dashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert 'warnings.push("Agents data could not be loaded")' in src
    assert 'warnings.push("Approvals data could not be loaded")' in src
    assert 'warnings.push("Audit data could not be loaded")' in src
    # And the warnings are rendered to the user.
    assert "fetchWarnings.length > 0" in src
