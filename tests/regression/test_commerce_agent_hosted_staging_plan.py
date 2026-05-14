from __future__ import annotations

from pathlib import Path


PLAN = Path("docs/commerce-agent-hosted-staging-plan.md")


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
