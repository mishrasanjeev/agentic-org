from __future__ import annotations

from pathlib import Path

from core.billing.catalog import PUBLIC_PLAN_CATALOG, plan_by_id, plan_price_minor
from core.billing.limits import TIERS
from scripts.check_billing_catalog import catalog_consistency_issues

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_public_catalog_is_complete_typed_and_versioned() -> None:
    assert PUBLIC_PLAN_CATALOG.schema_version == "agenticorg.billing-plans.v1"
    assert PUBLIC_PLAN_CATALOG.complete is True
    assert PUBLIC_PLAN_CATALOG.plan_count == len(PUBLIC_PLAN_CATALOG.plans) == 3
    assert [plan.plan_id for plan in PUBLIC_PLAN_CATALOG.plans] == ["free", "pro", "enterprise"]


def test_runtime_limits_are_derived_from_public_catalog() -> None:
    for plan in PUBLIC_PLAN_CATALOG.plans:
        assert TIERS[plan.plan_id] == {
            "agent_count": -1 if plan.limits.agent_count is None else plan.limits.agent_count,
            "agent_runs": -1 if plan.limits.agent_runs is None else plan.limits.agent_runs,
            "storage_bytes": -1 if plan.limits.storage_bytes is None else plan.limits.storage_bytes,
        }


def test_catalog_contains_only_offer_facts_not_unsupported_entitlements() -> None:
    payload = PUBLIC_PLAN_CATALOG.model_dump_json().casefold()
    for unsupported in ("priority support", "24/7 support", "custom sla", "dedicated csm", "sso", "scim"):
        assert unsupported not in payload


def test_price_contract_uses_minor_units_and_explicit_interval() -> None:
    assert plan_price_minor("pro", "USD") == 2_00
    assert plan_price_minor("pro", "INR") == 9_999_00
    assert plan_by_id("pro").prices[0].interval == "month"


def test_checkout_invoice_and_runtime_maps_do_not_drift() -> None:
    assert catalog_consistency_issues() == []


def test_release_builds_include_billing_catalog_generator_dependencies() -> None:
    required_copies = (
        "COPY pyproject.toml README.md ../",
        "COPY core/billing/catalog.py ../core/billing/catalog.py",
    )
    for dockerfile_name in ("Dockerfile.ui", "Dockerfile.ui.cloudrun"):
        dockerfile = (REPO_ROOT / dockerfile_name).read_text(encoding="utf-8")
        build_index = dockerfile.index("RUN npm run build")
        for copy_instruction in required_copies:
            assert copy_instruction in dockerfile
            assert dockerfile.index(copy_instruction) < build_index

    workflow = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )
    frontend_quality = workflow.split("  frontend-quality:", 1)[1].split(
        "\n  unit-tests:", 1
    )[0]
    install_project = "uv pip install --system -e ."
    check_catalog = "python scripts/check_billing_catalog.py"
    assert install_project in frontend_quality
    assert check_catalog in frontend_quality
    assert frontend_quality.index(install_project) < frontend_quality.index(check_catalog)
