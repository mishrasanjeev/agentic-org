from __future__ import annotations

from pathlib import Path

PLAN = Path("docs/commerce-agent-hosted-staging-plan.md")
DATA_SETUP = Path("docs/commerce-agent-staging-data-setup.md")


def test_commerce_agent_hosted_staging_plan_pins_staging_topology() -> None:
    content = PLAN.read_text(encoding="utf-8")

    for required in [
        "agenticorg-api-staging",
        "agenticorg-ui-staging",
        "agenticorg-worker-staging",
        "agenticorg-beat-staging",
        "staging.agenticorg.ai",
        "GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev",
        "GRANTEX_BASE_URL=https://api-staging.grantex.dev",
        "commerce_sales_agent",
        "grantex_commerce:*",
        "No direct Stripe/Plural/Pine/provider credential path for commerce",
        "workflow_dispatch",
        "No production database or Redis",
    ]:
        assert required in content


def test_commerce_agent_hosted_staging_plan_lists_secret_names_only() -> None:
    content = PLAN.read_text(encoding="utf-8")

    for required in [
        "AGENTICORG_SECRET_KEY",
        "AGENTICORG_DATABASE_URL",
        "AGENTICORG_REDIS_URL",
        "GRANTEX_COMMERCE_BEARER_TOKEN",
        "GRANTEX_AGENT_ASSERTION",
        "GRANTEX_API_KEY",
        "LLM provider keys required by staging runtime",
    ]:
        assert required in content

    forbidden_values = [
        "sk_live_",
        "pk_live_",
        "-----BEGIN",
        "Bearer ",
        "passport.jwt",
        "idempotency-key:",
    ]
    for forbidden in forbidden_values:
        assert forbidden not in content


def test_commerce_agent_hosted_staging_plan_is_ascii_and_no_deploy() -> None:
    content = PLAN.read_text(encoding="utf-8")

    assert not any(ord(char) > 127 for char in content)
    assert "No deploy was performed" in content
    assert "No production config was changed" in content
    assert "No live payment or live Plural path was enabled" in content


def test_commerce_agent_staging_data_setup_pins_grantex_ids_and_env() -> None:
    content = DATA_SETUP.read_text(encoding="utf-8")

    for required in [
        "cten_staging_commerce",
        "mch_staging_electronics_pilot",
        "cag_staging_agenticorg_sales",
        "electronics_appliances",
        "Provider: `mock`",
        "GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev",
        "GRANTEX_BASE_URL=https://api-staging.grantex.dev",
        "AgenticOrg consumes Grantex staging data only",
    ]:
        assert required in content


def test_commerce_agent_staging_data_setup_keeps_commerce_grantex_only() -> None:
    content = DATA_SETUP.read_text(encoding="utf-8")

    assert "Commerce actions must use `grantex_commerce:*` tools." in content
    assert "No direct Stripe/Plural/Pine/provider credential commerce path is prescribed." in content
    assert "No provider credential handling is required for the Commerce Sales Agent." in content
    assert "Do not run commerce through direct Stripe, Plural, Pine, or provider credential paths." in content


def test_commerce_agent_staging_data_setup_has_no_secret_values() -> None:
    content = DATA_SETUP.read_text(encoding="utf-8")

    for required_secret_name in [
        "GRANTEX_COMMERCE_BEARER_TOKEN",
        "GRANTEX_AGENT_ASSERTION",
        "GRANTEX_API_KEY",
    ]:
        assert required_secret_name in content

    forbidden_values = [
        "sk_live_",
        "pk_live_",
        "-----BEGIN",
        "Bearer ",
        "passport.jwt",
        "idempotency-key:",
        "mock-webhook-secret",
    ]
    for forbidden in forbidden_values:
        assert forbidden not in content

    assert not any(ord(char) > 127 for char in content)
    assert "does not create resources" in content
    assert "does not deploy" in content
