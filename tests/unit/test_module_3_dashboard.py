"""Foundation #6 — Module 3 Dashboard.

The legacy dashboard page (``ui/src/pages/Dashboard.tsx``) these tests pinned
(TC-DASH-001 to 004: three API calls, metric derivations, charts, recent
activity, partial-failure warnings, the decorative-KPI ban) was retired into
Insights in Phase 4: ``/dashboard`` now opens each role's landing page
(``ui/src/__tests__/legacyRetirement.test.tsx``), and every Insights number
comes from a registry metric with a defined empty state
(``tests/unit/test_insights_registry.py``, ``ui/src/__tests__/insights.lib.test.ts``).

What still applies is pinned here: agent lists walk every page (TC-DASH-001),
and the /agents endpoint enforces the caller's domains (TC-DASH-005).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_tc_dash_001_dashboard_fetches_all_agent_pages() -> None:
    """Aishwarya 2026-05-11 TC_001: agent lists must not stop at the /agents
    default page of 20. They use the shared listAll helper, which walks every
    page at the backend cap of 100."""
    api_src = (REPO / "ui" / "src" / "lib" / "api.ts").read_text(
        encoding="utf-8"
    )
    agents_src = (REPO / "ui" / "src" / "pages" / "Agents.tsx").read_text(
        encoding="utf-8"
    )
    playground_src = (REPO / "ui" / "src" / "pages" / "Playground.tsx").read_text(
        encoding="utf-8"
    )
    assert "async function listAllPages" in api_src
    assert 'per_page: perPage' in api_src
    assert "await agentsApi.listAll(params)" in agents_src
    assert "per_page=100" in playground_src
    assert "per_page=50" not in playground_src


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
    # Bug sheet 2026-09-14 rows 19/22: the list filter moved into
    # core/ownership.agent_visibility_clause, which keeps the domain RBAC for
    # shared agents and adds the caller's own personal agents.
    assert "agent_visibility_clause(Agent" in list_block
    ownership_src = (REPO / "core" / "ownership.py").read_text(encoding="utf-8")
    assert "agent_model.domain.in_(caller.domains)" in ownership_src
    assert "user_domains: list[str] | None = Depends(get_user_domains)" in src
