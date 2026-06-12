"""Regression pins for the May 5 GitHub issue/security sweep."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_structured_tool_kwargs_are_flattened() -> None:
    from core.langgraph.tool_adapter import _flatten_structured_tool_kwargs

    assert _flatten_structured_tool_kwargs({"kwargs": {"page": 1}}) == {"page": 1}
    assert _flatten_structured_tool_kwargs({"page": 1}) == {"page": 1}


def test_shadow_fixture_prompts_are_marked_trusted_and_skip_redaction() -> None:
    agents_src = _read("api/v1/agents.py")
    runner_src = _read("core/langgraph/runner.py")

    assert 'incoming_inputs["shadow_fixture_origin"] = True' in agents_src
    assert 'incoming_inputs.pop("shadow_fixture_origin", None)' in agents_src
    assert "trusted_shadow_fixture_prompt" in runner_src
    assert "pii_redaction_skipped_for_shadow_fixture" in runner_src


def test_zoho_india_gst_report_refuses_invoice_fallback() -> None:
    src = _read("connectors/finance/zoho_books.py")
    india_branch = src.split("async def generate_gst_report", 1)[1].split(
        'data = await self._get("/reports/gstsummary"',
        1,
    )[0]

    assert 'self.config.get("region") == "in"' in india_branch
    assert "GSTN connector" in india_branch
    assert 'await self.list_invoices(' not in india_branch
    assert '"/reports/gstsummary"' not in india_branch


def test_tds_period_parser_avoids_redos_prone_quarter_regex() -> None:
    from api.v1._tds_routing import _extract_period

    src = _read("api/v1/_tds_routing.py")
    assert "_QUARTER_RE" not in src
    assert _extract_period("Calculate TDS for Q1 FY26") == "Q1 FY26"
    assert _extract_period("Calculate TDS for Q2 FY 2026") == "Q2 FY 2026"
    assert _extract_period("Calculate TDS for April 2026") == "April 2026"


def test_db_pool_size_is_settings_configurable() -> None:
    config_src = _read("core/config.py")
    database_src = _read("core/database.py")

    assert "db_pool_size: int = Field(default=5" in config_src
    assert "db_max_overflow: int = Field(default=5" in config_src
    assert "pool_size=settings.db_pool_size" in database_src
    assert "max_overflow=settings.db_max_overflow" in database_src


def test_legal_and_insurance_packs_are_gated_until_tools_exist() -> None:
    from core.agents.packs.installer import get_pack_detail, install_pack

    for pack_name in ("legal", "insurance"):
        detail = get_pack_detail(pack_name)
        assert detail is not None
        assert detail["installable"] is False
        assert "Composio" in detail["install_disabled_reason"]
        try:
            install_pack(pack_name, "tenant-test")
        except ValueError as exc:
            assert "not installable" in str(exc)
        else:  # pragma: no cover - failure path only
            raise AssertionError(f"{pack_name} unexpectedly installed")


def test_industry_packs_ui_exposes_repair_not_only_uninstall() -> None:
    src = _read("ui/src/pages/IndustryPacks.tsx")

    assert "handleRepair" in src
    assert "Repair Pack" in src
    assert "Use Repair to refresh metadata while preserving history" in src


def test_campaign_pilot_defaults_bind_real_connectors() -> None:
    import connectors  # noqa: F401 - register connector classes
    from api.v1.agents import (
        _AGENT_TYPE_DEFAULT_CONNECTOR_IDS,
        _AGENT_TYPE_DEFAULT_TOOLS,
    )
    from core.langgraph.tool_adapter import _build_tool_index

    defaults = _AGENT_TYPE_DEFAULT_TOOLS["campaign_pilot"]
    index = _build_tool_index()
    assert defaults
    assert [tool for tool in defaults if tool not in index] == []
    assert _AGENT_TYPE_DEFAULT_CONNECTOR_IDS["campaign_pilot"] == [
        "registry-google_ads",
        "registry-linkedin_ads",
        "registry-sendgrid",
    ]


def test_connectors_list_serializer_tolerates_malformed_optional_fields() -> None:
    from api.v1.connectors import _connector_to_dict

    row = SimpleNamespace(
        id=uuid4(),
        name="broken-row",
        category=None,
        description=None,
        base_url=None,
        auth_type=None,
        auth_config={},
        secret_ref=None,
        tool_functions=None,
        data_schema_ref=None,
        rate_limit_rpm=None,
        timeout_ms=None,
        status=None,
        health_check_at="not-a-datetime",
        created_at="not-a-datetime",
    )

    serialized = _connector_to_dict(row)  # type: ignore[arg-type]
    assert serialized["tool_functions"] == []
    assert serialized["health_check_at"] is None
    assert serialized["created_at"] is None
    assert serialized["status"] == "active"
