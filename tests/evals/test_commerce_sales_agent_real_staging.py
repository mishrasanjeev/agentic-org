from __future__ import annotations

import os

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
