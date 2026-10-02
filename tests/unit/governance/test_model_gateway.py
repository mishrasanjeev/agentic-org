# SPDX-License-Identifier: Apache-2.0
"""Model gateway decisions: matching, routing, fences, restriction, pass-through and the enforcement points."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.governance import model_gateway as gw
from core.governance.model_gateway import ModelGatewayRefused, Policy, RouteDecision, RouteRequest, decide

TENANT = uuid.uuid4()
ROOT = Path(__file__).resolve().parents[3]


def _policy(**over) -> Policy:
    base = {"id": str(uuid.uuid4()), "name": "p", "priority": 100}
    base.update(over)
    return Policy(**base)


def _rows(enabled: bool):
    from core.feature_flags import FlagRows

    row = {"enabled": True, "rollout_percentage": 100} if enabled else None
    return FlagRows(global_row=row, tenant_row=None)


def _req(**over) -> RouteRequest:
    base = {
        "tenant_id": TENANT,
        "use_case": "agent_run",
        "requested_provider": "gemini",
        "requested_model": "gemini-2.5-flash",
    }
    base.update(over)
    return RouteRequest(**base)


def _with(policies: list[Policy]):
    return patch.object(gw, "active_policies", AsyncMock(return_value=policies))


@pytest.fixture
def gateway_on(monkeypatch):
    monkeypatch.setattr(gw.settings, "model_gateway_enabled", True)
    monkeypatch.setattr(gw.settings, "env", "test")


class TestSwitch:
    def test_off_by_default_reads_nothing(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", False)
        reads = AsyncMock(return_value=[])
        with (
            patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=_rows(False))),
            patch.object(gw, "active_policies", reads),
        ):
            decision = asyncio.run(decide(_req()))
        assert decision.applied is False and decision.reason == "gateway off"
        assert decision.provider == "gemini" and decision.model == "gemini-2.5-flash"
        reads.assert_not_called()

    def test_the_authority_flag_turns_it_on(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", False)
        monkeypatch.setattr(gw.settings, "env", "test")
        with (
            patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=_rows(True))),
            _with([_policy(model="gpt-4o")]),
        ):
            decision = asyncio.run(decide(_req()))
        assert decision.applied and decision.model == "gpt-4o" and decision.provider == "openai"
        assert decision.correlation_id and decision.policy_name == "p"

    def test_without_a_tenant_the_callers_choice_stands(self, gateway_on):
        decision = asyncio.run(decide(_req(tenant_id=None)))
        assert decision.applied is False and decision.reason.startswith("no tenant")

    def test_an_unreadable_policy_set_refuses_in_strict_and_passes_through_in_relaxed(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", True)
        monkeypatch.setattr(gw.settings, "env", "production")
        with patch.object(gw, "active_policies", AsyncMock(side_effect=RuntimeError("db down"))):
            with pytest.raises(ModelGatewayRefused) as info:
                asyncio.run(decide(_req()))
            assert "could not be read" in info.value.reason
            assert info.value.to_error()["error"]["code"] == gw.ERROR_CODE
            monkeypatch.setattr(gw.settings, "env", "test")
            decision = asyncio.run(decide(_req()))
        assert decision.applied is False and "unreadable" in decision.reason

    def test_the_flag_is_operator_managed(self):
        from core.feature_flags import is_reserved_flag_key

        assert is_reserved_flag_key(gw.FLAG_KEY)


class TestMatching:
    def test_the_first_enabled_policy_in_priority_order_decides(self, gateway_on):
        broad = _policy(name="broad", priority=50, tier="tier3")
        finance = _policy(name="finance", priority=10, business_unit="finance", model="gpt-4o-mini")
        with _with([finance, broad]):
            for_finance = asyncio.run(decide(_req(business_unit="finance")))
            for_hr = asyncio.run(decide(_req(business_unit="hr")))
        assert for_finance.policy_name == "finance" and for_finance.model == "gpt-4o-mini"
        assert for_hr.policy_name == "broad" and for_hr.model == gw.tier_model("tier3")

    def test_every_match_field_a_policy_sets_must_equal_the_request(self):
        policy = _policy(
            use_case="agent_run", sensitivity="confidential", agent_id="a1", business_unit="finance", language="hi"
        )
        assert policy.matches(_req(sensitivity="Confidential", agent_id="a1", business_unit="Finance", language="HI"))
        other_agent = _req(sensitivity="confidential", agent_id="a2", business_unit="finance", language="hi")
        assert not policy.matches(other_agent)
        assert not policy.matches(_req(agent_id="a1", business_unit="finance", language="hi"))
        assert _policy().matches(_req())

    def test_no_match_keeps_the_callers_choice(self, gateway_on):
        with _with([_policy(use_case="completion", model="gpt-4o")]):
            decision = asyncio.run(decide(_req()))
        assert decision.applied is False and decision.model == "gemini-2.5-flash"
        assert "no policy matched" in decision.reason


class TestRouting:
    def test_a_provider_with_a_model(self, gateway_on):
        with _with([_policy(provider="openai", model="gpt-4o")]):
            decision = asyncio.run(decide(_req()))
        assert (decision.provider, decision.model) == ("openai", "gpt-4o")

    def test_a_provider_alone_keeps_a_model_of_that_provider_or_takes_its_first(self, gateway_on):
        with _with([_policy(provider="openai")]):
            kept = asyncio.run(decide(_req(requested_provider="openai", requested_model="gpt-4o-mini")))
            moved = asyncio.run(decide(_req()))
        assert kept.model == "gpt-4o-mini"
        assert moved.provider == "openai" and moved.model == "gpt-4o"

    def test_a_model_alone_sets_the_provider_from_the_catalogue(self, gateway_on):
        with _with([_policy(model="claude-sonnet-4-5-20250929")]):
            decision = asyncio.run(decide(_req()))
        assert decision.provider == "anthropic"

    def test_a_tier_resolves_like_the_smart_router(self, gateway_on):
        with _with([_policy(tier="tier1")]):
            decision = asyncio.run(decide(_req()))
        assert decision.model == gw.tier_model("tier1") and decision.provider == gw.provider_for_model(decision.model)

    def test_provider_names_are_the_catalogues(self):
        assert gw.normalise_provider("Claude") == "anthropic" and gw.normalise_provider("gpt") == "openai"
        assert gw.normalise_provider("openai_compatible") == "openai_compatible"
        assert gw.provider_for_model("ollama:llama3") == "ollama" and gw.provider_for_model("vllm:x") == "vllm"
        assert gw.provider_for_model("gpt-4o") == "openai" and gw.provider_for_model("") is None
        assert gw.provider_for_model("gemini-2.5-flash", "Claude") == "anthropic"


class TestFenceAndRestriction:
    def test_a_fence_reads_the_provider_from_the_model_when_none_is_pinned(self, gateway_on):
        with _with([_policy(name="fence", allowed_providers=("openai",))]):
            legacy = asyncio.run(decide(_req(requested_provider=None, requested_model="gpt-4o")))
            with pytest.raises(ModelGatewayRefused):
                asyncio.run(decide(_req(requested_provider=None, requested_model="gemini-2.5-flash")))
        assert legacy.applied and legacy.provider == "openai" and legacy.model == "gpt-4o"

    def test_a_restricted_request_that_names_no_provider_is_refused(self, gateway_on):
        with _with([]), patch("core.governance.residency.check_provider", AsyncMock()) as residency:
            with pytest.raises(ModelGatewayRefused, match="named neither a provider nor a model"):
                asyncio.run(decide(_req(sensitivity="restricted", requested_provider=None, requested_model="")))
        residency.assert_not_called()

    def test_a_provider_outside_the_fence_is_refused_with_the_policy_named(self, gateway_on):
        fence = _policy(name="fence", allowed_providers=("openai",))
        with _with([fence]):
            with pytest.raises(ModelGatewayRefused) as info:
                asyncio.run(decide(_req()))
            allowed = asyncio.run(decide(_req(requested_provider="openai", requested_model="gpt-4o")))
        assert info.value.policy_name == "fence" and "outside the providers" in info.value.reason
        assert allowed.applied and allowed.provider == "openai" and allowed.model == "gpt-4o"

    def test_restricted_data_may_only_reach_in_region_providers(self, gateway_on):
        asked: list[tuple[str, str, bool | None]] = []

        async def check(_tid, provider, *, kind="llm", enforce=None):
            asked.append((provider, kind, enforce))
            return SimpleNamespace(blocked=provider != "ollama", reason="no attestation")

        with patch("core.governance.residency.check_provider", check), _with([]):
            with pytest.raises(ModelGatewayRefused) as info:
                asyncio.run(decide(_req(sensitivity="restricted")))
            local = asyncio.run(
                decide(_req(sensitivity="restricted", requested_provider="ollama", requested_model="ollama:llama3"))
            )
        assert "restricted data may not reach provider gemini" in info.value.reason
        assert local.restricted is True and local.applied is False
        assert asked[0] == ("gemini", "llm", True)

    def test_an_in_region_only_policy_restricts_unlabelled_requests_too(self, gateway_on):
        blocked = AsyncMock(return_value=SimpleNamespace(blocked=True, reason="no attestation"))
        with patch("core.governance.residency.check_provider", blocked), _with([_policy(in_region_only=True)]):
            with pytest.raises(ModelGatewayRefused):
                asyncio.run(decide(_req()))

    def test_the_residency_check_can_be_asked_regardless_of_the_flag(self, monkeypatch):
        from core.governance import residency as res

        monkeypatch.setattr(res.settings, "residency_enforce", False)
        with (
            patch.object(res, "active_attestations", AsyncMock(return_value=[])),
            patch.object(res, "tenant_data_region", AsyncMock(return_value="IN")),
        ):
            assert asyncio.run(res.check_provider(TENANT, "gemini", enforce=True)).blocked is True
            assert asyncio.run(res.check_provider(TENANT, "gemini", enforce=False)).blocked is False
            assert asyncio.run(res.check_provider(TENANT, "ollama", enforce=True)).blocked is False


class TestEvaluate:
    def test_a_dry_run_evaluates_the_policies_while_the_gateway_is_off(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", False)
        monkeypatch.setattr(gw.settings, "env", "test")
        meter = patch.object(gw, "_meter")
        with (
            patch.object(gw, "enabled", AsyncMock(return_value=False)),
            _with([_policy(name="fence", provider="openai", model="gpt-4o", allowed_providers=("openai",))]),
            meter as metered,
        ):
            evaluation = asyncio.run(gw.evaluate(_req()))
            refused = asyncio.run(gw.evaluate(_req(requested_provider="gemini", requested_model="gemini-2.5-pro")))
        assert evaluation.enabled is False and evaluation.decision is not None
        assert evaluation.decision.applied and evaluation.decision.model == "gpt-4o"
        assert refused.decision is None and refused.refusal is None or refused.decision is not None
        metered.assert_not_called()

    def test_a_dry_run_reports_a_refusal_as_data(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", False)
        monkeypatch.setattr(gw.settings, "env", "test")
        with (
            patch.object(gw, "enabled", AsyncMock(return_value=True)),
            _with([_policy(name="fence", allowed_providers=("openai",))]),
        ):
            evaluation = asyncio.run(gw.evaluate(_req()))
        assert evaluation.enabled is True and evaluation.decision is None
        assert evaluation.refusal is not None and evaluation.refusal.policy_name == "fence"
        with pytest.raises(ValueError, match="needs a tenant"):
            asyncio.run(gw.evaluate(_req(tenant_id=None)))


class TestValidation:
    @pytest.mark.parametrize(
        ("fields", "message"),
        [
            ({"name": "p"}, "must route"),
            ({"name": "p", "sensitivity": "secret", "tier": "tier1"}, "sensitivity must be"),
            ({"name": "p", "tier": "tier9"}, "tier must be"),
            ({"name": "p", "model": "not-a-model"}, "not in the provider catalogue"),
            ({"name": "p", "provider": "openai", "allowed_providers": ["gemini"]}, "must be among"),
            ({"name": "p", "allowed_providers": []}, "at least one"),
            ({"name": "", "tier": "tier1"}, "needs a name"),
            ({"name": "p", "tier": "tier1", "priority": -1}, "priority"),
        ],
    )
    def test_unusable_policies_are_refused(self, fields, message):
        with pytest.raises(ValueError, match=message):
            gw.validate_policy_fields(fields)

    def test_a_provider_and_model_must_be_a_catalogue_pair(self):
        with pytest.raises(ValueError):
            gw.validate_policy_fields({"name": "p", "provider": "openai", "model": "gemini-2.5-pro"})

    def test_fields_are_normalised(self):
        clean = gw.validate_policy_fields(
            {
                "name": " Finance ",
                "business_unit": "Finance",
                "provider": "Claude",
                "allowed_providers": ["anthropic", "GPT"],
                "sensitivity": "Restricted",
                "tier": "Tier2",
            }
        )
        assert clean["name"] == "Finance" and clean["business_unit"] == "finance"
        assert clean["provider"] == "anthropic" and clean["allowed_providers"] == ["anthropic", "openai"]
        assert clean["sensitivity"] == "restricted" and clean["tier"] == "tier2" and clean["enabled"] is True


class TestEnforcementPoints:
    @staticmethod
    def _run(route):
        from core.langgraph import runner

        prefetch = AsyncMock(side_effect=RuntimeError("stop at the prefetch"))
        with (
            patch("core.billing.metering.gate_agent_run", AsyncMock(return_value=None)),
            patch("core.governance.operator_override.check", AsyncMock(return_value=SimpleNamespace(blocked=False))),
            patch("core.database.get_tenant_session", side_effect=RuntimeError("no database in this test")),
            patch.object(runner, "route_for_agent", route),
            patch.object(runner, "prefetch_llm_credential", prefetch),
            patch.object(runner, "build_agent_graph") as graph,
        ):
            try:
                result = asyncio.run(
                    runner.run_agent(
                        agent_id="a1",
                        agent_type="finance",
                        domain="finance",
                        tenant_id=str(TENANT),
                        system_prompt="x",
                        authorized_tools=[],
                        task_input={},
                        llm_model="gemini-2.5-flash",
                        llm_provider="gemini",
                    )
                )
            except RuntimeError as exc:
                result = {"status": str(exc)}
        return result, route, prefetch, graph

    def test_the_runner_routes_before_the_credential_prefetch(self):
        decision = RouteDecision(provider="openai", model="gpt-4o", correlation_id="c1", reason="p", applied=True)
        result, route, prefetch, graph = self._run(AsyncMock(return_value=decision))
        assert result["status"] == "stop at the prefetch"
        route.assert_awaited_once_with(
            str(TENANT),
            use_case="agent_run",
            agent_id="a1",
            business_unit="finance",
            requested_provider="gemini",
            requested_model="gemini-2.5-flash",
        )
        prefetch.assert_awaited_once_with("gpt-4o", "openai", str(TENANT))
        graph.assert_not_called()

    def test_a_pass_through_keeps_the_agents_own_model(self):
        decision = RouteDecision(provider="gemini", model="gemini-2.5-flash", correlation_id="c1", reason="gateway off")
        _result, _route, prefetch, _graph = self._run(AsyncMock(return_value=decision))
        prefetch.assert_awaited_once_with("gemini-2.5-flash", "gemini", str(TENANT))

    def test_the_runner_returns_the_refused_result(self):
        refusal = ModelGatewayRefused("refused", correlation_id="c2", policy_id="p1", policy_name="fence")
        result, _route, prefetch, graph = self._run(AsyncMock(side_effect=refusal))
        assert result["status"] == "model_gateway_refused" and result["error_code"] == gw.ERROR_CODE
        assert result["model_gateway"] == {"correlation_id": "c2", "policy_id": "p1", "policy_name": "fence"}
        prefetch.assert_not_called()
        graph.assert_not_called()

    def test_the_resume_path_routes_before_its_credential_prefetch(self):
        src = (ROOT / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        resume = src[src.index("async def resume_agent(") :]
        assert resume.index("check_operator_override(") < resume.index("route_for_agent(") < resume.index(
            "prefetch_llm_credential("
        )
        assert 'use_case="agent_resume"' in resume

    def test_the_router_applies_the_gateway_model_and_keeps_failover_within_its_provider(self):
        from core.llm.router import LLMRouter

        router = LLMRouter()
        router.primary_model = "gemini-2.5-flash"
        router.fallback_model = "gemini-2.5-pro"
        decision = RouteDecision(provider="openai", model="gpt-4o", correlation_id="c", reason="policy", applied=True)
        called: list[str] = []

        async def fake_call(model, _messages, _temperature, _max_tokens, **_scope):
            called.append(model)
            raise TimeoutError("transient")

        with patch("core.llm.router.gateway_decide", AsyncMock(return_value=decision)) as ask:
            with patch.object(router, "_call_model", fake_call):
                with pytest.raises(TimeoutError):
                    asyncio.run(router.complete([{"role": "user", "content": "hi"}], tenant_id=str(TENANT)))
        assert called == ["gpt-4o"]
        request = ask.await_args.args[0]
        assert request.use_case == "completion" and request.requested_model == "gemini-2.5-flash"

    def test_the_router_refuses_a_provider_it_cannot_dispatch(self):
        from core.llm.router import LLMRouter

        router = LLMRouter()
        decision = RouteDecision(
            provider="openai_compatible", model="gpt-4o", correlation_id="c", reason="policy", applied=True
        )
        with (
            patch("core.llm.router.gateway_decide", AsyncMock(return_value=decision)),
            patch.object(router, "_call_model", AsyncMock()) as call,
        ):
            with pytest.raises(ModelGatewayRefused, match="cannot reach provider openai_compatible"):
                asyncio.run(router.complete([{"role": "user", "content": "hi"}], tenant_id=str(TENANT)))
        call.assert_not_called()

    def test_the_router_refusal_is_not_retried_and_a_call_without_a_tenant_does_not_ask(self):
        from core.llm.router import LLMResponse, LLMRouter, _is_transient_llm_failure

        refusal = ModelGatewayRefused("refused", correlation_id="c")
        assert _is_transient_llm_failure(refusal) is False
        router = LLMRouter()
        with patch("core.llm.router.gateway_decide", AsyncMock(side_effect=refusal)):
            with pytest.raises(ModelGatewayRefused):
                asyncio.run(router.complete([{"role": "user", "content": "hi"}], tenant_id=str(TENANT)))
        response = LLMResponse(content="ok", model="gemini-2.5-flash", tokens_used=1, cost_usd=0.0, latency_ms=1)
        with (
            patch("core.llm.router.gateway_decide", AsyncMock()) as ask,
            patch.object(router, "_call_model", AsyncMock(return_value=response)),
        ):
            assert asyncio.run(router.complete([{"role": "user", "content": "hi"}])).content == "ok"
        ask.assert_not_called()


class TestMigration:
    def test_revision_chain_and_rls(self):
        import importlib.util

        path = ROOT / "migrations" / "versions" / "v6_z34_model_routing_policies.py"
        spec = importlib.util.spec_from_file_location("v6_z34_model_routing_policies", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "v6z34_model_routing_policies" and len(module.revision) <= 32
        assert module.down_revision == "v6z33_provider_attestations"
        src = path.read_text(encoding="utf-8")
        assert "ALTER TABLE model_routing_policies ENABLE ROW LEVEL SECURITY" in src
        assert "FORCE ROW LEVEL SECURITY" in src and "WITH CHECK" in src
