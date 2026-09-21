# SPDX-License-Identifier: Apache-2.0
"""PRD A-8 on PostgreSQL: governed case lifecycle, agent runtime through the tool gateway, workflow and API.

Runs the ``v6z25_governed_cases`` migration (idempotent) against ``AGENTICORG_DB_URL`` and drives
cases through ``core.cases`` with the mock provider and a scripted model: investigation to
``awaiting_decision`` with a cited memo, screening dispositions, graceful degradation, grant
refusal, decisions refused without a decision grant, row-level security between tenants, the
``business_onboarding`` workflow on the real engine, and the HTTP API.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import yaml
from langchain_core.messages import AIMessage, BaseMessage
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from connectors.framework.verification_provider import Capability
from connectors.providers.mock import MockConfig, MockProvider
from core.cases.decisions import DecisionCheck
from core.cases.runtime import CaseRuntime, decide_case, dispose_screening_hits, investigate_case, run_case_step
from core.cases.states import CaseError, CaseState
from core.cases.store import counts_by_state, create_case, get_case, transition, transitions_for
from core.domain_schemas import validate
from core.test_doubles.scripted_model import final
from core.tool_gateway.provider_gateway import ToolDecision

_DB_URL = os.getenv("AGENTICORG_DB_URL", "")
_SYNC_URL = _DB_URL.replace("postgresql+asyncpg", "postgresql")
_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "migrations" / "versions" / "v6_z25_governed_cases.py"
_PROBE_ROLE = "governed_case_rls_probe"
FROZEN = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

pytestmark = pytest.mark.skipif(not _DB_URL, reason="integration tests require AGENTICORG_DB_URL")


async def _stored_excerpts(tenant_id: str, case_ref: str) -> list[dict[str, Any]]:
    from core.database import get_tenant_session

    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        case = await get_case(session, tenant_id, case_ref)
        return [dict(entry) for entry in case.excerpts_encrypted or []]


async def _tamper_with_excerpt(tenant_id: str, case_ref: str, excerpt_ref: str) -> None:
    """Rewrite one stored passage, leaving its digest: exactly what an integrity check is for."""
    from core.crypto.tenant_secrets import encrypt_for_tenant
    from core.database import get_tenant_session

    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        case = await get_case(session, tenant_id, case_ref, for_update=True)
        entries = [dict(entry) for entry in case.excerpts_encrypted or []]
        for entry in entries:
            if entry["excerpt_ref"] == excerpt_ref:
                entry["text_encrypted"] = await encrypt_for_tenant(
                    "TAMPERED - not what the provider returned", uuid.UUID(tenant_id)
                )
        case.excerpts_encrypted = entries


def _memo_evidence(memo: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every evidence entry in a memo, with where it was found."""
    found: list[tuple[str, dict[str, Any]]] = []
    for section in memo["sections"]:
        found += [(section["section_id"], item) for item in section["evidence"]]
        for finding in section["findings"]:
            found += [(f"{section['section_id']}:{finding['code']}", item) for item in finding["evidence"]]
    return found


def _respond(messages: list[BaseMessage]) -> AIMessage:
    """A well-behaved model for both reference agents."""
    system = str(messages[0].content)
    context = json.loads(str(messages[-1].content))
    if "rationale for a proposed disposition" in system:
        return final({"rationale": "The identifier comparisons support the proposed outcome.", "confidence": 0.6})
    summaries = [
        {"section_id": s["section_id"], "summary": "The section is summarised from its findings.", "citations": [0]}
        for s in context["sections"]
        if s["status"] in ("complete", "partial") and s["evidence_count"]
    ]
    return final({"summaries": summaries, "confidence": 0.7})


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    import core.models  # noqa: F401 - registers every ORM model
    from core.models.base import BaseModel

    sync_engine = create_engine(_SYNC_URL)
    BaseModel.metadata.create_all(sync_engine)
    spec = importlib.util.spec_from_file_location("v6z25_governed_cases", _MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sync_engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
        migration.upgrade()  # idempotent
    yield sync_engine
    sync_engine.dispose()


@pytest.fixture(autouse=True)
def fresh_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.database as db_mod

    test_engine = create_async_engine(_DB_URL, poolclass=NullPool)
    monkeypatch.setattr(db_mod, "async_session_factory", async_sessionmaker(test_engine, expire_on_commit=False))


@pytest.fixture
def tenants(engine: Engine) -> tuple[str, str]:
    ids = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        for tenant_id in ids:
            conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, plan, data_region, settings, byok_kek_resource) "
                    "VALUES (:id, :name, :slug, 'enterprise', 'IN', '{}'::jsonb, '')"
                ),
                {"id": tenant_id, "name": f"tenant-{tenant_id}", "slug": f"tenant-{tenant_id}"},
            )
    return ids


def _runtime(provider: MockProvider | None = None, **overrides: Any) -> CaseRuntime:
    backend = provider or MockProvider(MockConfig(clock=lambda: FROZEN))

    async def enabled(tenant_id: uuid.UUID) -> bool:
        return True

    values: dict[str, Any] = {
        "provider_factory": lambda name: backend,
        "flag": enabled,
        "clock": lambda: FROZEN,
        "llm_model": "scripted",
        "require_os_isolation": False,
        "push_kick": lambda tenant_id: None,
    }
    values.update(overrides)
    return CaseRuntime(**values)


async def _submit(tenant_id: str, key: str) -> str:
    from core.database import get_tenant_session

    application = MockProvider().fixture(key).application
    policy = "business_onboarding_uk" if key.startswith("gb-") else "business_onboarding_us"
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        case = await create_case(
            session, tenant_id=tenant_id, application=application, purpose="aml.cdd.onboarding",
            provider="mock", policy_id=policy, created_by="user:submitter", now=FROZEN,
        )  # fmt: skip
        return case.case_ref


async def _load(tenant_id: str, case_ref: str) -> Any:
    from core.database import get_tenant_session

    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        case = await get_case(session, tenant_id, case_ref)
        history = await transitions_for(session, case)
        return case, [(t.from_state, t.to_state, t.reason) for t in history]


# --- lifecycle ------------------------------------------------------------------------------------


async def test_case_investigation_runs_through_the_tool_gateway_to_awaiting_decision(
    engine: Engine, tenants: tuple[str, str], scripted_model: Any
) -> None:
    tenant, _ = tenants
    scripted_model([_respond, _respond])
    case_ref = await _submit(tenant, "gb-missing-owner-marlpit")
    runtime = _runtime()

    output = await investigate_case(tenant, case_ref, runtime=runtime, actor="user:analyst-a")
    assert output["state"] == "awaiting_decision" and output["screening_hits"] == 1

    case, history = await _load(tenant, case_ref)
    assert history == [
        (None, "submitted", "case_submitted"),
        ("submitted", "in_progress", "investigation_started"),
        ("in_progress", "awaiting_decision", "memo_ready"),
    ]
    validate("underwriting_memo", case.memo)
    assert "missing_owner" in [f["code"] for s in case.memo["sections"] for f in s["findings"]]
    [record] = case.agent_records
    assert record["agent"] == "business_underwriter" and record["prompt"]["sha256"].startswith("sha256:")
    assert {call["tool"] for call in record["tool_calls"]} >= {
        "resolve_business",
        "verification_result",
        "screen_person",
    }

    disposed = await dispose_screening_hits(tenant, case_ref, runtime=runtime, actor="user:analyst-a")
    assert disposed == {"case_ref": case_ref, "proposed": 1, "failed": [], "outcomes": ["false_positive"]}
    case, _ = await _load(tenant, case_ref)
    [disposition] = case.screening_dispositions
    validate("screening_disposition", disposition)
    assert disposition["review"] is None
    again = await dispose_screening_hits(tenant, case_ref, runtime=runtime, actor="user:analyst-a")
    assert again["proposed"] == 0


async def test_decision_is_refused_without_a_decision_grant_and_recorded_with_one(
    engine: Engine, tenants: tuple[str, str], scripted_model: Any
) -> None:
    tenant, _ = tenants
    scripted_model([_respond])
    case_ref = await _submit(tenant, "us-clean-hollowbrook")
    runtime = _runtime()
    await investigate_case(tenant, case_ref, runtime=runtime, actor="user:analyst-a")

    with pytest.raises(CaseError) as refused:
        await decide_case(tenant, case_ref, runtime=runtime, actor="user:analyst-a", outcome="approve", grants=[])
    assert refused.value.reason == "decision_required" and refused.value.status == 403
    case, _ = await _load(tenant, case_ref)
    assert case.state == "awaiting_decision" and case.decision is None

    class VerifiedGrant:
        async def verify(self, *, tenant_id: str, case: Any, outcome: str, grants: list[str]) -> DecisionCheck:
            return DecisionCheck(allowed=grants == ["grant-1"], approvers=(("user:approver-a", "grant-1"),))

    decided = await decide_case(
        tenant, case_ref, runtime=_runtime(decision_verifier=VerifiedGrant()), actor="user:approver-a",
        outcome="approve", grants=["grant-1"],
    )  # fmt: skip
    assert decided["state"] == "decided"
    case, history = await _load(tenant, case_ref)
    assert history[-1] == ("awaiting_decision", "decided", "decision_approve")
    assert case.decision["approvers"] == [{"approver": "user:approver-a", "decision_grant_id": "grant-1"}]


async def test_capability_degraded_provider_still_reaches_awaiting_decision(
    engine: Engine, tenants: tuple[str, str], scripted_model: Any
) -> None:
    tenant, _ = tenants
    scripted_model([_respond])
    case_ref = await _submit(tenant, "gb-clean-brightwater")
    provider = MockProvider(
        MockConfig(clock=lambda: FROZEN, capabilities=frozenset({Capability.RESOLVE, Capability.VERIFY}))
    )
    output = await investigate_case(tenant, case_ref, runtime=_runtime(provider), actor="workflow:test")
    assert output["state"] == "awaiting_decision"
    case, _ = await _load(tenant, case_ref)
    statuses = {s["section_id"]: s["status"] for s in case.memo["sections"]}
    assert statuses["ownership"] == statuses["screening"] == statuses["web_presence"] == "not_available"


async def test_a_refused_grant_fails_the_case_closed(
    engine: Engine, tenants: tuple[str, str], scripted_model: Any
) -> None:
    tenant, _ = tenants
    scripted_model([])

    class Deny:
        async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
            return ToolDecision(allowed=False, reason="grant_missing")

    case_ref = await _submit(tenant, "gb-clean-brightwater")
    output = await investigate_case(
        tenant, case_ref, runtime=_runtime(authorizer_factory=lambda t, c: Deny()), actor="workflow:test"
    )
    assert output == {"case_ref": case_ref, "state": "failed", "failure_reason": "tool_refused:grant_missing"}
    case, history = await _load(tenant, case_ref)
    assert history[-1] == ("in_progress", "failed", "tool_refused:grant_missing")
    assert case.memo is None and case.agent_records[0]["tool_calls"][0]["outcome"] == "denied"


async def test_lifecycle_refuses_illegal_transitions_and_stale_versions(
    engine: Engine, tenants: tuple[str, str]
) -> None:
    from core.database import get_tenant_session

    tenant, _ = tenants
    case_ref = await _submit(tenant, "gb-clean-brightwater")
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        case = await get_case(session, tenant, case_ref, for_update=True)
        with pytest.raises(CaseError, match="transition_not_allowed"):
            await transition(session, case, CaseState.DECIDED, actor="user:a", reason="x")
        with pytest.raises(CaseError, match="case_version_conflict"):
            await transition(session, case, CaseState.IN_PROGRESS, actor="user:a", reason="x", expected_version=9)
        await transition(session, case, CaseState.WITHDRAWN, actor="user:a", reason="withdrawn")
        with pytest.raises(CaseError, match="transition_not_allowed"):
            await transition(session, case, CaseState.IN_PROGRESS, actor="user:a", reason="x")
        counts = await counts_by_state(session, tenant)
    assert counts["withdrawn"] >= 1 and set(counts) == {s.value for s in CaseState}


async def test_an_invalid_application_is_refused(engine: Engine, tenants: tuple[str, str]) -> None:
    from core.database import get_tenant_session

    tenant, _ = tenants
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        with pytest.raises(CaseError) as refused:
            await create_case(
                session, tenant_id=tenant, application={"legal_name": "Example Ltd"}, purpose="aml.cdd.onboarding",
                provider="mock", policy_id="business_onboarding_uk", created_by="user:a",
            )  # fmt: skip
    assert refused.value.reason == "application_invalid" and refused.value.status == 422


async def test_everything_is_refused_while_the_flag_is_off(engine: Engine, tenants: tuple[str, str]) -> None:
    tenant, _ = tenants
    case_ref = await _submit(tenant, "gb-clean-brightwater")

    async def off(tenant_id: uuid.UUID) -> bool:
        return False

    async def broken(tenant_id: uuid.UUID) -> bool:
        raise ConnectionError("flag store unreachable")

    for flag in (off, broken):
        with pytest.raises(CaseError, match="governed_cases_disabled"):
            await investigate_case(tenant, case_ref, runtime=_runtime(flag=flag), actor="workflow:test")
    case, _ = await _load(tenant, case_ref)
    assert case.state == "submitted"


async def test_row_level_security_isolates_cases_between_tenants(engine: Engine, tenants: tuple[str, str]) -> None:
    tenant_a, tenant_b = tenants
    case_ref = await _submit(tenant_a, "gb-clean-brightwater")
    with pytest.raises(CaseError, match="case_not_found"):
        await _load(tenant_b, case_ref)
    with engine.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {_PROBE_ROLE}"))
        conn.execute(text(f"CREATE ROLE {_PROBE_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_PROBE_ROLE}"))
        conn.execute(text(f"GRANT SELECT, INSERT ON governed_cases, governed_case_transitions TO {_PROBE_ROLE}"))

    queries = {
        "governed_cases": "SELECT count(*) FROM governed_cases WHERE case_ref = :ref",
        "governed_case_transitions": (
            "SELECT count(*) FROM governed_case_transitions t "
            "WHERE t.case_id = (SELECT id FROM governed_cases g WHERE g.case_ref = :ref)"
        ),
    }

    def visible(tenant_id: str | None, table: str) -> int:
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL ROLE {_PROBE_ROLE}"))
            if tenant_id is not None:
                conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": tenant_id})
            return conn.execute(text(queries[table]), {"ref": case_ref}).scalar_one()

    try:
        assert visible(tenant_a, "governed_cases") == 1
        assert visible(tenant_b, "governed_cases") == 0
        assert visible(None, "governed_cases") == 0
        assert visible(tenant_b, "governed_case_transitions") == 0
        with engine.connect() as conn, conn.begin() as transaction:
            conn.execute(text(f"SET LOCAL ROLE {_PROBE_ROLE}"))
            conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": tenant_b})
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(
                    text(
                        "INSERT INTO governed_cases "
                        "(id, tenant_id, case_ref, purpose, state, provider, policy_id, application) "
                        "VALUES (:id, :t, 'case_000000000000000000000000', 'aml.cdd.onboarding', "
                        "'submitted', 'mock', 'p', '{}'::jsonb)"
                    ),
                    {"id": str(uuid.uuid4()), "t": tenant_a},
                )
            transaction.rollback()
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP OWNED BY {_PROBE_ROLE}"))
            conn.execute(text(f"DROP ROLE {_PROBE_ROLE}"))


# --- workflow ---------------------------------------------------------------------------------------


async def test_business_onboarding_workflow_runs_on_the_engine_and_stops_at_the_decision_grant(
    engine: Engine, tenants: tuple[str, str], scripted_model: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workflows.engine import WorkflowEngine
    from workflows.parser import WorkflowParser
    from workflows.state_store import InMemoryWorkflowStateRepository, WorkflowStateStore

    tenant, _ = tenants
    scripted_model([_respond, _respond])
    case_ref = await _submit(tenant, "us-false-positive-oakhollow")
    runtime = _runtime()

    async def step_with_test_runtime(step: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        return await run_case_step(step, state, runtime=runtime)

    monkeypatch.setattr("workflows.step_types._execute_case_agent", step_with_test_runtime)
    definition = WorkflowParser().parse(
        (_ROOT / "workflows" / "examples" / "business_onboarding.yaml").read_text(encoding="utf-8")
    )
    workflow = WorkflowEngine(WorkflowStateStore(repository=InMemoryWorkflowStateRepository()))
    run_id = await workflow.start_run(definition, {"case_ref": case_ref, "decision_grants": []}, tenant_id=tenant)
    await workflow.execute(run_id)
    state = await workflow.state_store.load(run_id)
    assert state["status"] == "waiting_hitl", state.get("error")
    assert state["step_results"]["investigate"]["status"] == "completed"
    assert state["step_results"]["propose_screening_dispositions"]["output"]["outcomes"] == ["false_positive"]

    await workflow.resume_from_hitl(run_id, {"decision": "approve", "notes": "looks fine"})
    state = await workflow.state_store.load(run_id)
    record = state["step_results"]["record_decision"]
    assert record["status"] == "failed" and record["output"]["reason"] == "decision_required"
    case, _ = await _load(tenant, case_ref)
    assert case.state == "awaiting_decision" and case.decision is None


def test_both_example_workflows_parse_and_use_only_case_agent_actions_that_exist() -> None:
    from core.cases.runtime import _ACTIONS
    from workflows.parser import WorkflowParser

    for name in ("business_onboarding.yaml", "screening_disposition.yaml"):
        raw = (_ROOT / "workflows" / "examples" / name).read_text(encoding="utf-8")
        definition = WorkflowParser().parse(raw)
        for step in definition["steps"]:
            if step["type"] == "case_agent":
                assert step["action"] in _ACTIONS, (name, step["id"])
        assert yaml.safe_load(raw)["trigger"]["type"] == "api_event"


# --- API ------------------------------------------------------------------------------------------


async def test_case_api_end_to_end_with_tenant_isolation(
    client: Any, auth_headers: dict[str, str], scripted_model: Any
) -> None:
    from api.main import app
    from api.v1 import governed_cases as routes
    from tests.integration.conftest import TEST_TENANT_ID, _make_jwt

    scripted_model([_respond, _respond])
    runtime = _runtime()
    app.dependency_overrides[routes.get_case_runtime] = lambda: runtime
    try:
        application = MockProvider().fixture("gb-missing-owner-marlpit").application
        created = await client.post("/api/v1/governed-cases", json={"application": application}, headers=auth_headers)
        assert created.status_code == 201, created.text
        case_ref = created.json()["case_ref"]
        assert created.json()["state"] == "submitted"

        started = await client.post(f"/api/v1/governed-cases/{case_ref}/investigate", headers=auth_headers)
        assert started.status_code == 202, started.text
        fetched = await client.get(f"/api/v1/governed-cases/{case_ref}", headers=auth_headers)
        body = fetched.json()
        assert body["case"]["state"] == "awaiting_decision", body
        validate("business_case", body["case"])
        assert [t["to_state"] for t in body["transitions"]] == ["submitted", "in_progress", "awaiting_decision"]
        assert body["transitions"][0]["actor"].startswith("user:")

        tenant_b_headers = {"Authorization": f"Bearer {_make_jwt(tenant_id=str(uuid.uuid4()))}"}
        assert (await client.get(f"/api/v1/governed-cases/{case_ref}", headers=tenant_b_headers)).status_code in (
            401,
            403,
            404,
        )

        decision = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision", json={"outcome": "approve"}, headers=auth_headers
        )
        assert decision.status_code == 403 and decision.json()["error"]["reason"] == "decision_required"

        disposed = await dispose_screening_hits(TEST_TENANT_ID, case_ref, runtime=runtime, actor="user:analyst")
        assert disposed["proposed"] == 1
        detail = (await client.get(f"/api/v1/governed-cases/{case_ref}", headers=auth_headers)).json()
        hit_id = detail["screening_dispositions"][0]["hit_id"]
        forged = await client.post(
            f"/api/v1/governed-cases/{case_ref}/screening-dispositions/{hit_id}/review",
            json={"action": "accepted", "final_outcome": "false_positive", "analyst_id": "user:someone-else"},
            headers=auth_headers,
        )
        assert forged.status_code == 422
        missing_reason = await client.post(
            f"/api/v1/governed-cases/{case_ref}/screening-dispositions/{hit_id}/review",
            json={"action": "overridden", "final_outcome": "true_match"},
            headers=auth_headers,
        )
        assert (
            missing_reason.status_code == 422 and missing_reason.json()["error"]["reason"] == "override_reason_required"
        )
        reviewed = await client.post(
            f"/api/v1/governed-cases/{case_ref}/screening-dispositions/{hit_id}/review",
            json={
                "action": "overridden",
                "final_outcome": "insufficient_information",
                "reason": "Awaiting a passport copy.",
            },
            headers=auth_headers,
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["review"]["analyst_id"].startswith("user:")
        again = await client.post(
            f"/api/v1/governed-cases/{case_ref}/screening-dispositions/{hit_id}/review",
            json={"action": "accepted", "final_outcome": "false_positive"},
            headers=auth_headers,
        )
        assert again.status_code == 409

        stats = await client.get("/api/v1/governed-cases/stats", headers=auth_headers)
        assert stats.json()["cases_by_state"]["awaiting_decision"] >= 1
        listed = await client.get("/api/v1/governed-cases?state=awaiting_decision", headers=auth_headers)
        assert case_ref in [c["case_ref"] for c in listed.json()["cases"]]
        record = await client.get(f"/api/v1/governed-cases/{case_ref}/case-record", headers=auth_headers)
        assert [r["agent"] for r in record.json()["agent_records"]] == ["business_underwriter", "screening_disposition"]
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)


async def test_case_api_is_hidden_while_the_flag_is_off(client: Any, auth_headers: dict[str, str]) -> None:
    from api.main import app
    from api.v1 import governed_cases as routes

    async def off(tenant_id: uuid.UUID) -> bool:
        return False

    app.dependency_overrides[routes.get_case_runtime] = lambda: _runtime(flag=off)
    try:
        response = await client.get("/api/v1/governed-cases", headers=auth_headers)
        assert response.status_code == 404 and response.json()["error"]["reason"] == "governed_cases_disabled"
        created = await client.post("/api/v1/governed-cases", json={"application": {}}, headers=auth_headers)
        assert created.status_code == 404
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)


async def test_case_api_refusals_withdrawal_and_information_requests(
    client: Any, auth_headers: dict[str, str], scripted_model: Any
) -> None:
    from api.main import app
    from api.v1 import governed_cases as routes

    scripted_model([_respond, _respond])
    runtime = _runtime()
    app.dependency_overrides[routes.get_case_runtime] = lambda: runtime
    base = "/api/v1/governed-cases"
    try:
        fixture = MockProvider().fixture("us-missing-owner-cinderpath").application
        unsupported = await client.post(
            base, json={"application": {**fixture, "jurisdiction": "FR"}}, headers=auth_headers
        )
        assert unsupported.status_code == 422 and unsupported.json()["error"]["reason"] == "policy_not_configured"
        invalid = await client.post(
            base, json={"application": {"legal_name": "Example Ltd", "jurisdiction": "GB"}}, headers=auth_headers
        )
        assert invalid.status_code == 422 and invalid.json()["error"]["reason"] == "application_invalid"
        assert (await client.get(f"{base}?state=approved", headers=auth_headers)).status_code == 422
        assert (await client.get(f"{base}/case_{'f' * 24}", headers=auth_headers)).status_code == 404
        assert (await client.get(f"{base}/not-a-case-ref/case-record", headers=auth_headers)).status_code == 404

        created = (await client.post(base, json={"application": fixture}, headers=auth_headers)).json()
        case_ref = created["case_ref"]
        early = await client.post(
            f"{base}/{case_ref}/information-requests",
            json={"template_id": "onboarding_missing_items", "template_version": "1.0.0"},
            headers=auth_headers,
        )
        assert early.status_code == 409
        assert (await client.post(f"{base}/{case_ref}/investigate", headers=auth_headers)).status_code == 202
        again = await client.post(f"{base}/{case_ref}/investigate", headers=auth_headers)
        assert again.status_code == 202  # re-investigation from awaiting_decision is allowed
        detail = (await client.get(f"{base}/{case_ref}", headers=auth_headers)).json()
        assert detail["memo"]["recommendation"]["proposed"] == "request_information"
        assert (
            await client.post(
                f"{base}/{case_ref}/screening-dispositions/hit-unknown/review",
                json={"action": "accepted", "final_outcome": "false_positive"},
                headers=auth_headers,
            )
        ).status_code == 404

        wrong_template = await client.post(
            f"{base}/{case_ref}/information-requests",
            json={"template_id": "free_text_letter", "template_version": "1.0.0"},
            headers=auth_headers,
        )
        assert wrong_template.status_code == 422 and wrong_template.json()["error"]["reason"] == "template_not_approved"
        proposed = await client.post(
            f"{base}/{case_ref}/information-requests",
            json={"template_id": "onboarding_missing_items", "template_version": "1.0.0"},
            headers=auth_headers,
        )
        assert proposed.status_code == 201, proposed.text
        digest = proposed.json()["proposal_sha256"]
        assert (
            await client.post(f"{base}/{case_ref}/information-requests/sha256:{'0' * 64}/approve", headers=auth_headers)
        ).status_code == 404
        approved = await client.post(f"{base}/{case_ref}/information-requests/{digest}/approve", headers=auth_headers)
        assert approved.status_code == 200, approved.text
        assert approved.json()["request"]["approved_by"].startswith("user:")
        twice = await client.post(f"{base}/{case_ref}/information-requests/{digest}/approve", headers=auth_headers)
        assert twice.status_code == 409 and twice.json()["error"]["reason"] == "information_request_not_pending"

        withdrawn = await client.post(f"{base}/{case_ref}/withdraw", headers=auth_headers)
        assert withdrawn.status_code == 200 and withdrawn.json()["state"] == "withdrawn"
        assert (await client.post(f"{base}/{case_ref}/withdraw", headers=auth_headers)).status_code == 409
        assert (await client.post(f"{base}/{case_ref}/investigate", headers=auth_headers)).status_code == 409
        refused = await client.post(f"{base}/{case_ref}/decision", json={"outcome": "approve"}, headers=auth_headers)
        assert refused.status_code == 409
        record = (await client.get(f"{base}/{case_ref}/case-record", headers=auth_headers)).json()
        assert record["decision"] is None and len(record["agent_records"]) == 2
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)


async def test_reviews_and_approvals_need_the_case_awaiting_decision(
    client: Any, auth_headers: dict[str, str], scripted_model: Any
) -> None:
    """A disposition review and an information-request approval are refused outside the window.

    Both write onto documents produced by the investigation, so they must not land on a case that
    is being investigated, has been withdrawn or has already been decided.
    """
    from api.main import app
    from api.v1 import governed_cases as routes

    scripted_model([_respond])
    app.dependency_overrides[routes.get_case_runtime] = lambda: _runtime()
    base = "/api/v1/governed-cases"
    try:
        fixture = MockProvider().fixture("us-missing-owner-cinderpath").application
        case_ref = (await client.post(base, json={"application": fixture}, headers=auth_headers)).json()["case_ref"]
        # Still `submitted`: the state is checked before the disposition is even looked up.
        early_review = await client.post(
            f"{base}/{case_ref}/screening-dispositions/hit-unknown/review",
            json={"action": "accepted", "final_outcome": "false_positive"},
            headers=auth_headers,
        )
        assert early_review.status_code == 409
        assert early_review.json()["error"]["reason"] == "transition_not_allowed"

        assert (await client.post(f"{base}/{case_ref}/investigate", headers=auth_headers)).status_code == 202
        proposed = await client.post(
            f"{base}/{case_ref}/information-requests",
            json={"template_id": "onboarding_missing_items", "template_version": "1.0.0"},
            headers=auth_headers,
        )
        assert proposed.status_code == 201, proposed.text
        digest = proposed.json()["proposal_sha256"]
        assert (await client.post(f"{base}/{case_ref}/withdraw", headers=auth_headers)).status_code == 200

        # Withdrawn: the pending proposal can no longer be approved, so no text is ever rendered
        # from a memo the case has moved past.
        late_approval = await client.post(
            f"{base}/{case_ref}/information-requests/{digest}/approve", headers=auth_headers
        )
        assert late_approval.status_code == 409
        assert late_approval.json()["error"]["reason"] == "transition_not_allowed"
        late_review = await client.post(
            f"{base}/{case_ref}/screening-dispositions/hit-unknown/review",
            json={"action": "accepted", "final_outcome": "false_positive"},
            headers=auth_headers,
        )
        assert late_review.status_code == 409 and late_review.json()["error"]["reason"] == "transition_not_allowed"
        detail = (await client.get(f"{base}/{case_ref}", headers=auth_headers)).json()
        assert detail["information_requests"][0]["status"] == "awaiting_approval"
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)


async def test_the_case_holds_the_passage_behind_every_citation_and_serves_it(
    client: Any, auth_headers: dict[str, str], scripted_model: Any
) -> None:
    """PRD A-6: a reviewer can read what a citation points at, not only which record it named."""
    from api.main import app
    from api.v1 import governed_cases as routes
    from tests.integration.conftest import TEST_TENANT_ID

    scripted_model([_respond])
    app.dependency_overrides[routes.get_case_runtime] = lambda: _runtime()
    base = "/api/v1/governed-cases"
    try:
        application = MockProvider().fixture("us-hostile-web-glintmoor").application
        case_ref = (await client.post(base, json={"application": application}, headers=auth_headers)).json()["case_ref"]
        assert (await client.post(f"{base}/{case_ref}/investigate", headers=auth_headers)).status_code == 202

        detail = (await client.get(f"{base}/{case_ref}", headers=auth_headers)).json()
        memo = detail["memo"]
        cited = {e["excerpt_ref"] for _, e in _memo_evidence(memo) if e.get("excerpt_ref")}
        assert cited, "the mock provider cites excerpts on this case"
        attached = {excerpt["excerpt_ref"] for excerpt in memo["excerpts"]}
        assert cited <= attached, sorted(cited - attached)

        held = {excerpt["excerpt_ref"] for excerpt in detail["excerpts"]}
        # Every reference the memo attaches has a passage, whichever produced it: the provider's
        # records and the sandboxed extractor's own passages, which used to be attached and lost.
        assert attached <= held, sorted(attached - held)
        assert cited <= held, sorted(cited - held)
        # The list carries references only; the passages have their own route.
        assert all("text" not in excerpt for excerpt in detail["excerpts"])

        # Every cited record is one this run's own tool calls returned.
        retrieved = {record_id for call in detail["tool_calls"] for record_id in call["record_ids"]}
        assert {e["record_id"] for _, e in _memo_evidence(memo)} <= retrieved
        assert all(call["tool"] for call in detail["tool_calls"])

        reference = sorted(cited)[0]
        passage = await client.get(f"{base}/{case_ref}/excerpts/{quote(reference, safe='')}", headers=auth_headers)
        assert passage.status_code == 200, passage.text
        body = passage.json()
        assert body["excerpt_ref"] == reference and body["text"] and body["verified"] is True
        assert body["sha256"] == "sha256:" + hashlib.sha256(body["text"].encode("utf-8")).hexdigest()

        missing = await client.get(f"{base}/{case_ref}/excerpts/excerpt:not-a-reference", headers=auth_headers)
        assert missing.status_code == 404 and missing.json()["error"]["reason"] == "excerpt_not_found"

        # At rest the passage is ciphertext, not the provider's record in clear text.
        stored = await _stored_excerpts(TEST_TENANT_ID, case_ref)
        entry = next(e for e in stored if e["excerpt_ref"] == reference)
        assert "text" not in entry and entry["text_encrypted"]
        assert body["text"][:40] not in json.dumps(stored)

        # A passage that no longer matches its digest is refused, never shown beside that digest.
        await _tamper_with_excerpt(TEST_TENANT_ID, case_ref, reference)
        tampered = await client.get(f"{base}/{case_ref}/excerpts/{quote(reference, safe='')}", headers=auth_headers)
        assert tampered.status_code == 409
        assert tampered.json()["error"]["reason"] == "excerpt_integrity_failed"
        assert "TAMPERED" not in tampered.text

        # Forgetting the passages keeps every reference the memo cites.
        forgotten = await client.delete(f"{base}/{case_ref}/excerpts", headers=auth_headers)
        assert forgotten.status_code == 200 and forgotten.json()["forgotten"] >= 1
        after = (await client.get(f"{base}/{case_ref}", headers=auth_headers)).json()
        assert {e["excerpt_ref"] for e in after["excerpts"]} == held
        gone = await client.get(f"{base}/{case_ref}/excerpts/{quote(reference, safe='')}", headers=auth_headers)
        assert gone.status_code == 404 and gone.json()["error"]["reason"] == "excerpt_not_held"
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)


# --- decision requests (PRD G-3) --------------------------------------------------------------------


def _decision_runtime(fake: Any) -> CaseRuntime:
    """A runtime whose decisions go through a decision service, as production does with Grantex."""
    from core.cases.decision_requests import ServiceDecisionVerifier

    return _runtime(decision_service=lambda: fake, decision_verifier=ServiceDecisionVerifier(fake))


async def test_case_api_decision_request_reaches_the_approval_page_and_four_eyes_decides(
    client: Any, auth_headers: dict[str, str], scripted_model: Any
) -> None:
    """A decline needs two different approvers, and neither approval happens in this console."""
    from api.main import app
    from api.v1 import governed_cases as routes
    from core.cases.decision_requests import DecisionServiceError
    from core.test_doubles.fake_decision_grants import FakeDecisionGrantService

    scripted_model([_respond])
    fake = FakeDecisionGrantService()
    runtime = _decision_runtime(fake)
    app.dependency_overrides[routes.get_case_runtime] = lambda: runtime
    try:
        application = MockProvider().fixture("us-clean-hollowbrook").application
        case_ref = (
            await client.post("/api/v1/governed-cases", json={"application": application}, headers=auth_headers)
        ).json()["case_ref"]
        assert (
            await client.post(f"/api/v1/governed-cases/{case_ref}/investigate", headers=auth_headers)
        ).status_code == 202

        requested = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision-requests",
            json={"outcome": "decline", "override_reason": "The applicant withdrew.", "client_dwell_ms": 45_000},
            headers=auth_headers,
        )
        assert requested.status_code == 201, requested.text
        request_body = requested.json()
        request_id = request_body["request_id"]
        # Four eyes on a decline, and the approval happens only on the issuer's page.
        assert request_body["approvals_required"] == 2
        assert request_body["approval_page"].endswith(f"/decisions/{request_id}")
        assert request_body["status"] == "pending" and request_body["grants_ready"] is False
        assert request_body["requested_by"].startswith("user:")
        assert request_body["override_reason"] == "The applicant withdrew."

        # The request is bound to the case version and to this exact action.
        assert request_body["action"]["decision"] == "decline"
        assert request_body["case_version"] == request_body["case_version"]

        not_yet = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision",
            json={"outcome": "decline", "decision_request_id": request_id},
            headers=auth_headers,
        )
        assert not_yet.status_code == 409 and not_yet.json()["error"]["reason"] == "decision_not_approved"

        # First approver: the issuer measured the dwell, not this console.
        fake.approve(request_id, "user:9f:approver-a", dwell_ms=61_250)
        status = (
            await client.get(f"/api/v1/governed-cases/{case_ref}/decision-requests/{request_id}", headers=auth_headers)
        ).json()
        assert status["approvals_received"] == 1 and status["grants_ready"] is False
        assert status["approvals"][0]["dwell_source"] == "server" and status["approvals"][0]["dwell_ms"] == 61_250
        assert status["case_changed"] is False

        # The second approver cannot be the first.
        with pytest.raises(DecisionServiceError) as same:
            fake.approve(request_id, "user:9f:approver-a")
        assert same.value.detail == "same_approver"

        fake.approve(request_id, "user:9f:approver-b", dwell_ms=30_000)
        approved = await client.get(
            f"/api/v1/governed-cases/{case_ref}/decision-requests/{request_id}", headers=auth_headers
        )
        status = approved.json()
        assert status["status"] == "approved" and status["grants_ready"] is True
        assert [a["approver"] for a in status["approvals"]] == ["user:9f:approver-a", "user:9f:approver-b"]
        # The status answer is the one that could carry grants, and it must not: the tokens stay
        # on the server and only their ids are ever recorded.
        tokens = await fake.grants(request_id)
        assert tokens and all(token not in approved.text for token in tokens)
        assert "decision_grants" not in approved.text and "decisionGrants" not in approved.text

        # A decision without grants is still refused, and the request cannot decide another outcome.
        bare = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision", json={"outcome": "decline"}, headers=auth_headers
        )
        assert bare.status_code == 403 and bare.json()["error"]["reason"] == "decision_required"
        mismatched = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision",
            json={"outcome": "approve", "decision_request_id": request_id},
            headers=auth_headers,
        )
        assert mismatched.status_code == 409 and mismatched.json()["error"]["reason"] == "decision_outcome_mismatch"

        decided = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision",
            json={"outcome": "decline", "decision_request_id": request_id, "client_dwell_ms": 120_000},
            headers=auth_headers,
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["state"] == "decided"

        fetched = await client.get(f"/api/v1/governed-cases/{case_ref}", headers=auth_headers)
        detail = fetched.json()
        approvers = [a["approver"] for a in detail["decision"]["approvers"]]
        assert approvers == ["user:9f:approver-a", "user:9f:approver-b"]
        # The case records the grant ids, never the tokens themselves.
        assert [a["decision_grant_id"] for a in detail["decision"]["approvers"]] == [
            f"jti-{request_id}-1",
            f"jti-{request_id}-2",
        ]
        assert all(token not in fetched.text for token in tokens)
        assert detail["case"]["state"] == "decided"
        assert [r["request_id"] for r in detail["decision_requests"]] == [request_id]

        # The grants are spent: the same request cannot decide the case twice.
        again = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision",
            json={"outcome": "decline", "decision_request_id": request_id},
            headers=auth_headers,
        )
        assert again.status_code in (403, 409)
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)


async def test_a_case_that_changes_after_the_request_can_no_longer_be_decided_on_it(
    client: Any, auth_headers: dict[str, str], scripted_model: Any
) -> None:
    from api.main import app
    from api.v1 import governed_cases as routes
    from core.test_doubles.fake_decision_grants import FakeDecisionGrantService
    from tests.integration.conftest import TEST_TENANT_ID

    scripted_model([_respond, _respond])
    fake = FakeDecisionGrantService()
    runtime = _decision_runtime(fake)
    app.dependency_overrides[routes.get_case_runtime] = lambda: runtime
    try:
        application = MockProvider().fixture("gb-missing-owner-marlpit").application
        case_ref = (
            await client.post("/api/v1/governed-cases", json={"application": application}, headers=auth_headers)
        ).json()["case_ref"]
        assert (
            await client.post(f"/api/v1/governed-cases/{case_ref}/investigate", headers=auth_headers)
        ).status_code == 202
        await dispose_screening_hits(TEST_TENANT_ID, case_ref, runtime=runtime, actor="user:analyst")

        requested = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision-requests", json={"outcome": "approve"}, headers=auth_headers
        )
        assert requested.status_code == 201, requested.text
        request_id = requested.json()["request_id"]
        assert requested.json()["approvals_required"] == 1
        fake.approve(request_id, "user:9f:approver-a")

        # An analyst reviews a disposition: the case has materially changed.
        detail = (await client.get(f"/api/v1/governed-cases/{case_ref}", headers=auth_headers)).json()
        hit_id = detail["screening_dispositions"][0]["hit_id"]
        reviewed = await client.post(
            f"/api/v1/governed-cases/{case_ref}/screening-dispositions/{hit_id}/review",
            json={"action": "accepted", "final_outcome": detail["screening_dispositions"][0]["proposed_outcome"]},
            headers=auth_headers,
        )
        assert reviewed.status_code == 200, reviewed.text

        status = (
            await client.get(f"/api/v1/governed-cases/{case_ref}/decision-requests/{request_id}", headers=auth_headers)
        ).json()
        assert status["case_changed"] is True
        # The issuer heard about the new version and superseded its own request as well.
        assert status["status"] == "superseded" and status["grants_ready"] is False
        assert status["approval_page"].endswith(f"/decisions/{request_id}")

        refused = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision",
            json={"outcome": "approve", "decision_request_id": request_id},
            headers=auth_headers,
        )
        assert refused.status_code == 409 and refused.json()["error"]["reason"] == "case_changed"
        case, _ = await _load(TEST_TENANT_ID, case_ref)
        assert case.state == "awaiting_decision" and case.decision is None
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)


async def test_decision_requests_are_refused_without_a_configured_issuer_and_outside_awaiting_decision(
    client: Any, auth_headers: dict[str, str]
) -> None:
    from api.main import app
    from api.v1 import governed_cases as routes
    from core.test_doubles.fake_decision_grants import FakeDecisionGrantService

    application = MockProvider().fixture("us-clean-hollowbrook").application

    app.dependency_overrides[routes.get_case_runtime] = lambda: _runtime(decision_service=lambda: None)
    try:
        case_ref = (
            await client.post("/api/v1/governed-cases", json={"application": application}, headers=auth_headers)
        ).json()["case_ref"]
        # Not investigated yet: no memo to approve against, and no issuer either.
        refused = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision-requests", json={"outcome": "approve"}, headers=auth_headers
        )
        assert refused.status_code == 503
        assert refused.json()["error"]["reason"] == "decision_service_not_configured"
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)

    fake = FakeDecisionGrantService()
    app.dependency_overrides[routes.get_case_runtime] = lambda: _decision_runtime(fake)
    try:
        case_ref = (
            await client.post("/api/v1/governed-cases", json={"application": application}, headers=auth_headers)
        ).json()["case_ref"]
        early = await client.post(
            f"/api/v1/governed-cases/{case_ref}/decision-requests", json={"outcome": "approve"}, headers=auth_headers
        )
        assert early.status_code == 409 and early.json()["error"]["reason"] == "transition_not_allowed"
        unknown = await client.get(
            f"/api/v1/governed-cases/{case_ref}/decision-requests/dr_99999999", headers=auth_headers
        )
        assert unknown.status_code == 404 and unknown.json()["error"]["reason"] == "decision_request_not_found"
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)
