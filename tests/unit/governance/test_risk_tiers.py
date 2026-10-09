# SPDX-License-Identifier: Apache-2.0
"""Regulatory risk tiers: the controls a tier forces, enforced whatever the separate switches say."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import agent_registry as registry_api
from api.v1 import agents as agents_api
from api.v1 import governance_risk_tiers as api
from core.config import settings
from core.evals import gates as eval_gates
from core.governance import risk_tiers

ROOT = Path(__file__).resolve().parents[3]
OWNER = uuid.uuid4()
ADMIN = uuid.uuid4()


def _agent(**over):
    base = {
        "id": uuid.uuid4(),
        "name": "Collections",
        "status": "shadow",
        "owner_user_id": OWNER,
        "hitl_condition": "confidence < 0.9",
        "config": {"eval_gate": {"dataset_id": str(uuid.uuid4()), "min_pass_rate": 90}},
        "shadow_scored_sample_count": 60,
        "shadow_sample_count": 60,
        "shadow_feedback_count": 60,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _entry(tier, state="published"):
    return SimpleNamespace(risk_tier=tier, state=state)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "governance_risk_tiers_enabled", True)


@pytest.fixture
def seams(monkeypatch):
    async def _evaluate(_session, _tid, agent):
        gate = (agent.config or {}).get("eval_gate")
        return SimpleNamespace(declared=bool(gate), ok=bool(gate) and agent.config.get("gate_ok", True), code="failed")

    monkeypatch.setattr(eval_gates, "evaluate", _evaluate)
    from core.agent_registry import lifecycle

    async def _no_entry(_session, _tid, _agent_id):
        return None

    monkeypatch.setattr(lifecycle, "get_entry", _no_entry)
    from core.prompts import activation

    async def _maker_checker(_tid):
        return settings.prompts_maker_checker

    monkeypatch.setattr(activation, "maker_checker_on", _maker_checker)


class TestPolicy:
    def test_the_tiers_name_their_requirements_and_thresholds(self):
        policy = risk_tiers.policy()
        assert policy["tiers"]["low"]["requirements"] == []
        assert policy["tiers"]["medium"]["requirements"] == ["registry_approval"]
        assert policy["tiers"]["high"]["requirements"] == [
            "registry_approval",
            "eval_gate",
            "human_oversight",
            "shadow_evidence",
        ]
        assert policy["tiers"]["critical"]["requirements"][-1] == "maker_checker"
        assert (
            policy["tiers"]["high"]["min_shadow_samples"] == 50
            and policy["tiers"]["critical"]["min_shadow_samples"] == 200
        )
        assert set(policy["descriptions"]) == set(risk_tiers.DESCRIPTIONS)

    def test_oversight_means_a_condition_that_can_reach_a_person(self):
        assert risk_tiers.is_oversight("confidence < 0.9") and risk_tiers.is_oversight("always")
        assert (
            not risk_tiers.is_oversight("")
            and not risk_tiers.is_oversight(" never ")
            and not risk_tiers.is_oversight(None)
        )

    def test_off_by_default(self):
        assert settings.governance_risk_tiers_enabled is False and risk_tiers.enabled() is False
        assert risk_tiers.tier_of(_entry("odd")) is None and risk_tiers.tier_of(None) is None


class TestAssess:
    @pytest.mark.asyncio
    async def test_a_high_agent_meeting_everything_is_compliant(self, seams):
        assessment = await risk_tiers.assess(None, uuid.uuid4(), _agent(), _entry("high"))
        assert assessment["tier"] == "high" and assessment["compliant"]
        assert set(assessment["requirements"]) == {
            "registry_approval",
            "eval_gate",
            "human_oversight",
            "shadow_evidence",
        }
        assert assessment["requirements"]["shadow_evidence"]["detail"] == "60 of 50 human-reviewed samples"

    @pytest.mark.asyncio
    async def test_each_missing_control_is_named(self, seams, monkeypatch):
        tid = uuid.uuid4()
        no_gate = await risk_tiers.assess(None, tid, _agent(config={}), _entry("high", state="approved"))
        assert (
            not no_gate["requirements"]["eval_gate"]["met"]
            and "no evaluation gate" in no_gate["requirements"]["eval_gate"]["detail"]
        )
        failing = await risk_tiers.assess(
            None, tid, _agent(config={"eval_gate": {"x": 1}, "gate_ok": False}), _entry("high")
        )
        assert failing["requirements"]["eval_gate"] == {"met": False, "detail": "failed"}
        no_human = await risk_tiers.assess(None, tid, _agent(hitl_condition=""), _entry("high"))
        assert no_human["requirements"]["human_oversight"] == {"met": False, "detail": "hitl condition unset"}
        thin = await risk_tiers.assess(
            None,
            tid,
            _agent(shadow_scored_sample_count=10, shadow_sample_count=10, shadow_feedback_count=10),
            _entry("critical"),
        )
        assert thin["requirements"]["shadow_evidence"] == {"met": False, "detail": "10 of 200 human-reviewed samples"}
        assert thin["requirements"]["maker_checker"] == {"met": False, "detail": "maker-checker off"}
        monkeypatch.setattr(settings, "prompts_maker_checker", True)
        strict = await risk_tiers.assess(
            None,
            tid,
            _agent(shadow_scored_sample_count=300, shadow_sample_count=300, shadow_feedback_count=300),
            _entry("critical"),
        )
        assert strict["compliant"]
        draft = await risk_tiers.assess(None, tid, _agent(), _entry("medium", state="review"))
        assert draft["requirements"] == {"registry_approval": {"met": False, "detail": "registry state review"}}
        assert (await risk_tiers.assess(None, tid, _agent(), _entry("low")))["requirements"] == {}
        assert (await risk_tiers.assess(None, tid, _agent(), None))["tier"] is None


class TestShadowEvidence:
    @pytest.mark.asyncio
    async def test_self_scored_runs_are_not_human_evidence(self, seams):
        tid = uuid.uuid4()
        self_scored = _agent(shadow_scored_sample_count=500, shadow_sample_count=500, shadow_feedback_count=0)
        assessment = await risk_tiers.assess(None, tid, self_scored, _entry("high"))
        assert assessment["requirements"]["shadow_evidence"] == {
            "met": False,
            "detail": "0 of 50 human-reviewed samples",
        }
        assert not assessment["compliant"]
        reviewed = _agent(shadow_scored_sample_count=0, shadow_sample_count=0, shadow_feedback_count=50)
        assert (await risk_tiers.assess(None, tid, reviewed, _entry("high")))["requirements"]["shadow_evidence"]["met"]

    def test_only_a_whole_review_count_is_read(self):
        assert risk_tiers.human_reviewed_samples(SimpleNamespace(shadow_feedback_count=7)) == 7
        for odd in (None, True, "50", -3):
            assert risk_tiers.human_reviewed_samples(SimpleNamespace(shadow_feedback_count=odd)) == 0
        assert risk_tiers.human_reviewed_samples(SimpleNamespace()) == 0

    @pytest.mark.asyncio
    async def test_a_high_agent_without_human_reviews_is_not_promoted(self, seams, monkeypatch):
        from core.agent_registry import lifecycle

        async def _entry_of(_session, _tid, _agent_id):
            return _entry("high")

        monkeypatch.setattr(lifecycle, "get_entry", _entry_of)
        monkeypatch.setattr(settings, "governance_risk_tiers_enabled", True)
        with pytest.raises(risk_tiers.TierError) as refused:
            await risk_tiers.check_promotion(None, uuid.uuid4(), _agent(shadow_feedback_count=0))
        assert refused.value.code == "shadow_evidence_required"


class TestActiveTierChange:
    @pytest.mark.asyncio
    async def test_raising_an_active_agent_needs_the_new_tier_controls(self, seams, on):
        tid = uuid.uuid4()
        bare = _agent(status="active", config={}, hitl_condition="never", shadow_feedback_count=0)
        with pytest.raises(risk_tiers.TierError) as refused:
            await risk_tiers.check_active_tier_change(None, tid, bare, _entry("low"), "high")
        assert refused.value.code == "eval_gate_required" and refused.value.tier == "high"
        assert "Pause the agent first" in refused.value.message and refused.value.status == 409
        no_reviews = _agent(status="active", shadow_feedback_count=0)
        with pytest.raises(risk_tiers.TierError) as refused:
            await risk_tiers.check_active_tier_change(None, tid, no_reviews, None, "high")
        # Unset tier, no registry entry: the registry approval of the new tier is checked first.
        assert refused.value.code == "registry_approval_required"
        with pytest.raises(risk_tiers.TierError) as refused:
            await risk_tiers.check_active_tier_change(None, tid, no_reviews, _entry("medium"), "high")
        assert refused.value.code == "shadow_evidence_required"
        ready = await risk_tiers.check_active_tier_change(None, tid, _agent(status="active"), _entry("low"), "high")
        assert ready is not None and ready["tier"] == "high" and ready["compliant"]

    @pytest.mark.asyncio
    async def test_only_an_active_agent_and_a_real_change_are_checked(self, seams, on):
        tid = uuid.uuid4()
        bare = _agent(config={}, hitl_condition="never", shadow_feedback_count=0)
        # A paused or shadow agent is checked when it is resumed or promoted.
        for status in ("shadow", "paused", "draft"):
            bare.status = status
            assert await risk_tiers.check_active_tier_change(None, tid, bare, _entry("low"), "critical") is None
        bare.status = "active"
        assert await risk_tiers.check_active_tier_change(None, tid, bare, _entry("high"), "high") is None
        assert await risk_tiers.check_active_tier_change(None, tid, bare, _entry("high"), None) is None
        # Moving to a tier with no requirements never fails on controls.
        lowered = await risk_tiers.check_active_tier_change(None, tid, bare, _entry("medium", "draft"), "low")
        assert lowered == {"tier": "low", "requirements": {}, "compliant": True}

    @pytest.mark.asyncio
    async def test_off_nothing_is_checked(self, seams):
        bare = _agent(status="active", config={}, hitl_condition="never", shadow_feedback_count=0)
        assert await risk_tiers.check_active_tier_change(None, uuid.uuid4(), bare, _entry("low"), "critical") is None


class _CardSession:
    def __init__(self, agent):
        self.agent = agent

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.agent)


class TestCardEndpoint:
    @pytest.fixture
    def card(self, monkeypatch, seams, on):
        from core.agent_registry import lifecycle

        monkeypatch.setattr(settings, "agent_registry_enabled", True)
        monkeypatch.setattr(registry_api, "require_agent_mutable", lambda _agent, _caller: None)
        monkeypatch.setattr(registry_api, "_effective_caller", lambda _c, _d: SimpleNamespace(is_admin=True))
        saved: list[dict] = []

        async def _entry_of(_session, _tid, _agent_id):
            return _entry("low")

        async def _set(_session, _tid, _agent_id, fields):
            saved.append(fields)
            return _entry(fields.get("risk_tier", "low"))

        async def _card(_session, _tid, agent):
            return {"id": str(agent.id)}

        monkeypatch.setattr(lifecycle, "get_entry", _entry_of)
        monkeypatch.setattr(lifecycle, "set_card_fields", _set)
        monkeypatch.setattr(lifecycle, "card", _card)

        def run(agent, tier):
            from core.schemas.api import AgentCardIn

            monkeypatch.setattr(registry_api, "get_tenant_session", lambda _tid: _CardSession(agent))
            return registry_api.set_agent_card(
                agent.id,
                AgentCardIn(risk_tier=tier),
                tenant_id=str(uuid.uuid4()),
                user_domains=None,
                caller=None,
                user={"agenticorg:user_id": str(ADMIN)},
            )

        return run, saved

    @pytest.mark.asyncio
    async def test_raising_an_active_agent_without_the_controls_is_refused_and_nothing_is_saved(self, card):
        run, saved = card
        with pytest.raises(HTTPException) as refused:
            await run(_agent(status="active", config={}), "high")
        assert refused.value.status_code == 409
        assert refused.value.detail["error"] == "risk_tier" and refused.value.detail["code"] == "eval_gate_required"
        assert saved == []

    @pytest.mark.asyncio
    async def test_a_paused_agent_or_a_compliant_active_one_takes_the_new_tier(self, card):
        run, saved = card
        await run(_agent(status="paused", config={}), "high")
        await run(_agent(status="active"), "high")
        assert saved == [{"risk_tier": "high"}, {"risk_tier": "high"}]


class _ReachedError(Exception):
    pass


class TestFullReplacement:
    @pytest.fixture
    def replace(self, monkeypatch):
        from contextlib import asynccontextmanager

        from core.agent_registry import lifecycle

        agent = _agent(status="active", hitl_condition="confidence < 0.9", visibility="tenant", domain="finance")
        reads: list[uuid.UUID] = []

        async def _entry_of(_session, _tid, agent_id):
            reads.append(agent_id)
            return _entry("high")

        @asynccontextmanager
        async def _session(_tid):
            yield SimpleNamespace(execute=_execute)

        async def _execute(_statement):
            return SimpleNamespace(scalar_one_or_none=lambda: agent)

        def _reached(_agent, _visibility):
            raise _ReachedError

        monkeypatch.setattr(lifecycle, "get_entry", _entry_of)
        monkeypatch.setattr(agents_api, "get_tenant_session", _session)
        monkeypatch.setattr(agents_api, "_effective_caller", lambda _c, _d: SimpleNamespace(is_admin=True))
        monkeypatch.setattr(agents_api, "require_agent_mutable", lambda _agent, _caller: None)
        monkeypatch.setattr(agents_api, "check_agent_domain_change", lambda _agent, _domain, _caller: None)
        monkeypatch.setattr(agents_api, "check_agent_visibility_change", lambda _agent, _vis, _caller: None)
        monkeypatch.setattr(agents_api, "_apply_agent_visibility", _reached)

        def run(**body):
            from core.schemas.api import AgentCreate

            payload = {"name": "Collections", "agent_type": "collections", "domain": "finance", **body}
            return agents_api.replace_agent(
                agent_id=agent.id,
                body=AgentCreate(**payload),
                tenant_id=str(uuid.uuid4()),
                user_domains=None,
                user={},
                caller=None,
            )

        return run, agent, reads

    @pytest.mark.asyncio
    async def test_a_put_cannot_drop_oversight_on_a_regulated_agent(self, replace, on):
        run, agent, _reads = replace
        for condition in ("", "never"):
            with pytest.raises(HTTPException) as refused:
                await run(hitl_policy={"condition": condition})
            assert refused.value.status_code == 409
            assert refused.value.detail["code"] == "human_oversight_required"
            assert agent.hitl_condition == "confidence < 0.9"

    @pytest.mark.asyncio
    async def test_a_put_that_keeps_oversight_or_omits_it_goes_on(self, replace, on):
        run, _agent_row, reads = replace
        with pytest.raises(_ReachedError):
            await run(hitl_policy={"condition": "confidence < 0.5"})
        assert len(reads) == 1
        with pytest.raises(_ReachedError):
            await run()
        assert len(reads) == 1

    @pytest.mark.asyncio
    async def test_off_the_put_is_not_checked(self, replace):
        run, _agent_row, reads = replace
        with pytest.raises(_ReachedError):
            await run(hitl_policy={"condition": ""})
        assert reads == []


class TestChecks:
    @pytest.mark.asyncio
    async def test_promotion_is_refused_on_the_first_unmet_requirement_only_while_on(self, seams, monkeypatch):
        from core.agent_registry import lifecycle

        async def _entry_of(_session, _tid, _agent_id):
            return _entry("high", state="approved")

        monkeypatch.setattr(lifecycle, "get_entry", _entry_of)
        agent = _agent(config={})
        assert await risk_tiers.check_promotion(None, uuid.uuid4(), agent) is None
        monkeypatch.setattr(settings, "governance_risk_tiers_enabled", True)
        with pytest.raises(risk_tiers.TierError) as refused:
            await risk_tiers.check_promotion(None, uuid.uuid4(), agent)
        assert refused.value.code == "eval_gate_required" and refused.value.tier == "high"
        assert refused.value.requirement == "eval_gate" and "no evaluation gate declared" in refused.value.message
        assessment = await risk_tiers.check_promotion(None, uuid.uuid4(), _agent())
        assert assessment is not None and assessment["compliant"]

    def test_tier_changes_are_an_administrators_and_lowering_needs_a_second_person(self, on):
        risk_tiers.check_tier_change(current="low", new="low", is_admin=False, actor=None, owner_user_id=OWNER)
        with pytest.raises(risk_tiers.TierError) as refused:
            risk_tiers.check_tier_change(current="low", new="medium", is_admin=False, actor=OWNER, owner_user_id=OWNER)
        assert refused.value.code == "admin_only" and refused.value.status == 403
        risk_tiers.check_tier_change(current="low", new="critical", is_admin=True, actor=OWNER, owner_user_id=OWNER)
        with pytest.raises(risk_tiers.TierError) as refused:
            risk_tiers.check_tier_change(current="high", new="medium", is_admin=True, actor=OWNER, owner_user_id=OWNER)
        assert refused.value.code == "second_person"
        with pytest.raises(risk_tiers.TierError) as refused:
            risk_tiers.check_tier_change(current="critical", new=None, is_admin=True, actor=None, owner_user_id=OWNER)
        assert refused.value.code == "no_actor"
        risk_tiers.check_tier_change(current="high", new="low", is_admin=True, actor=ADMIN, owner_user_id=OWNER)
        risk_tiers.check_tier_change(current="high", new="critical", is_admin=True, actor=OWNER, owner_user_id=OWNER)

    def test_a_regulated_agent_keeps_oversight_and_its_gate(self, on):
        risk_tiers.check_update(_entry("medium"), {"hitl_policy": {"condition": ""}})
        risk_tiers.check_update(_entry("high"), {"hitl_policy": {"condition": "confidence < 0.5"}})
        risk_tiers.check_update(_entry("high"), {"name": "renamed"})
        with pytest.raises(risk_tiers.TierError) as refused:
            risk_tiers.check_update(_entry("high"), {"hitl_policy": {"condition": "never"}})
        assert refused.value.code == "human_oversight_required"
        with pytest.raises(risk_tiers.TierError):
            risk_tiers.check_update(_entry("critical"), {"hitl_condition": ""})
        risk_tiers.check_gate_removal(_entry("medium"), None)
        risk_tiers.check_gate_removal(_entry("high"), {"dataset_id": "x"})
        with pytest.raises(risk_tiers.TierError) as refused:
            risk_tiers.check_gate_removal(_entry("critical"), None)
        assert refused.value.code == "eval_gate_required" and refused.value.requirement == "eval_gate"

    def test_off_the_change_and_update_checks_are_silent(self):
        risk_tiers.check_tier_change(current="critical", new="low", is_admin=False, actor=None, owner_user_id=OWNER)
        risk_tiers.check_update(_entry("critical"), {"hitl_policy": {"condition": "never"}})
        risk_tiers.check_gate_removal(_entry("critical"), None)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _statement):
        return _Result(self.rows)


@pytest.mark.asyncio
async def test_the_overview_lists_every_agent_with_its_compliance(seams, monkeypatch):
    from core.agent_registry import lifecycle

    tiers = {}
    a, b, c = _agent(name="A"), _agent(name="B", config={}), _agent(name="C")
    tiers[a.id], tiers[b.id] = _entry("high"), _entry("critical")

    async def _entry_of(_session, _tid, agent_id):
        return tiers.get(agent_id)

    monkeypatch.setattr(lifecycle, "get_entry", _entry_of)
    overview = await risk_tiers.overview(_Session([c, b, a]), uuid.uuid4())
    assert [r["name"] for r in overview["agents"]] == ["A", "B", "C"]
    assert overview["agents"][0]["compliant"] and overview["agents"][0]["unmet"] == []
    assert overview["agents"][1]["unmet"] == ["eval_gate", "shadow_evidence", "maker_checker"]
    assert overview["agents"][2]["tier"] is None and overview["agents"][2]["compliant"]
    assert overview["by_tier"] == {"low": 0, "medium": 0, "high": 1, "critical": 1, "unset": 1}
    assert overview["non_compliant"] == 1 and overview["policy"]["tiers"]["high"]["min_shadow_samples"] == 50


class TestEndpointsAndHooks:
    @pytest.mark.asyncio
    async def test_off_the_overview_is_not_found_and_on_it_answers(self, monkeypatch):
        with pytest.raises(HTTPException) as refused:
            await api.risk_tier_overview(tenant_id=str(uuid.uuid4()))
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "governance_risk_tiers_disabled"
        monkeypatch.setattr(settings, "governance_risk_tiers_enabled", True)

        async def _overview(_session, _tid):
            return {"policy": risk_tiers.policy(), "agents": [], "by_tier": {}, "non_compliant": 0}

        monkeypatch.setattr(risk_tiers, "overview", _overview)
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session([]))
        answer = await api.risk_tier_overview(tenant_id=str(uuid.uuid4()))
        assert answer["policy"]["enabled"] is True and answer["non_compliant"] == 0

    def test_a_refusal_names_the_tier_and_the_requirement(self):
        refused = agents_api._tier_refused(
            risk_tiers.TierError("eval_gate_required", "not yet", tier="high", requirement="eval_gate")
        )
        assert refused.status_code == 409
        assert refused.detail == {
            "error": "risk_tier",
            "code": "eval_gate_required",
            "message": "not yet",
            "tier": "high",
            "requirement": "eval_gate",
        }

    def test_the_hooks_sit_at_promotion_resume_update_gate_and_card(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert src.count("await risk_tiers.check_promotion(session, tid, agent)") == 2
        for hook in ("registry_approval.check_promotion(session, tid, agent)",):
            first = src.index(hook)
            assert src.index("risk_tiers.check_promotion", first) > first
        promote = src[src.index('@router.post(\n    "/agents/{agent_id}/promote",') :]
        assert promote.index("risk_tiers.check_promotion(") < promote.index("agent.status = new_status")
        assert "risk_tiers.check_update(" in src and "risk_tiers.check_gate_removal(" in src
        # The oversight guard covers both mutation paths: PATCH and the PUT full replacement.
        assert src.count("risk_tiers.check_update(") == 2
        replace = src[src.index("async def replace_agent(") : src.index("async def update_agent(")]
        assert replace.index("risk_tiers.check_update(") < replace.index("agent.hitl_condition = body.hitl_policy")
        registry = (ROOT / "api" / "v1" / "agent_registry.py").read_text(encoding="utf-8")
        assert "risk_tiers.check_tier_change(" in registry
        card = registry[registry.index("async def set_agent_card(") :]
        assert card.index("risk_tiers.check_active_tier_change(") < card.index("lifecycle.set_card_fields(")
        assert 'if "risk_tier" in fields and risk_tiers.enabled():' in registry
        main = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        assert "app.include_router(governance_risk_tiers.router" in main
        assert registry_api is not None
