# SPDX-License-Identifier: Apache-2.0
"""Model gateway decisions: matching, routing, fences, restriction, pass-through and the enforcement points."""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
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


def _with(policies: list[Policy], access: list[gw.AccessPolicy] | None = None, limits: list | None = None):
    policy_set = gw.PolicySet(routing=tuple(policies), access=tuple(access or ()), limits=tuple(limits or ()))
    return patch.object(gw, "active_policy_set", AsyncMock(return_value=policy_set))


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
            patch.object(gw, "active_policy_set", reads),
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
        with patch.object(gw, "active_policy_set", AsyncMock(side_effect=RuntimeError("db down"))):
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
        assert result["model_gateway"] == {
            "correlation_id": "c2",
            "policy_id": "p1",
            "policy_name": "fence",
            "kind": "routing",
        }
        prefetch.assert_not_called()
        graph.assert_not_called()

    def test_the_resume_path_routes_before_its_credential_prefetch(self):
        src = (ROOT / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        resume = src[src.index("async def resume_agent(") :]
        assert (
            resume.index("check_operator_override(")
            < resume.index("route_for_agent(")
            < resume.index("prefetch_llm_credential(")
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


TARGETS = (
    {"provider": "openai", "model": "gpt-4o", "weight": 3},
    {"provider": "gemini", "model": "gemini-2.5-flash", "weight": 1},
)


def _access(**over) -> gw.AccessPolicy:
    base = {"id": str(uuid.uuid4()), "name": "a", "priority": 100}
    base.update(over)
    return gw.AccessPolicy(**base)


class TestTargets:
    def test_pick_target_is_stable_per_correlation_id_and_proportional_over_many(self):
        first = gw.pick_target(TARGETS, "c1")
        assert gw.pick_target(TARGETS, "c1") == first
        counts = Counter(gw.pick_target(TARGETS, f"id-{i}")["model"] for i in range(2000))
        assert 0.68 < counts["gpt-4o"] / 2000 < 0.82

    def test_a_policy_with_targets_sends_each_call_to_one_of_them(self, gateway_on):
        with _with([_policy(targets=TARGETS)]):
            decisions = [asyncio.run(decide(_req(correlation_id=f"c-{i}"))) for i in range(40)]
            again = asyncio.run(decide(_req(correlation_id="c-0")))
        assert all(d.applied for d in decisions)
        assert {(d.provider, d.model) for d in decisions} == {("openai", "gpt-4o"), ("gemini", "gemini-2.5-flash")}
        assert (again.provider, again.model) == (decisions[0].provider, decisions[0].model)

    def test_targets_are_validated_against_the_catalogue_and_the_fence(self):
        clean = gw.validate_policy_fields(
            {
                "name": "split",
                "targets": [
                    {"provider": "OpenAI", "model": "gpt-4o", "weight": 3},
                    {"provider": "gemini", "model": "gemini-2.5-flash"},
                ],
            }
        )
        assert clean["targets"] == [
            {"provider": "openai", "model": "gpt-4o", "weight": 3},
            {"provider": "gemini", "model": "gemini-2.5-flash", "weight": 1},
        ]
        for bad in (
            [],
            "x",
            ["x"],
            [{"provider": "openai", "model": "gpt-4o", "weight": 0}],
            [{"provider": "openai", "model": "nope"}],
        ):
            with pytest.raises(ValueError):
                gw.validate_policy_fields({"name": "s", "targets": bad})
        with pytest.raises(ValueError, match="no single provider"):
            gw.validate_policy_fields({"name": "s", "targets": list(TARGETS), "tier": "tier1"})
        with pytest.raises(ValueError, match="allowed_providers"):
            gw.validate_policy_fields({"name": "s", "targets": list(TARGETS), "allowed_providers": ["openai"]})
        policy = _policy(targets=TARGETS, allowed_providers=("gemini", "openai"))
        assert Policy.from_dict(policy.to_dict()) == policy


class TestAccess:
    def test_a_call_no_access_policy_matches_is_allowed(self, gateway_on):
        with _with([], access=[_access(application="other-app", effect="deny")]):
            decision = asyncio.run(decide(_req()))
        assert decision.gated and decision.access_policy_id is None and decision.model == "gemini-2.5-flash"

    def test_a_deny_refuses_with_the_access_kind(self, gateway_on):
        deny = _access(name="no-flash", model="gemini-2.5-flash", effect="deny")
        with _with([], access=[deny]):
            with pytest.raises(ModelGatewayRefused) as info:
                asyncio.run(decide(_req(application="advisory-app")))
        assert info.value.kind == "access" and info.value.code == gw.ERROR_CODE
        assert info.value.policy_name == "no-flash" and "denies advisory-app" in info.value.reason
        assert info.value.to_error()["model_gateway"]["kind"] == "access"

    def test_the_first_match_in_priority_order_decides(self, gateway_on):
        allow = _access(name="advisory-ok", priority=10, application="advisory-app", model="gemini-2.5-flash")
        deny = _access(name="flash-denied", priority=20, model="gemini-2.5-flash", effect="deny")
        with _with([], access=[deny, allow]):
            allowed = asyncio.run(decide(_req(application="Advisory-App")))
            with pytest.raises(ModelGatewayRefused, match="flash-denied"):
                asyncio.run(decide(_req(application="other-app")))
        assert allowed.access_policy_name == "advisory-ok"

    def test_an_allow_fences_the_providers_and_models(self, gateway_on):
        with _with([], access=[_access(application="app", allowed_models=("gpt-4o",))]):
            with pytest.raises(ModelGatewayRefused, match="outside the models"):
                asyncio.run(decide(_req(application="app")))
        with _with([], access=[_access(application="app", allowed_providers=("openai",))]):
            with pytest.raises(ModelGatewayRefused, match="outside the providers"):
                asyncio.run(decide(_req(application="app")))
        with _with(
            [], access=[_access(application="app", allowed_providers=("gemini",), allowed_models=("gemini-2.5-flash",))]
        ):
            assert asyncio.run(decide(_req(application="app"))).access_policy_id is not None

    def test_the_access_policy_sees_the_routed_model_not_the_requested_one(self, gateway_on):
        with _with([_policy(model="gpt-4o")], access=[_access(model="gpt-4o", effect="deny")]):
            with pytest.raises(ModelGatewayRefused, match="gpt-4o"):
                asyncio.run(decide(_req()))

    def test_the_bound_identity_fills_the_application_and_principal(self, gateway_on):
        from core.governance.caller_identity import CallerIdentity, bind_identity, reset_identity

        deny = _access(principal="user:7", effect="deny")
        token = bind_identity(CallerIdentity(principal="user:7", application="console", auth_mode="legacy"))
        try:
            with _with([], access=[deny]):
                with pytest.raises(ModelGatewayRefused):
                    asyncio.run(decide(_req()))
                # A request that names its caller is not overridden by the bound identity.
                assert asyncio.run(decide(_req(principal="user:8", application="x"))).gated
        finally:
            reset_identity(token)
        with _with([], access=[deny]):
            assert asyncio.run(decide(_req())).access_policy_id is None

    def test_the_runner_entry_point_also_carries_the_identity(self, gateway_on):
        from core.governance.caller_identity import CallerIdentity, bind_identity, reset_identity

        seen: list[RouteRequest] = []

        async def fake_decide(request, policies, correlation_id, **_kw):
            seen.append(request)
            return gw._passthrough(request, correlation_id, "probe")

        token = bind_identity(CallerIdentity(principal="api_key:k1", application="ops", auth_mode="api_key"))
        try:
            with (
                _with([]),
                patch.object(gw, "agent_sensitivity", AsyncMock(return_value=None)),
                patch.object(gw, "_decide", fake_decide),
            ):
                asyncio.run(
                    gw.route_for_agent(
                        TENANT,
                        use_case="agent_run",
                        agent_id="a1",
                        business_unit="finance",
                        requested_provider="gemini",
                        requested_model="gemini-2.5-flash",
                    )
                )
        finally:
            reset_identity(token)
        assert seen[0].application == "ops" and seen[0].principal == "api_key:k1"

    def test_a_dry_run_reports_an_access_refusal_without_metering(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", False)
        monkeypatch.setattr(gw.settings, "env", "test")
        meter = []
        with (
            patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=_rows(False))),
            _with([], access=[_access(effect="deny")]),
            patch.object(gw, "_meter", lambda outcome: meter.append(outcome)),
        ):
            evaluation = asyncio.run(gw.evaluate(_req(application="app")))
        assert evaluation.enabled is False and evaluation.refusal is not None
        assert evaluation.refusal.kind == "access" and meter == []

    def test_access_policy_validation(self):
        clean = gw.validate_access_policy_fields(
            {
                "name": "frontier",
                "application": "Advisory-App",
                "provider": "OpenAI",
                "model": "gpt-4o",
                "effect": "ALLOW",
                "allowed_models": ["gpt-4o", " "],
            }
        )
        assert clean["application"] == "advisory-app" and clean["provider"] == "openai" and clean["effect"] == "allow"
        assert clean["allowed_models"] == ["gpt-4o"] and clean["priority"] == 100 and clean["enabled"] is True
        with pytest.raises(ValueError, match="belongs on an allow"):
            gw.validate_access_policy_fields({"name": "d", "effect": "deny", "allowed_models": ["gpt-4o"]})
        with pytest.raises(ValueError, match="effect must be"):
            gw.validate_access_policy_fields({"name": "d", "effect": "maybe"})
        with pytest.raises(ValueError, match="sensitivity"):
            gw.validate_access_policy_fields({"name": "d", "sensitivity": "secret"})
        with pytest.raises(ValueError, match="at least one"):
            gw.validate_access_policy_fields({"name": "d", "allowed_providers": [""]})
        with pytest.raises(ValueError, match="needs a name"):
            gw.validate_access_policy_fields({"effect": "deny"})
        with pytest.raises(ValueError, match="priority"):
            gw.validate_access_policy_fields({"name": "d", "priority": True})
        with pytest.raises(ValueError):
            gw.validate_access_policy_fields({"name": "d", "provider": "openai", "model": "gemini-2.5-pro"})

    def test_round_trips(self):
        access = _access(application="app", allowed_providers=("openai",), allowed_models=("gpt-4o",))
        assert gw.AccessPolicy.from_dict(access.to_dict()) == access
        from core.governance.model_gateway_limits import Limit

        policy_set = gw.PolicySet(
            routing=(_policy(targets=TARGETS),),
            access=(access,),
            limits=(Limit(id="l1", provider="openai", model="gpt-4o", max_concurrency=2),),
        )
        assert gw.PolicySet.from_json(policy_set.to_json()) == policy_set


class TestAdmission:
    def _decision(self, **over) -> RouteDecision:
        base = {
            "provider": "openai",
            "model": "gpt-4o",
            "correlation_id": "c9",
            "reason": "p",
            "applied": True,
            "gated": True,
            "tenant_id": str(TENANT),
            "use_case": "agent_run",
        }
        base.update(over)
        return RouteDecision(**base)

    def test_a_pass_through_decision_is_not_admitted_and_reads_nothing(self):
        with patch.object(gw, "active_limits", AsyncMock()) as reads:
            assert asyncio.run(gw.admit(self._decision(gated=False))) is None
            assert asyncio.run(gw.admit(self._decision(tenant_id=None))) is None
        reads.assert_not_called()

    def test_admission_applies_the_tenants_limits_and_returns_the_lease(self):
        from core.governance.model_gateway_limits import Admission, Lease, Limit

        limit = Limit(id="l1", provider="openai", model="gpt-4o", max_concurrency=2)
        lease = Lease(lease_id="c9", keys=("k",))
        with (
            patch.object(gw, "active_limits", AsyncMock(return_value=[limit])),
            patch.object(gw, "_admit_limits", AsyncMock(return_value=Admission(lease=lease))) as admit,
        ):
            assert asyncio.run(gw.admit(self._decision())) is lease
        assert admit.await_args.args == (str(TENANT), "openai", "gpt-4o", [limit])
        assert admit.await_args.kwargs == {"correlation_id": "c9"}

    def test_a_rejected_admission_refuses_with_the_limit_code_and_the_wait(self):
        from core.governance.model_gateway_limits import Admission, Lease, Limit, LimitRejected

        limit = Limit(id="l1", provider="openai", model="gpt-4o", max_concurrency=2, requests_per_minute=60)
        for kind, wait, text in (("concurrency", 1.0, "2 calls in flight"), ("rate", 12.5, "60 calls per minute")):
            admission = Admission(
                lease=Lease(lease_id="c9", outcome="rejected"),
                rejected=LimitRejected(limit=limit, kind=kind, retry_after_seconds=wait, in_flight=2),
            )
            with (
                patch.object(gw, "active_limits", AsyncMock(return_value=[limit])),
                patch.object(gw, "_admit_limits", AsyncMock(return_value=admission)),
            ):
                with pytest.raises(ModelGatewayRefused) as info:
                    asyncio.run(gw.admit(self._decision()))
            assert info.value.kind == "limit" and info.value.code == gw.LIMIT_ERROR_CODE == "E1015"
            assert info.value.retry_after_seconds == wait and text in info.value.reason
            error = info.value.to_error()
            assert error["error"]["code"] == "E1015" and error["model_gateway"]["retry_after_seconds"] == wait
            assert gw.refused_run_result(info.value)["error_code"] == "E1015"

    def test_unreadable_limits_admit_with_an_unavailable_lease(self):
        with patch.object(gw, "active_limits", AsyncMock(side_effect=RuntimeError("db down"))):
            lease = asyncio.run(gw.admit(self._decision()))
        assert lease is not None and lease.outcome == "unavailable" and not lease.held

    def test_release_delegates_to_the_limit_store(self):
        from core.governance.model_gateway_limits import Lease

        lease = Lease(lease_id="c9", keys=("k",))
        with patch.object(gw, "_release_lease", AsyncMock()) as release:
            asyncio.run(gw.release(lease))
            asyncio.run(gw.release(None))
        assert [call.args[0] for call in release.await_args_list] == [lease, None]

    def test_the_runner_binds_the_route_for_the_run_and_the_reasoning_node_admits_each_turn(self):
        src = (ROOT / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        run = src[src.index("async def run_agent(") : src.index("async def resume_agent(")]
        resume = src[src.index("async def resume_agent(") :]
        for body in (run, resume):
            assert body.index("route_for_agent(") < body.index("graph.compile(") < body.index("bind_route(")
            assert body.index("bind_route(") < body.index("t0 = time.perf_counter()")
            assert body.count("reset_route(route_token)") == 1
            tail = body[body.index("reset_route(route_token)") - 40 : body.index("reset_route(route_token)")]
            assert "finally:" in tail
            assert "except ModelGatewayRefused as exc:" in body[body.index("t0 = time.perf_counter()") :]
        graph = (ROOT / "core" / "langgraph" / "agent_graph.py").read_text(encoding="utf-8")
        reason = graph[graph.index("async def reason(") : graph.index("async def evaluate(")]
        assert reason.index("gateway_admit(route.decision)") < reason.index("llm.ainvoke(messages)")
        assert "finally:" in reason and "await gateway_release(lease)" in reason

    def _resume(self, invoke_outcome):
        from unittest.mock import MagicMock

        from auth.grant_enforcement import EnforcementMode
        from auth.run_grants import RunGrant
        from core.langgraph import runner

        seen: dict[str, object] = {}

        class _Compiled:
            async def ainvoke(self, _command, config=None):
                seen["route"] = gw.current_route()
                if isinstance(invoke_outcome, Exception):
                    raise invoke_outcome
                return {"status": "completed", "messages": []}

        graph = MagicMock()
        graph.compile.return_value = _Compiled()
        decision = self._decision(use_case="agent_resume")
        with (
            patch.object(runner, "build_agent_graph", MagicMock(return_value=graph)),
            patch.object(runner, "prefetch_llm_credential", AsyncMock(return_value=None)),
            patch.object(runner, "route_for_agent", AsyncMock(return_value=decision)),
        ):
            result = asyncio.run(
                runner.resume_agent(
                    agent_id="a1",
                    thread_id=runner._run_thread_id(str(TENANT), "t-1", "a1"),
                    decision={"action": "approve"},
                    system_prompt="x",
                    authorized_tools=[],
                    tenant_id=str(TENANT),
                    run_grant=RunGrant(mode=EnforcementMode.OFF, token="", source="minted"),
                )
            )
        return result, seen, decision

    def test_the_resume_path_binds_the_route_while_the_graph_runs_and_clears_it_after(self):
        result, seen, decision = self._resume(None)
        assert result["status"] == "completed"
        route = seen["route"]
        assert route is not None and route.decision is decision
        assert route.use_case == "agent_resume" and route.agent_id == "a1"
        assert gw.current_route() is None

    def test_the_resume_path_returns_the_refused_result_when_a_turn_is_refused(self):
        refusal = ModelGatewayRefused("at its limit", correlation_id="c9", kind="limit", retry_after_seconds=2.0)
        result, _seen, _decision = self._resume(refusal)
        assert result["status"] == "model_gateway_refused" and result["error_code"] == "E1015"
        assert result["model_gateway"]["retry_after_seconds"] == 2.0
        assert gw.current_route() is None

    def test_the_router_admits_after_the_decision_and_releases_after_the_call_and_its_fallback(self):
        from core.governance.model_gateway_limits import Lease
        from core.llm.router import LLMResponse, LLMRouter

        router = LLMRouter()
        router.primary_model = "gemini-2.5-flash"
        router.fallback_model = "gemini-2.5-flash"
        decision = self._decision(provider="gemini", model="gemini-2.5-pro", use_case="completion")
        lease = Lease(lease_id="c9", keys=("k",))
        called: list[str] = []
        response = LLMResponse(content="ok", model="gemini-2.5-flash", tokens_used=1, cost_usd=0.0, latency_ms=1)

        async def fake_call(model, _messages, _temperature, _max_tokens, **_scope):
            called.append(model)
            if len(called) == 1:
                raise TimeoutError("transient")
            return response

        with (
            patch("core.llm.router.gateway_decide", AsyncMock(return_value=decision)),
            patch("core.llm.router.gateway_admit", AsyncMock(return_value=lease)) as admit,
            patch("core.llm.router.gateway_release", AsyncMock()) as release,
            patch.object(router, "_call_model", fake_call),
        ):
            result = asyncio.run(router.complete([{"role": "user", "content": "hi"}], tenant_id=str(TENANT)))
        assert result.content == "ok" and called == ["gemini-2.5-pro", "gemini-2.5-flash"]
        assert admit.await_args.args[0] is decision
        release.assert_awaited_once_with(lease)

    def test_the_router_refusal_at_admission_calls_no_model(self):
        from core.llm.router import LLMRouter

        router = LLMRouter()
        decision = self._decision(provider="gemini", model="gemini-2.5-flash", use_case="completion")
        refusal = ModelGatewayRefused("at its limit", correlation_id="c9", kind="limit", retry_after_seconds=1.0)
        with (
            patch("core.llm.router.gateway_decide", AsyncMock(return_value=decision)),
            patch("core.llm.router.gateway_admit", AsyncMock(side_effect=refusal)),
            patch("core.llm.router.gateway_release", AsyncMock()) as release,
            patch.object(router, "_call_model", AsyncMock()) as call,
        ):
            with pytest.raises(ModelGatewayRefused, match="at its limit"):
                asyncio.run(router.complete([{"role": "user", "content": "hi"}], tenant_id=str(TENANT)))
        call.assert_not_called()
        release.assert_not_called()


class TestAccessAndLimitsMigration:
    def test_revision_chain_rls_and_the_targets_column(self):
        import importlib.util

        path = ROOT / "migrations" / "versions" / "v6_z35_model_access_limits.py"
        spec = importlib.util.spec_from_file_location("v6_z35_model_access_limits", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "v6z35_model_access_limits" and len(module.revision) <= 32
        assert module.down_revision == "v6z34_model_routing_policies"
        src = path.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS targets JSONB" in src
        for table in ("model_access_policies", "model_limits"):
            assert f"CREATE TABLE IF NOT EXISTS {table}" in src
            assert f'_rls("{table}")' in src
        assert "{table}_tenant_isolation" in src and "ENABLE ROW LEVEL SECURITY" in src
        assert "FORCE ROW LEVEL SECURITY" in src and "WITH CHECK" in src
        assert "COALESCE(model, '')" in src

    def test_the_limit_error_code_is_registered_and_retryable(self):
        from core.schemas.errors import ERROR_META, ErrorCode

        assert ErrorCode.MODEL_GATEWAY_LIMIT.value == "E1015"
        entry = ERROR_META["E1015"]
        assert entry["name"] == "MODEL_GATEWAY_LIMIT" and entry["retryable"] is True
        assert ERROR_META["E1014"]["retryable"] is False


class TestRouterRecords:
    def _router(self):
        from core.llm.router import LLMRouter

        router = LLMRouter()
        router.primary_model = "gemini-2.5-flash"
        router.fallback_model = "gemini-2.5-flash"
        return router

    def _decision(self) -> RouteDecision:
        return RouteDecision(
            provider="gemini",
            model="gemini-2.5-pro",
            correlation_id="c9",
            reason="p",
            applied=True,
            gated=True,
            tenant_id=str(TENANT),
            use_case="completion",
        )

    def test_a_completed_call_is_recorded_with_its_response_and_admission_wait(self):
        from core.llm.router import LLMResponse

        router = self._router()
        response = LLMResponse(content="ok", model="gemini-2.5-pro", tokens_used=42, cost_usd=0.002, latency_ms=350)
        with (
            patch("core.llm.router.gateway_decide", AsyncMock(return_value=self._decision())),
            patch("core.llm.router.gateway_admit", AsyncMock(return_value=None)),
            patch("core.llm.router.gateway_release", AsyncMock()),
            patch("core.llm.router.record_model_call", AsyncMock()) as record,
            patch.object(router, "_call_model", AsyncMock(return_value=response)),
        ):
            asyncio.run(router.complete([{"role": "user", "content": "hi"}], tenant_id=str(TENANT)))
        record.assert_awaited_once()
        kwargs = record.await_args.kwargs
        assert record.await_args.args[0].model == "gemini-2.5-pro"
        assert (
            kwargs["model"] == "gemini-2.5-pro" and kwargs["provider"] == "gemini" and kwargs["outcome"] == "completed"
        )
        assert kwargs["tokens"] == 42 and kwargs["cost_usd"] == 0.002 and kwargs["latency_ms"] == 350
        assert isinstance(kwargs["admission_wait_ms"], int) and kwargs["use_case"] == "completion"
        assert kwargs["fallback_from"] is None and kwargs["error_type"] is None

    def test_a_failed_primary_and_its_fallback_are_both_recorded(self):
        from core.llm.router import LLMResponse

        router = self._router()
        response = LLMResponse(content="ok", model="gemini-2.5-flash", tokens_used=1, cost_usd=0.0, latency_ms=1)
        calls: list[str] = []

        async def fake_call(model, _messages, _temperature, _max_tokens, **_scope):
            calls.append(model)
            if len(calls) == 1:
                raise TimeoutError("transient")
            return response

        with (
            patch("core.llm.router.gateway_decide", AsyncMock(return_value=self._decision())),
            patch("core.llm.router.gateway_admit", AsyncMock(return_value=None)),
            patch("core.llm.router.gateway_release", AsyncMock()),
            patch("core.llm.router.record_model_call", AsyncMock()) as record,
            patch.object(router, "_call_model", fake_call),
        ):
            asyncio.run(router.complete([{"role": "user", "content": "hi"}], tenant_id=str(TENANT)))
        outcomes = [
            (c.kwargs["model"], c.kwargs["outcome"], c.kwargs["error_type"], c.kwargs["fallback_from"])
            for c in record.await_args_list
        ]
        assert outcomes == [
            ("gemini-2.5-pro", "failed", "TimeoutError", None),
            ("gemini-2.5-flash", "completed", None, "gemini-2.5-pro"),
        ]
        assert record.await_args_list[1].kwargs["admission_wait_ms"] is None

    def test_a_failed_fallback_is_recorded_then_raised(self):
        router = self._router()
        with (
            patch("core.llm.router.gateway_decide", AsyncMock(return_value=self._decision())),
            patch("core.llm.router.gateway_admit", AsyncMock(return_value=None)),
            patch("core.llm.router.gateway_release", AsyncMock()),
            patch("core.llm.router.record_model_call", AsyncMock()) as record,
            patch.object(router, "_call_model", AsyncMock(side_effect=TimeoutError("down"))),
        ):
            with pytest.raises(TimeoutError):
                asyncio.run(router.complete([{"role": "user", "content": "hi"}], tenant_id=str(TENANT)))
        assert [c.kwargs["outcome"] for c in record.await_args_list] == ["failed", "failed"]
        assert record.await_args_list[1].kwargs["fallback_from"] == "gemini-2.5-pro"

    def test_a_call_without_a_tenant_is_still_metered_without_a_decision(self):
        from core.llm.router import LLMResponse

        router = self._router()
        response = LLMResponse(content="ok", model="gemini-2.5-flash", tokens_used=3, cost_usd=0.0, latency_ms=2)
        with (
            patch("core.llm.router.gateway_decide", AsyncMock()) as ask,
            patch("core.llm.router.record_model_call", AsyncMock()) as record,
            patch.object(router, "_call_model", AsyncMock(return_value=response)),
        ):
            asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
        ask.assert_not_called()
        assert record.await_args.args[0] is None and record.await_args.kwargs["tokens"] == 3


class TestRecordsMigration:
    def test_revision_chain_and_rls(self):
        import importlib.util

        path = ROOT / "migrations" / "versions" / "v6_z36_model_gateway_records.py"
        spec = importlib.util.spec_from_file_location("v6_z36_model_gateway_records", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "v6z36_model_gateway_records" and len(module.revision) <= 32
        assert module.down_revision == "v6z35_model_access_limits"
        src = path.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS model_gateway_records" in src
        assert "ALTER TABLE model_gateway_records ENABLE ROW LEVEL SECURITY" in src
        assert "FORCE ROW LEVEL SECURITY" in src and "WITH CHECK" in src
        assert "ix_model_gateway_records_tenant_correlation" in src


CANDIDATES = (
    {"provider": "openai", "model": "gpt-4o", "weight": 1},
    {"provider": "openai", "model": "gpt-4o-mini", "weight": 1},
    {"provider": "gemini", "model": "gemini-2.5-flash", "weight": 1},
)


def _health(rates: dict[tuple[str, str], float] | None = None):
    from core.governance.model_gateway_records import ModelHealth

    return {
        key: ModelHealth(
            key[0],
            key[1],
            calls=100,
            failures=int(rate * 100),
            avg_latency_ms=1.0,
            avg_cost_usd=0.0,
            total_cost_usd=0.0,
        )
        for key, rate in (rates or {}).items()
    }


class TestCostAware:
    def test_the_cheapest_healthy_candidate_wins(self, gateway_on):
        policy = _policy(targets=CANDIDATES, cost_aware=True)
        with (
            _with([policy]),
            patch("core.governance.model_gateway_records.model_health", AsyncMock(return_value=_health())),
        ):
            decision = asyncio.run(decide(_req()))
        # gemini-2.5-flash is the cheapest by list price, then gpt-4o-mini, then gpt-4o.
        assert (decision.provider, decision.model) == ("gemini", "gemini-2.5-flash")
        assert decision.applied and "cost-aware: cheapest healthy of 3" in decision.reason

    def test_a_failing_candidate_is_skipped_at_the_policy_threshold(self, gateway_on):
        policy = _policy(targets=CANDIDATES, cost_aware=True, max_failure_rate=0.02)
        health = _health({("gemini", "gemini-2.5-flash"): 0.1, ("openai", "gpt-4o-mini"): 0.02})
        with (
            _with([policy]),
            patch("core.governance.model_gateway_records.model_health", AsyncMock(return_value=health)),
        ):
            decision = asyncio.run(decide(_req()))
        assert (decision.provider, decision.model) == ("openai", "gpt-4o-mini")

    def test_the_default_threshold_comes_from_the_settings(self, gateway_on, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_max_failure_rate", 0.5)
        policy = _policy(targets=CANDIDATES, cost_aware=True)
        health = _health({("gemini", "gemini-2.5-flash"): 0.4})
        with (
            _with([policy]),
            patch("core.governance.model_gateway_records.model_health", AsyncMock(return_value=health)),
        ):
            assert asyncio.run(decide(_req())).model == "gemini-2.5-flash"

    def test_no_healthy_candidate_gives_the_least_failing_one(self, gateway_on):
        policy = _policy(targets=CANDIDATES, cost_aware=True, max_failure_rate=0.01)
        health = _health(
            {("gemini", "gemini-2.5-flash"): 0.5, ("openai", "gpt-4o-mini"): 0.3, ("openai", "gpt-4o"): 0.2}
        )
        with (
            _with([policy]),
            patch("core.governance.model_gateway_records.model_health", AsyncMock(return_value=health)),
        ):
            decision = asyncio.run(decide(_req()))
        assert decision.model == "gpt-4o" and "least failing" in decision.reason

    def test_unreadable_health_degrades_to_price_alone(self, gateway_on):
        policy = _policy(targets=CANDIDATES, cost_aware=True)
        with (
            _with([policy]),
            patch("core.governance.model_gateway_records.model_health", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            decision = asyncio.run(decide(_req()))
        assert decision.model == "gemini-2.5-flash" and "health unavailable" in decision.reason

    def test_an_unpriced_candidate_ranks_last_and_the_fence_still_applies(self, gateway_on):
        targets = (
            {"provider": "openai_compatible", "model": "in-house", "weight": 1},
            {"provider": "openai", "model": "gpt-4o", "weight": 1},
        )
        policy = _policy(targets=targets, cost_aware=True)
        with (
            _with([policy]),
            patch("core.governance.model_gateway_records.model_health", AsyncMock(return_value={})),
        ):
            assert asyncio.run(decide(_req())).model == "gpt-4o"
        fenced = _policy(targets=targets, cost_aware=True, allowed_providers=("openai", "openai_compatible"))
        with (
            _with([fenced]),
            patch("core.governance.model_gateway_records.model_health", AsyncMock(return_value={})),
        ):
            assert asyncio.run(decide(_req())).model == "gpt-4o"

    def test_the_dry_run_reports_the_cost_aware_choice(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", False)
        monkeypatch.setattr(gw.settings, "env", "test")
        policy = _policy(targets=CANDIDATES, cost_aware=True)
        with (
            patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=_rows(False))),
            _with([policy]),
            patch("core.governance.model_gateway_records.model_health", AsyncMock(return_value={})),
        ):
            evaluation = asyncio.run(gw.evaluate(_req()))
        assert evaluation.decision is not None and "cost-aware" in evaluation.decision.reason

    def test_validation(self):
        clean = gw.validate_policy_fields(
            {"name": "c", "targets": list(CANDIDATES), "cost_aware": True, "max_failure_rate": 0.1}
        )
        assert clean["cost_aware"] is True and clean["max_failure_rate"] == 0.1
        assert gw.validate_policy_fields({"name": "c", "targets": list(CANDIDATES)})["cost_aware"] is False
        with pytest.raises(ValueError, match="needs targets"):
            gw.validate_policy_fields({"name": "c", "tier": "tier1", "cost_aware": True})
        with pytest.raises(ValueError, match="needs targets"):
            gw.validate_policy_fields({"name": "c", "tier": "tier1", "max_failure_rate": 0.1})
        for bad in (-0.1, 1.5, True, "x"):
            with pytest.raises(ValueError):
                gw.validate_policy_fields({"name": "c", "targets": list(CANDIDATES), "max_failure_rate": bad})
        policy = _policy(targets=CANDIDATES, cost_aware=True, max_failure_rate=0.2)
        assert Policy.from_dict(policy.to_dict()) == policy


class TestCostAwareMigration:
    def test_revision_chain_and_columns(self):
        import importlib.util

        path = ROOT / "migrations" / "versions" / "v6_z37_cost_aware_routing.py"
        spec = importlib.util.spec_from_file_location("v6_z37_cost_aware_routing", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "v6z37_cost_aware_routing" and len(module.revision) <= 32
        assert module.down_revision == "v6z36_model_gateway_records"
        src = path.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS cost_aware BOOLEAN NOT NULL DEFAULT false" in src
        assert "ADD COLUMN IF NOT EXISTS max_failure_rate DOUBLE PRECISION" in src
        assert "ck_model_routing_policies_max_failure_rate" in src
