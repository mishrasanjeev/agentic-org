from __future__ import annotations

from pathlib import Path

PLAN = Path("docs/commerce-agent-hosted-staging-plan.md")
DATA_SETUP = Path("docs/commerce-agent-staging-data-setup.md")
E2E = Path("docs/commerce-agent-hosted-staging-e2e.md")
CONTRACT_GAP = Path("docs/commerce-agent-contract-gap-report.md")


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


def test_commerce_agent_hosted_staging_e2e_doc_exists_and_pins_targets() -> None:
    content = E2E.read_text(encoding="utf-8")

    for required in [
        "Commerce Sales Agent Hosted Staging E2E Plan",
        "https://staging.agenticorg.ai",
        "GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev",
        "GRANTEX_BASE_URL=https://api-staging.grantex.dev",
        "mch_staging_electronics_pilot",
        "cag_staging_agenticorg_sales",
        "Provider: `mock`",
        "MCP discovery",
        "A2A discovery",
    ]:
        assert required in content


def test_commerce_agent_hosted_staging_e2e_has_real_staging_command_plan() -> None:
    content = E2E.read_text(encoding="utf-8")

    for required in [
        "python demos/commerce_sales_agent_demo.py --mode=hosted-staging",
        "python -m pytest tests/evals/test_commerce_sales_agent_evals.py -q --hosted-staging",
        "The current local demo/eval path is mocked.",
        "Do not present mocked results as hosted staging evidence.",
    ]:
        assert required in content


def test_commerce_agent_hosted_staging_e2e_keeps_commerce_grantex_only() -> None:
    content = E2E.read_text(encoding="utf-8")

    assert "No direct Stripe/Plural/Pine/provider credential commerce path is prescribed." in content
    assert "must not call Stripe, Plural, Pine, or any payment provider directly for commerce" in content
    assert "must not read or handle provider credentials for commerce" in content
    assert "AgenticOrg consumes Grantex staging data only" in content
    assert "GRANTEX_COMMERCE_BASE_URL=https://api.grantex.dev" not in content
    assert "GRANTEX_BASE_URL=https://api.grantex.dev" not in content


def test_commerce_agent_hosted_staging_e2e_lists_negative_evals_and_no_secrets() -> None:
    content = E2E.read_text(encoding="utf-8")

    for required in [
        "missing consent is refused",
        "denied consent is refused",
        "revoked passport is refused",
        "expired passport is refused",
        "amount cap breach is refused",
        "disabled merchant is refused",
        "untrusted agent is refused",
        "stale inventory is refused",
        "unsupported EMI claim is refused",
        "unsupported discount claim is refused",
        "unsupported warranty claim is refused",
        "invalid webhook signature evidence remains a Grantex-side refusal",
    ]:
        assert required in content

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
    assert "does not deploy" in content
    assert "create cloud resources" in content


def test_commerce_agent_contract_gap_report_classifies_required_items() -> None:
    content = CONTRACT_GAP.read_text(encoding="utf-8")

    for status in ["`done`", "`partial`", "`blocked`", "`deferred`", "`not-started`"]:
        assert status in content

    for required in [
        "Grantex-only commerce connector",
        "Safe tool aliases",
        "Consent/passport guardrails",
        "Amount cap guardrails",
        "Disabled merchant/agent guardrails",
        "Stale inventory behavior",
        "Unsupported EMI/discount/warranty behavior",
        "No direct Stripe/Plural/Pine/provider credential path",
        "Mocked eval/demo status",
        "Real hosted staging eval gap",
        "Broader PRD Commerce Agent Pack",
    ]:
        assert required in content


def test_commerce_agent_contract_gap_report_preserves_no_provider_boundary() -> None:
    content = CONTRACT_GAP.read_text(encoding="utf-8")

    assert "No direct Stripe/Plural/Pine/provider credential commerce path is allowed." in content
    assert "The only commerce execution path is Grantex" in content
    assert "GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev" in content
    assert "GRANTEX_BASE_URL=https://api-staging.grantex.dev" in content
    assert "GRANTEX_COMMERCE_BASE_URL=https://api.grantex.dev" not in content
    assert "GRANTEX_BASE_URL=https://api.grantex.dev" not in content
    assert "retry a refused Grantex payment through another provider path" in content


def test_commerce_agent_contract_gap_report_preserves_real_staging_gap_and_no_secrets() -> None:
    content = CONTRACT_GAP.read_text(encoding="utf-8")

    for required in [
        "The real-staging gap is still blocked",
        "current local demo/eval path uses mocked Grantex responses",
        "No redacted hosted staging evidence exists yet",
        "demos/commerce_sales_agent_demo.py --mode=hosted-staging",
        "python -m pytest tests/evals/test_commerce_sales_agent_evals.py -q --hosted-staging",
    ]:
        assert required in content

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
