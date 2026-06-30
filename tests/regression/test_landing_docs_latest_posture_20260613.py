from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_landing_page_reflects_oacp_protocol_without_milestone_section() -> None:
    landing = _read("ui/src/pages/Landing.tsx")
    oacp_page = _read("ui/src/pages/OpenAgenticCommerceProtocol.tsx")
    index = _read("ui/index.html")

    for expected in (
        "Open Agentic Commerce Protocol",
        "Buyer and seller AI-agent runtime for OACP commerce.",
        "source-backed artifacts",
        "Explore the OACP flow",
        "does not claim universal purchase support",
    ):
        assert expected in landing

    for expected in (
        "From merchant data to a buyer-agent answer.",
        "Every buyer request resolves to one safe posture.",
        "Provider-owned execution boundary",
        "Non-goals this page does not claim",
        "No live checkout or payment execution claim",
    ):
        assert expected in oacp_page

    for stale in (
        "Latest shipped foundation",
        "Latest mainline work",
        "June hardening",
        "Review latest foundation",
        "Project Apex",
        "places the order",
        "Shopping Agent",
    ):
        assert stale not in landing

    assert "Open Agentic Commerce Protocol artifact boundaries" in index
    assert "Open Agentic Commerce Protocol explains" in index
    assert "Latest foundation:" not in index
    assert "Commerce guardrail" not in index
    assert "Cloud Run release safety" not in index
    assert "AI Virtual Employees for Enterprise | Create & Deploy AI Agents" not in index
    assert "End-to-End MCP/A2A Demo" not in index


def test_integration_workflow_is_non_executing_oacp_preview() -> None:
    workflow = _read("ui/src/pages/IntegrationWorkflow.tsx")

    for expected in (
        "OACP-Grounded Commerce Preview",
        "allowed_to_execute=false",
        "Prepared handoff review",
        "Fail-Closed Commerce Boundary",
        "no execute()",
        "checkout/payment blocked",
    ):
        assert expected in workflow

    for stale in (
        "shopping_agent",
        "Shopping Agent",
        "place_order",
        "result: ordered",
        "Ordered Sony",
        "places the order",
        "real order placement",
    ):
        assert stale not in workflow


def test_readme_and_deploy_docs_describe_current_cloud_run_path() -> None:
    readme = _read("README.md")
    deploy = _read("docs/deployment.md")
    backup = _read("docs/BACKUP_AND_DR.md")
    product_prd = _read("docs/PRD.md")
    archived_prd = _read("docs/PRD_v4.0.0.md")
    user_guide = _read("docs/AgenticOrg_Complete_User_Guide_v5.0.html")
    ddl_roadmap = _read("docs/roadmap/startup_ddl_removal.md")

    assert "Latest Mainline Status (2026-06-30)" in readme
    assert "OACP Shopify runtime vertical" in readme
    assert "OACP Seller And Buyer Commerce Runtime" in readme
    assert "durable public-safe refs" in readme
    assert "maintenance planner" in readme
    assert "Cloud Run manual helper" in readme
    assert "GKE Production" not in readme

    assert "2026-06-13 status" in deploy
    assert "asia-southeast1" in deploy
    assert "GAR_REGION=asia-south1" in deploy
    assert "Legacy Kubernetes Lean" in deploy
    assert "GKE Autopilot" not in deploy
    assert "Cloud Run rewrite" not in deploy
    assert "disabled GKE block" not in deploy

    assert "Cloud Run services `agenticorg-api` and `agenticorg-ui`" in backup
    assert "GKE cluster loss" not in backup
    assert "agenticorg-prod-gke" not in backup

    assert "Latest posture (2026-06-13)" in product_prd
    assert "Cloud Run services in asia-southeast1" in product_prd
    assert "GKE Autopilot, Cloud SQL" not in product_prd
    assert "Archive note (2026-06-13)" in archived_prd

    assert "Latest Platform Posture (2026-06-13)" in user_guide
    assert "E.3 Cloud Run Production" in user_guide
    assert "scripts/deploy_cloud_run.sh" in user_guide
    assert "Kubernetes (Helm)" not in user_guide
    assert "GKE Autopilot" not in user_guide

    assert "Cloud Run app services receive traffic" in ddl_roadmap
    assert "Helm values" not in ddl_roadmap


def test_commerce_docs_capture_c6x4_c6x5_boundaries() -> None:
    overview = _read("docs/commerce-agent-overview.md")
    developer = _read("docs/commerce-agent-developer-guide.md")
    prd = _read("docs/commerce-agent-agentic-commerce-implementation-prd.md")

    for doc in (overview, developer, prd):
        assert "C6X4" in doc
        assert "C6X5" in doc
        assert "OACP" in doc

    assert "oacp_artifact_cache_records" in overview
    assert "does not refresh, evict, purge, schedule, call Grantex live" in overview
    assert "Maintenance planning may recommend refresh" in developer
    assert "Current OACP Status Through C6X5" in prd
    assert "persistent cache scoped by buyer agent" not in prd
