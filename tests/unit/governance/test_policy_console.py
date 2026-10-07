# SPDX-License-Identifier: Apache-2.0
"""The policy console: one shape for every policy, written through the stores' own writers, dry-run across points."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import governance_policies as api
from core.config import settings
from core.governance import policy_console as console
from core.governance.guardrails.schema import GuardrailResult, Outcome, Rule
from core.governance.model_gateway import AccessPolicy, Evaluation, Policy, RouteDecision
from core.governance.model_gateway_limits import Limit

ROOT = Path(__file__).resolve().parents[3]


def _approval(name="Payments", steps=2, workflow=None, agent=None, active=True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        description="two eyes",
        workflow_id=workflow,
        agent_id=agent,
        is_active=active,
        steps=[SimpleNamespace(approver_role=f"role{i}") for i in range(steps)],
    )


class TestEntries:
    def test_every_kind_takes_the_console_shape(self):
        routing = console.entry_of(
            "model_routing",
            Policy(
                id="r1",
                name="finance",
                priority=5,
                use_case="finance",
                model="gpt-4o",
                provider="openai",
                in_region_only=True,
                reason="why",
            ),
        )
        assert routing["effect"] == "route" and routing["scope"] == {"use_case": "finance"}
        assert routing["detail"]["provider"] == "openai" and routing["detail"]["in_region_only"] is True
        assert routing["enforcement_point"].startswith("model call")
        access = console.entry_of(
            "model_access", AccessPolicy(id="a1", name="deny", priority=1, effect="deny", allowed_models=("x",))
        )
        assert access["effect"] == "deny" and access["detail"]["allowed_models"] == ["x"]
        limit = console.entry_of("model_limit", Limit(id="l1", provider="openai", max_concurrency=4))
        assert limit["name"] == "openai (all models)" and limit["effect"] == "limit" and limit["priority"] is None
        rule = console.entry_of(
            "guardrail",
            Rule(id="g1", name="pii", stage="output", detector="sensitive_data", action="mask", threshold=0.7),
        )
        assert rule["effect"] == "mask" and rule["scope"] == {"stage": "output"} and rule["detail"]["threshold"] == 0.7
        approval = console.entry_of("approval", _approval())
        assert approval["effect"] == "require_approval" and approval["detail"]["steps"] == 2 and approval["enabled"]
        with pytest.raises(console.PolicyConsoleError):
            console.entry_of("widget", None)

    def test_off_by_default(self):
        assert settings.governance_policy_console_enabled is False and console.enabled() is False
        assert set(console.ENFORCEMENT) == set(console.KINDS)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.added = []
        self.deleted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        self.statements = [*getattr(self, "statements", []), statement]
        return _Result(self.answers.pop(0) if self.answers else [])

    def add(self, row):
        self.added.append(row)
        if not getattr(row, "id", None):
            row.id = uuid.uuid4()

    async def delete(self, row):
        self.deleted.append(row)

    async def flush(self):
        return None


def _row(model, **fields):
    """A stored row of a store's table, every column present."""
    values = {column.name: None for column in model.__table__.columns}
    values.update(enabled=True, priority=100, in_region_only=False, cost_aware=False, threshold=0.5, options={})
    values.update(fields)
    return SimpleNamespace(**values)


def _store_answers(enabled=True):
    """The rows of the routing, access, limit and guardrail tables, in the order the console reads them."""
    from core.models.guardrail_rule import GuardrailRule
    from core.models.model_access_policy import ModelAccessPolicy
    from core.models.model_limit import ModelLimit
    from core.models.model_routing_policy import ModelRoutingPolicy

    return [
        [
            _row(ModelRoutingPolicy, id="r1", name="finance", priority=5),
            _row(ModelRoutingPolicy, id="r0", name="all", priority=1, enabled=enabled),
        ],
        [_row(ModelAccessPolicy, id="a1", name="deny", priority=1, effect="deny", enabled=enabled)],
        [_row(ModelLimit, id="l1", provider="openai", enabled=enabled)],
        [
            _row(
                GuardrailRule,
                id="g1",
                name="pii",
                stage="output",
                detector="sensitive_data",
                action="block",
                enabled=enabled,
            )
        ],
    ]


@pytest.mark.asyncio
async def test_list_policies_reads_every_store_and_orders_by_kind_priority_name():
    session = _Session(*_store_answers(), [_approval()])
    entries = await console.list_policies(session, uuid.uuid4())
    assert [e["id"] for e in entries][:5] == ["r0", "r1", "a1", "l1", "g1"]
    assert [e["kind"] for e in entries].count("action") == 12 and entries[5]["kind"] == "approval"
    only = await console.list_policies(_Session(_store_answers()[3]), uuid.uuid4(), kind="guardrail")
    assert [e["kind"] for e in only] == ["guardrail"]


@pytest.mark.asyncio
async def test_list_policies_includes_disabled_policies_of_every_store():
    tid = uuid.uuid4()
    session = _Session(*_store_answers(enabled=False), [_approval(active=False)])
    entries = await console.list_policies(session, tid)
    listed = {e["id"]: e["enabled"] for e in entries if e["kind"] != "action"}
    assert listed["r0"] is False and listed["r1"] is True
    assert listed["a1"] is False and listed["l1"] is False and listed["g1"] is False
    assert [e["enabled"] for e in entries if e["kind"] == "approval"] == [False]
    for statement in session.statements[:4]:
        where = str(statement.whereclause)
        assert "tenant_id" in where and "enabled" not in where


class TestWriteAndDelete:
    @pytest.mark.asyncio
    async def test_a_write_goes_through_the_stores_own_writer_with_the_actor(self, monkeypatch):
        from core.governance import model_gateway
        from core.governance.guardrails import engine

        seen = []

        async def _set_policy(tid, *, actor_id, **fields):
            seen.append(("routing", actor_id, fields))
            return Policy(id="new", name=fields["name"], priority=fields.get("priority", 100))

        async def _set_rule(tid, *, actor_id, **fields):
            seen.append(("rule", actor_id, fields))
            return Rule(
                id="g2",
                name=fields["name"],
                stage=fields["stage"],
                detector=fields["detector"],
                action=fields.get("action", "flag"),
            )

        monkeypatch.setattr(model_gateway, "set_policy", _set_policy)
        monkeypatch.setattr(engine, "set_rule", _set_rule)
        tid = uuid.uuid4()
        entry = await console.write_policy(
            _Session(), tid, "model_routing", {"name": "finance", "priority": 3}, actor_id="user:1"
        )
        assert entry["kind"] == "model_routing" and entry["name"] == "finance" and seen[0][1] == "user:1"
        rule = await console.write_policy(
            _Session(),
            tid,
            "guardrail",
            {"name": "pii", "stage": "output", "detector": "sensitive_data"},
            actor_id="user:1",
        )
        assert rule["effect"] == "flag" and seen[1][0] == "rule"
        with pytest.raises(console.PolicyConsoleError) as refused:
            await console.write_policy(_Session(), tid, "action", {}, actor_id="user:1")
        assert refused.value.code == "unknown_kind"
        with pytest.raises(console.PolicyConsoleError) as refused:
            await console.write_policy(_Session(), tid, "guardrail", {}, actor_id="")
        assert refused.value.code == "no_actor" and refused.value.status == 403

        async def _bad(tid, *, actor_id, **fields):
            raise ValueError("stage must be one of input, retrieval, output, action")

        monkeypatch.setattr(engine, "set_rule", _bad)
        with pytest.raises(console.PolicyConsoleError) as refused:
            await console.write_policy(
                _Session(), tid, "guardrail", {"name": "x", "stage": "odd", "detector": "d"}, actor_id="user:1"
            )
        assert refused.value.code == "invalid_policy" and refused.value.status == 422

    @pytest.mark.asyncio
    async def test_an_approval_policy_is_created_with_its_steps_and_refused_twice(self):
        tid = uuid.uuid4()
        session = _Session([])
        entry = await console.write_policy(
            session,
            tid,
            "approval",
            {
                "name": "Payments",
                "description": "two eyes",
                "workflow_id": None,
                "agent_id": None,
                "steps": [
                    {"sequence": 1, "approver_role": "admin"},
                    {"sequence": 2, "approver_role": "cfo", "mode": "parallel"},
                ],
            },
            actor_id="user:1",
        )
        assert entry["effect"] == "require_approval" and entry["detail"]["approver_roles"] == ["admin", "cfo"]
        assert len(session.added) == 3 and session.added[2].mode == "parallel"
        with pytest.raises(console.PolicyConsoleError) as refused:
            await console.write_policy(
                _Session([_approval()]), tid, "approval", {"name": "Payments", "steps": []}, actor_id="user:1"
            )
        assert refused.value.code == "exists" and refused.value.status == 409

    @pytest.mark.asyncio
    async def test_a_delete_goes_through_the_stores_own_deleter(self, monkeypatch):
        from core.governance import model_gateway

        seen = []

        async def _delete(tid, policy_id, *, actor_id):
            seen.append((policy_id, actor_id))
            return True

        monkeypatch.setattr(model_gateway, "delete_access_policy", _delete)
        pid = uuid.uuid4()
        assert await console.delete_policy(_Session(), uuid.uuid4(), "model_access", pid, actor_id="user:1") is True
        assert seen == [(pid, "user:1")]
        row = _approval()
        session = _Session([row])
        assert await console.delete_policy(session, uuid.uuid4(), "approval", row.id, actor_id="user:1") is True
        assert session.deleted == [row]
        assert (
            await console.delete_policy(_Session([]), uuid.uuid4(), "approval", uuid.uuid4(), actor_id="user:1")
            is False
        )
        with pytest.raises(console.PolicyConsoleError):
            await console.delete_policy(_Session(), uuid.uuid4(), "action", pid, actor_id="user:1")


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_a_dry_run_crosses_every_enforcement_point_and_takes_the_strongest_verdict(self, monkeypatch):
        from core.governance import model_gateway
        from core.governance.guardrails import engine

        async def _evaluate(request):
            return Evaluation(
                enabled=True,
                decision=RouteDecision(
                    provider="openai",
                    model="gpt-4o-mini",
                    correlation_id="c",
                    reason="policy finance",
                    applied=True,
                    policy_id="r1",
                    policy_name="finance",
                ),
            )

        async def _guard(stage, text, **kwargs):
            assert kwargs["dry_run"] is True
            return GuardrailResult(
                stage=stage,
                text=text.replace("4111", "****"),
                allowed=True,
                enforced=False,
                correlation_id="c",
                outcomes=[
                    Outcome(
                        rule_id="g1",
                        rule_name="pii",
                        stage=stage,
                        detector="sensitive_data",
                        action="mask",
                        findings=1,
                        score=0.9,
                        kinds=["card"],
                        applied=False,
                    )
                ],
            )

        monkeypatch.setattr(model_gateway, "evaluate", _evaluate)
        monkeypatch.setattr(engine, "evaluate", _guard)
        workflow = uuid.uuid4()
        session = _Session([_approval(workflow=workflow)])
        answer = await console.evaluate(
            session,
            uuid.uuid4(),
            {
                "use_case": "finance",
                "requested_model": "gpt-4o",
                "stage": "output",
                "text": "card 4111",
                "workflow_id": str(workflow),
                "tool": "initiate_payment",
                "domain": "finance",
            },
        )
        assert answer["verdict"] == "flagged"
        assert (
            answer["sections"]["model"]["outcome"] == "routed" and answer["sections"]["model"]["model"] == "gpt-4o-mini"
        )
        assert (
            answer["sections"]["guardrails"]["transformed"]
            and answer["sections"]["guardrails"]["outcomes"][0]["action"] == "mask"
        )
        assert [p["name"] for p in answer["sections"]["approval"]["policies"]] == ["Payments"]
        assert answer["sections"]["action"]["risk"] == "money" and answer["sections"]["action"]["unsafe"]
        assert len(answer["reasons"]) >= 4

    @pytest.mark.asyncio
    async def test_a_refusal_or_a_block_is_the_strongest_verdict_and_nothing_described_is_nothing_run(
        self, monkeypatch
    ):
        from core.governance import model_gateway
        from core.governance.guardrails import engine

        async def _refused(request):
            return Evaluation(
                enabled=True,
                refusal=SimpleNamespace(reason="outside the allowed providers", policy_id="a1", policy_name="fence"),
            )

        async def _block(stage, text, **kwargs):
            return GuardrailResult(
                stage=stage,
                text=text,
                allowed=False,
                enforced=True,
                correlation_id="c",
                outcomes=[
                    Outcome(
                        rule_id="g1",
                        rule_name="toxic",
                        stage=stage,
                        detector="toxicity",
                        action="block",
                        findings=1,
                        score=1.0,
                        kinds=[],
                        applied=False,
                    )
                ],
            )

        monkeypatch.setattr(model_gateway, "evaluate", _refused)
        monkeypatch.setattr(engine, "evaluate", _block)
        blocked = await console.evaluate(
            _Session([]), uuid.uuid4(), {"requested_model": "gpt-4o", "stage": "input", "text": "x"}
        )
        assert blocked["verdict"] == "blocked" and blocked["sections"]["model"]["policy_name"] == "fence"
        assert "guardrail block: toxic" in blocked["reasons"]
        empty = await console.evaluate(_Session([]), uuid.uuid4(), {})
        assert empty == {"verdict": "allowed", "reasons": [], "sections": {}}


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_off_the_endpoints_are_not_found(self):
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await api.list_policies(kind=None, tenant_id=tid)
        assert (
            refused.value.status_code == 404 and refused.value.detail["error"] == "governance_policy_console_disabled"
        )
        with pytest.raises(HTTPException) as refused:
            await api.evaluate_policies(api.PolicyEvaluateIn(), tenant_id=tid)
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_on_a_write_is_validated_by_the_kinds_own_schema(self, monkeypatch):
        monkeypatch.setattr(settings, "governance_policy_console_enabled", True)
        monkeypatch.setattr(api.gateway_api, "_actor", lambda request, caller: "user:1")
        written = []

        async def _write(session, tid, kind, fields, *, actor_id):
            written.append((kind, fields, actor_id))
            return {"kind": kind, "id": "x", "name": fields["name"]}

        monkeypatch.setattr(console, "write_policy", _write)
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session())
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await api.write_policy(
                api.PolicyWriteIn(kind="guardrail", policy={"name": "pii"}), request=None, tenant_id=tid, caller=None
            )
        assert refused.value.status_code == 422 and refused.value.detail["error"] == "invalid_policy"
        with pytest.raises(HTTPException) as refused:
            await api.write_policy(
                api.PolicyWriteIn(kind="action", policy={}), request=None, tenant_id=tid, caller=None
            )
        assert refused.value.detail["error"] == "unknown_kind"
        entry = await api.write_policy(
            api.PolicyWriteIn(
                kind="guardrail", policy={"name": "pii", "stage": "output", "detector": "sensitive_data"}
            ),
            request=None,
            tenant_id=tid,
            caller=None,
        )
        assert (
            entry["name"] == "pii"
            and written[0][0] == "guardrail"
            and written[0][1]["action"] == "flag"
            and written[0][2] == "user:1"
        )

    def test_the_router_is_registered_behind_admin_and_the_policy_scopes(self):
        main = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        assert "governance_policies," in main and "app.include_router(governance_policies.router" in main
        src = (ROOT / "api" / "v1" / "governance_policies.py").read_text(encoding="utf-8")
        assert src.count("dependencies=[require_tenant_admin]") == 4
        assert src.count('scope="governance.policies.sensitive.read"') == 2
        assert src.count('scope="governance.policies.sensitive.write"') == 2


class TestReviewFixes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "step, message",
        [
            ({"sequence": 1, "approver_role": "not_a_role"}, "invalid approver_role"),
            ({"sequence": 2, "approver_role": "cfo", "quorum_required": 3, "quorum_total": 2}, "quorum_required"),
        ],
    )
    async def test_an_approval_policy_with_a_bad_step_is_refused_before_anything_is_written(self, step, message):
        session = _Session([])
        with pytest.raises(console.PolicyConsoleError) as refused:
            await console.write_policy(
                session, uuid.uuid4(), "approval", {"name": "Payments", "steps": [step]}, actor_id="user:1"
            )
        assert refused.value.status == 422 and refused.value.code == "invalid_policy"
        assert message in refused.value.message and session.added == []

    @pytest.mark.asyncio
    async def test_the_model_dry_run_carries_the_callers_application_and_principal(self, monkeypatch):
        from core.governance import model_gateway

        seen = []

        async def _evaluate(request):
            seen.append(request)
            return Evaluation(
                enabled=True,
                refusal=SimpleNamespace(reason="denied for this application", policy_id="a1", policy_name="fence"),
            )

        monkeypatch.setattr(model_gateway, "evaluate", _evaluate)
        body = api.PolicyEvaluateIn(requested_model="gpt-4o", application="billing-app", principal="svc:billing")
        answer = await console.evaluate(_Session(), uuid.uuid4(), body.model_dump())
        assert seen[0].application == "billing-app" and seen[0].principal == "svc:billing"
        assert answer["verdict"] == "blocked"

    @pytest.mark.asyncio
    async def test_the_approval_dry_run_resolves_as_live_execution_does(self):
        workflow, agent = uuid.uuid4(), uuid.uuid4()
        default = _approval(name="default")
        by_agent = _approval(name="Agent review", agent=agent)
        by_workflow = _approval(name="Workflow review", workflow=workflow)

        async def run(rows, subject):
            return await console.evaluate(_Session(rows), uuid.uuid4(), subject)

        fallback = await run([by_agent, default], {"workflow_id": str(uuid.uuid4())})
        assert [p["name"] for p in fallback["sections"]["approval"]["policies"]] == ["default"]
        assert fallback["sections"]["approval"]["resolved_by"] == "default" and fallback["verdict"] == "routed"
        first = await run([by_agent, by_workflow, default], {"workflow_id": str(workflow), "agent_id": str(agent)})
        assert [p["name"] for p in first["sections"]["approval"]["policies"]] == ["Workflow review"]
        assert first["sections"]["approval"]["resolved_by"] == "workflow"
        agent_only = await run([by_agent, default], {"agent_id": str(agent)})
        assert agent_only["sections"]["approval"]["resolved_by"] == "agent"
        scoped_default = _approval(name="default", workflow=uuid.uuid4())
        none = await run([scoped_default], {"agent_id": str(uuid.uuid4())})
        assert none["sections"]["approval"] == {"policies": [], "resolved_by": None} and none["verdict"] == "allowed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool, domain, reason",
        [
            ("no_such_tool", "finance", "action_unknown"),
            ("initiate_payment", "hr", "action_domain_mismatch"),
            ("initiate_payment", None, "context_domain_missing"),
            ("initiate_payment", "nowhere", "context_domain_unknown"),
        ],
    )
    async def test_a_tool_runtime_would_block_is_blocked_in_the_dry_run(self, tool, domain, reason):
        answer = await console.evaluate(_Session(), uuid.uuid4(), {"tool": tool, "domain": domain})
        section = answer["sections"]["action"]
        assert answer["verdict"] == "blocked" and section["blocked"] and section["reason"] == reason
        assert section["unsafe"] is False and section["containment"] == "blocked"
        assert any(reason in r for r in answer["reasons"])

    @pytest.mark.asyncio
    async def test_a_known_tool_in_its_domain_is_classified_not_blocked(self):
        unsafe = await console.evaluate(_Session(), uuid.uuid4(), {"tool": "initiate_payment", "domain": "finance"})
        assert unsafe["verdict"] == "routed" and unsafe["sections"]["action"]["risk"] == "money"
        assert unsafe["sections"]["action"]["blocked"] is False
        read = await console.evaluate(_Session(), uuid.uuid4(), {"tool": "read_record", "domain": "hr"})
        assert read["verdict"] == "allowed" and read["sections"]["action"]["containment"] == "none"
