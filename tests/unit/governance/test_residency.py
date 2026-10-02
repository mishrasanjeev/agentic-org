# SPDX-License-Identifier: Apache-2.0
"""Residency enforcement: decisions, the enforcement points and the compliance section."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.governance import residency as res
from core.governance.residency import Attestation, ResidencyBlocked, ResidencyDecision, check_provider

TENANT = uuid.uuid4()
ROOT = Path(__file__).resolve().parents[3]


def _att(provider: str, region: str = "IN", in_region: bool = True, no_training: bool = True) -> Attestation:
    return Attestation(
        id=str(uuid.uuid4()),
        provider=provider,
        data_region=region,
        in_region=in_region,
        no_training=no_training,
        evidence_ref="contract clause 7.2",
        attested_by="admin",
        expires_at=None,
    )


@pytest.fixture(autouse=True)
def _clear_caches():
    res.invalidate()
    yield
    res.invalidate()


@pytest.fixture
def enforce_on(monkeypatch):
    monkeypatch.setattr(res.settings, "residency_enforce", True)
    monkeypatch.setattr(res.settings, "env", "test")


def _with(attestations: list[Attestation], region: str = "IN"):
    return (
        patch.object(res, "active_attestations", AsyncMock(return_value=attestations)),
        patch.object(res, "tenant_data_region", AsyncMock(return_value=region)),
    )


class TestDecision:
    def test_off_by_default_reads_nothing(self, monkeypatch):
        monkeypatch.setattr(res.settings, "residency_enforce", False)
        region = AsyncMock(return_value="IN")
        with (
            patch("core.feature_flags.is_enabled", AsyncMock(return_value=False)),
            patch.object(res, "tenant_data_region", region),
        ):
            assert asyncio.run(check_provider(TENANT, "openai")).blocked is False
        region.assert_not_called()

    def test_authority_flag_turns_enforcement_on(self, monkeypatch):
        monkeypatch.setattr(res.settings, "residency_enforce", False)
        a, b = _with([])
        with patch("core.feature_flags.is_enabled", AsyncMock(return_value=True)), a, b:
            decision = asyncio.run(check_provider(TENANT, "openai"))
        assert decision.blocked is True and "no active attestation" in decision.reason

    def test_local_providers_need_no_attestation(self, enforce_on):
        a, b = _with([])
        with a, b:
            for provider in ("ollama", "vllm", "local_embeddings", "piper_local", "ollama:llama3"):
                assert asyncio.run(check_provider(TENANT, provider)).blocked is False

    def test_attested_provider_is_allowed(self, enforce_on):
        a, b = _with([_att("openai")])
        with a, b:
            decision = asyncio.run(check_provider(TENANT, "OpenAI"))
        assert decision.blocked is False and decision.attestation is not None

    @pytest.mark.parametrize(
        ("attestation", "fragment"),
        [
            (_att("openai", region="US"), "no active attestation for data region IN"),
            (_att("openai", in_region=False), "without in-region processing"),
            (_att("openai", no_training=False), "without a no-training commitment"),
            (_att("openai", in_region=False, no_training=False), "in-region processing or a no-training commitment"),
            (_att("gemini"), "provider openai (llm) has no active attestation"),
        ],
    )
    def test_incomplete_attestations_block(self, enforce_on, attestation, fragment):
        a, b = _with([attestation])
        with a, b:
            decision = asyncio.run(check_provider(TENANT, "openai"))
        assert decision.blocked is True and fragment in decision.reason
        assert decision.to_error()["error"]["code"] == "E4006"

    def test_without_a_tenant_the_platform_region_applies_and_nothing_is_attested(self, enforce_on, monkeypatch):
        monkeypatch.setattr(res.settings, "data_region", "IN")
        loader = AsyncMock()
        with patch.object(res, "active_attestations", loader):
            decision = asyncio.run(check_provider(None, "composio", kind="tool"))
        assert decision.blocked is True and decision.data_region == "IN"
        loader.assert_not_called()

    def test_read_failures_fail_closed_only_in_strict_runtime(self, enforce_on, monkeypatch):
        with patch.object(res, "tenant_data_region", AsyncMock(side_effect=RuntimeError("db down"))):
            assert asyncio.run(check_provider(TENANT, "openai")).blocked is False
            monkeypatch.setattr(res.settings, "env", "production")
            decision = asyncio.run(check_provider(TENANT, "openai"))
        assert decision.blocked is True and "could not be read" in decision.reason
        with patch.object(res, "tenant_data_region", AsyncMock(return_value="IN")):
            with patch.object(res, "active_attestations", AsyncMock(side_effect=RuntimeError("db down"))):
                assert asyncio.run(check_provider(TENANT, "openai")).blocked is True

    def test_assert_raises(self, enforce_on):
        a, b = _with([])
        with a, b, pytest.raises(ResidencyBlocked) as info:
            asyncio.run(res.assert_provider_allowed(TENANT, "anthropic"))
        assert info.value.decision.provider == "anthropic"

    def test_refusals_are_metered(self, enforce_on):
        from observability.metrics import residency_refusals_total

        before = residency_refusals_total.labels(reason="no_attestation")._value.get()
        a, b = _with([])
        with a, b:
            asyncio.run(check_provider(TENANT, "openai"))
        assert residency_refusals_total.labels(reason="no_attestation")._value.get() == before + 1


class TestRegions:
    def test_cloud_region_conformance(self):
        assert res.cloud_region_conforms("IN", "asia-south1") is True
        assert res.cloud_region_conforms("IN", "asia-south2") is True
        assert res.cloud_region_conforms("IN", "us-central1") is False
        assert res.cloud_region_conforms("EU", "europe-west1") is True
        assert res.cloud_region_conforms("in", "ASIA-SOUTH1") is True
        assert res.cloud_region_conforms("IN", "moon-base-1") is None
        assert res.cloud_region_conforms("IN", None) is None

    def test_tenant_region_is_cached(self):
        loader = AsyncMock(return_value="EU")
        with patch.object(res, "_load_region", loader):
            assert asyncio.run(res.tenant_data_region(TENANT)) == "EU"
            assert asyncio.run(res.tenant_data_region(TENANT)) == "EU"
        loader.assert_awaited_once()
        res.invalidate(TENANT)
        with patch.object(res, "_load_region", AsyncMock(return_value="IN")):
            assert asyncio.run(res.tenant_data_region(TENANT)) == "IN"

    def test_platform_region_without_tenant(self, monkeypatch):
        monkeypatch.setattr(res.settings, "data_region", "eu")
        assert asyncio.run(res.tenant_data_region(None)) == "EU"
        assert asyncio.run(res.tenant_data_region("not-a-uuid")) == "EU"


class TestReport:
    def test_section_reports_region_profile_and_attestations(self, enforce_on, monkeypatch):
        monkeypatch.setattr(res.settings, "data_region", "IN")
        monkeypatch.setattr(res.settings, "storage_region", "asia-south1")
        monkeypatch.setattr(res.settings, "tenancy_profile", "dedicated")
        monkeypatch.setattr(res.settings, "dr_standby_region", "asia-south2")
        monkeypatch.setattr(res.settings, "dr_last_drill_at", "2026-09-30")
        a, b = _with([_att("openai"), _att("openai", region="US")])
        with a, b:
            section = asyncio.run(res.report_section(TENANT))
        assert section["control_id"] == "RES-1" and section["status"] == "collected"
        assert section["enforcement"] == "on" and section["data_region"] == "IN"
        assert section["storage_region_conforms"] is True and section["tenancy_profile"] == "dedicated"
        assert section["disaster_recovery"] == {
            "standby_region": "asia-south2",
            "standby_conforms": True,
            "last_drill_at": "2026-09-30",
            "status": "active",
        }
        assert [a["data_region"] for a in section["provider_attestations"]] == ["IN"]

    def test_section_never_raises(self, enforce_on):
        with patch.object(res, "tenant_data_region", AsyncMock(side_effect=RuntimeError("db down"))):
            section = asyncio.run(res.report_section(TENANT))
        assert section["status"] == "unavailable" and section["control_id"] == "RES-1"


class TestEnforcementPoints:
    def test_credential_resolver_refuses_before_reading_any_credential(self, enforce_on):
        from core.ai_providers import resolver

        a, b = _with([])
        with a, b, patch.object(resolver, "_fetch_tenant_credential", AsyncMock()) as fetch:
            with pytest.raises(ResidencyBlocked):
                asyncio.run(resolver.get_provider_credential(TENANT, "openai", "llm"))
        fetch.assert_not_called()
        src = (ROOT / "core" / "ai_providers" / "resolver.py").read_text(encoding="utf-8")
        body = src[src.index("async def get_provider_credential(") :]
        assert body.index("assert_provider_allowed(") < body.index("# 1. Cache lookup")

    def test_resolver_allows_attested_provider(self, enforce_on, monkeypatch):
        from core.ai_providers import resolver

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder")
        a, b = _with([_att("openai")])
        with a, b, patch.object(resolver, "_fetch_tenant_credential", AsyncMock(return_value=None)):
            with patch.object(resolver, "_tenant_fallback_allowed", AsyncMock(return_value=True)):
                resolved = asyncio.run(resolver.get_provider_credential(TENANT, "openai", "llm"))
        assert resolved.source == "platform_env"

    def test_router_treats_a_block_as_non_transient(self):
        from core.llm.router import _is_transient_llm_failure

        blocked = ResidencyBlocked(ResidencyDecision(blocked=True, reason="r", provider="openai", data_region="IN"))
        assert _is_transient_llm_failure(blocked) is False

    def test_ragflow_paths_are_gated(self):
        src = (ROOT / "api" / "v1" / "knowledge.py").read_text(encoding="utf-8")
        assert src.count("_ragflow_available() and await _ragflow_allowed(tenant_id)") == 2

    def test_ragflow_allowed_follows_the_decision(self, enforce_on):
        from api.v1 import knowledge

        a, b = _with([])
        with a, b:
            assert asyncio.run(knowledge._ragflow_allowed(str(TENANT))) is False
        a, b = _with([_att("ragflow")])
        with a, b:
            assert asyncio.run(knowledge._ragflow_allowed(str(TENANT))) is True

    def test_composio_execute_is_gated(self, enforce_on):
        from connectors.composio import adapter as composio

        src = (ROOT / "connectors" / "composio" / "adapter.py").read_text(encoding="utf-8")
        body = src[src.index("async def execute_tool(") :]
        assert body.index('check_provider(tenant_id, "composio"') < body.index("self._tool_registry.get(tool_name)")
        assert composio is not None

    def test_tracing_export_stays_off_under_deployment_enforcement(self, enforce_on, monkeypatch):
        from observability import trace_redaction

        monkeypatch.setattr(trace_redaction, "_installed", False)
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        assert trace_redaction.install_trace_redaction() is False
        assert "LANGSMITH_TRACING" not in __import__("os").environ

    def test_compliance_package_carries_the_section(self):
        src = (ROOT / "api" / "v1" / "compliance.py").read_text(encoding="utf-8")
        assert '"data_residency": data_residency' in src and "residency.report_section(tid)" in src


class TestMigration:
    def test_revision_chain_and_rls(self):
        import importlib.util

        path = ROOT / "migrations" / "versions" / "v6_z33_provider_attestations.py"
        spec = importlib.util.spec_from_file_location("v6_z33_provider_attestations", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "v6z33_provider_attestations" and len(module.revision) <= 32
        assert module.down_revision == "v6z32_operator_overrides"
        src = path.read_text(encoding="utf-8")
        assert "ALTER TABLE provider_residency_attestations ENABLE ROW LEVEL SECURITY" in src
        assert "FORCE ROW LEVEL SECURITY" in src and "WITH CHECK" in src

    def test_flag_is_operator_managed(self):
        from core.feature_flags import is_reserved_flag_key

        assert is_reserved_flag_key(res.FLAG_KEY)

    def test_settings_defaults(self):
        from core.config import Settings

        s = Settings(secret_key="test-secret-key-16chars")
        assert s.residency_enforce is False and s.tenancy_profile == "shared"
        assert s.dr_standby_region is None and s.dr_last_drill_at is None
