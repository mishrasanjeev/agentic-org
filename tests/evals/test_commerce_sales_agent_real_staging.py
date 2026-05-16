from __future__ import annotations

import os
from pathlib import Path

import pytest

from demos.commerce_sales_agent_demo import run_real_staging_demo

pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    os.getenv("AGENTICORG_COMMERCE_REAL_STAGING") != "1",
    reason="Set AGENTICORG_COMMERCE_REAL_STAGING=1 only for approved Grantex staging or smoke runs.",
)
async def test_commerce_sales_agent_real_staging_eval_path() -> None:
    result = await run_real_staging_demo(
        grantex_base_url=os.getenv("GRANTEX_COMMERCE_BASE_URL"),
        allow_smoke_cloud_run_url=os.getenv("AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL"),
        evidence_report=os.getenv("AGENTICORG_COMMERCE_EVIDENCE_REPORT"),
    )

    assert result["scope"] == "real_staging_only"
    assert result["audit_summary"]["no_direct_provider_calls"] is True
    assert all(
        tool.startswith("grantex_commerce:")
        for tool in result["audit_summary"]["tool_sequence"]
    )
    assert {step["tool_alias"] for step in result["steps"]} >= {
        "grantex_commerce:merchant_get_profile",
        "grantex_commerce:catalog_search",
        "grantex_commerce:consent_request",
    }


async def test_real_staging_docs_record_c2b_failed_safe_result() -> None:
    evidence = Path("docs/reports/commerce-agent-real-staging-evidence.md").read_text(encoding="utf-8")
    hosted = Path("docs/commerce-agent-hosted-staging-e2e.md").read_text(encoding="utf-8")
    setup = Path("docs/commerce-agent-staging-data-setup.md").read_text(encoding="utf-8")

    for doc in (evidence, hosted, setup):
        lowered = doc.lower()
        assert "C2B result: 2 passed, 2 failed-safe, 10 skipped" in doc
        assert "Grantex-only path confirmed" in doc
        assert "no provider credential handling" in lowered
        assert "synthetic consent/passport fixture support" in lowered

    assert "| catalog_search | failed-safe | grantex_commerce:catalog_search" in evidence
    assert "| consent_request | failed-safe | grantex_commerce:consent_request" in evidence
