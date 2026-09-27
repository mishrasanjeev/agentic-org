# SPDX-License-Identifier: Apache-2.0
"""Regression tests: a governed-case agent cannot be wired without its grant check (2026-09-27).

The provider tool gateway already refused every call it had no authorizer for, but the gateway and
both agents' dependency objects defaulted ``authorizer`` to ``None`` and could be built that way
anywhere; a ``CaseRuntime`` whose authorizer factory returned ``None`` still moved the case and
started the agent run; a ``CaseRuntime`` built with no authorizer factory at all failed with a
``TypeError`` on its first run instead of a named refusal; and the sample-case seed relied on the
runtime's default factory.

1. Outside local and test runtimes a gateway, or an agent's dependencies, built without an
   authorizer is refused when it is built, and the field has no default anywhere.
2. A ``CaseRuntime`` whose factory returns ``None`` refuses with ``authorization_unavailable``
   before the case moves or a provider is built, for investigations and screening dispositions.
   A ``CaseRuntime`` with no factory cannot be built outside local and test runtimes; where it can,
   every run is refused the same way.
3. A gateway built with no grant check - possible only in a test runtime, and only through the
   named ``NO_AUTHORIZER_FOR_TESTS`` - still refuses every call with ``authorization_unavailable``
   and never dispatches the provider.
4. The seed passes ``case_authorizer`` explicitly, and no production code passes the test sentinel,
   ``authorizer=None`` or ``authorizer_factory=None``.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from connectors.framework.verification_provider import BusinessQuery, Deadline
from connectors.providers.mock import MockConfig, MockProvider
from core import config
from core.agents.business_underwriter import UnderwriterConfig, UnderwriterDependencies, run_underwriter
from core.agents.screening_disposition import DispositionDependencies
from core.cases import runtime as case_runtime
from core.cases.decisions import RequireDecisionGrant
from core.cases.grant_authorizer import case_authorizer
from core.cases.runtime import CaseRuntime, dispose_screening_hits, load_case_policies, run_case_step
from core.cases.states import CaseError
from core.tool_gateway import provider_gateway
from core.tool_gateway.provider_gateway import AUTHORIZATION_UNAVAILABLE, ProviderToolGateway, ToolRefusedError

REPO = pathlib.Path(__file__).resolve().parents[2]
AT = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
TENANT = uuid.UUID(int=0xC1)
CASE_REF = "case_" + "c" * 24
SENTINEL = "NO_AUTHORIZER_FOR_TESTS"


def _provider() -> MockProvider:
    return MockProvider(MockConfig(clock=lambda: AT))


BUILDERS: dict[str, Callable[[MockProvider, Any], object]] = {
    "gateway": lambda provider, authorizer: ProviderToolGateway(
        provider=provider, agent="business_underwriter", tool_set=frozenset({"resolve_business"}), authorizer=authorizer
    ),
    "underwriter": lambda provider, authorizer: UnderwriterDependencies(provider=provider, authorizer=authorizer),
    "disposition": lambda provider, authorizer: DispositionDependencies(provider=provider, authorizer=authorizer),
}


# --- 1. no grant check, no gateway, outside local and test runtimes ------------------------------


@pytest.mark.parametrize("env", ["production", "staging", "preview", "uat"])
@pytest.mark.parametrize("build", BUILDERS.values(), ids=BUILDERS)
def test_gateway_without_authorizer_cannot_be_built_in_a_strict_runtime(
    monkeypatch: pytest.MonkeyPatch, env: str, build: Callable[[MockProvider, Any], object]
) -> None:
    provider = _provider()
    monkeypatch.setattr(config.settings, "env", env)
    with pytest.raises(ValueError, match="grant check") as refused:
        build(provider, None)
    assert isinstance(refused.value, provider_gateway.AuthorizerRequiredError)
    assert provider._state.attempts == {}


@pytest.mark.parametrize("owner", [ProviderToolGateway, UnderwriterDependencies, DispositionDependencies])
def test_the_authorizer_cannot_be_left_out_by_omission(owner: type) -> None:
    [field] = [f for f in dataclasses.fields(owner) if f.name == "authorizer"]
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING


# --- 2. a case runtime whose factory returns None --------------------------------------------------


class _Case:
    """Enough of a governed case row for the runtime to read and write."""

    def __init__(self, state: str) -> None:
        self.state, self.version = state, 1
        self.application = {"legal_name": "Brightwater Lantern Works", "jurisdiction": "GB"}
        self.provider, self.policy_id, self.purpose = "mock", "business_onboarding_uk", "aml.cdd.onboarding"
        self.memo: dict[str, Any] | None = None
        self.failure_reason: str | None = None
        self.agent_records: list[dict[str, Any]] = []
        self.parties = [{"kind": "person", "name": "Ada Brightwater"}]
        self.screening_results = [{"subject": {"name": "Ada Brightwater"}, "hits": [{"hit_id": "hit-0001"}]}]
        self.screening_dispositions: list[dict[str, Any]] = []
        self.excerpts_encrypted: list[dict[str, Any]] = []
        self.decision_requests: list[dict[str, Any]] = []


def _factory_returning_none(tenant: str, case_ref: str, role: str, purpose: str) -> None:
    return None


def _runtime_without_grant_check(
    monkeypatch: pytest.MonkeyPatch,
    case: _Case,
    providers: list[MockProvider],
    writes: list[str],
    authorizer_factory: Any = _factory_returning_none,
) -> CaseRuntime:
    """A runtime that cannot supply a grant check (by default its factory returns ``None``), over one case."""

    async def get_case(_session: Any, _tenant: Any, _case_ref: str, *, for_update: bool = False) -> _Case:
        return case

    async def transition(_session: Any, row: _Case, target: Any, **_kwargs: Any) -> _Case:
        writes.append(f"transition:{target.value}")
        row.state, row.version = target.value, row.version + 1
        return row

    async def record_update(_session: Any, row: _Case, **_kwargs: Any) -> _Case:
        writes.append("record_update")
        row.version += 1
        return row

    monkeypatch.setattr(case_runtime, "get_case", get_case)
    monkeypatch.setattr(case_runtime, "transition", transition)
    monkeypatch.setattr(case_runtime, "record_update", record_update)

    @asynccontextmanager
    async def session(_tenant: uuid.UUID) -> AsyncIterator[Any]:
        yield object()

    async def enabled(_tenant: uuid.UUID) -> bool:
        return True

    def provider_factory(_name: str) -> MockProvider:
        providers.append(_provider())
        return providers[-1]

    return CaseRuntime(
        authorizer_factory=authorizer_factory,
        provider_factory=provider_factory,
        session_factory=session,
        flag=enabled,
        push_kick=lambda tenant: None,
        llm_model="scripted",
        clock=lambda: AT,
    )


async def test_case_runtime_refuses_a_factory_returning_none(monkeypatch: pytest.MonkeyPatch) -> None:
    case, providers, writes = _Case("submitted"), [], []
    runtime = _runtime_without_grant_check(monkeypatch, case, providers, writes)

    result = await run_case_step(
        {"id": "investigate", "action": "investigate"},
        {"tenant_id": str(TENANT), "trigger_payload": {"case_ref": CASE_REF}},
        runtime=runtime,
    )

    assert result["status"] == "failed" and result["error"] == AUTHORIZATION_UNAVAILABLE
    # Refused before the case moved and before a provider was built: no run started without a grant check.
    assert writes == [] and case.state == "submitted"
    assert providers == []
    with pytest.raises(CaseError) as refused:
        runtime.authorizer_for(str(TENANT), CASE_REF, "business_underwriter", "aml.cdd.onboarding")
    assert refused.value.reason == AUTHORIZATION_UNAVAILABLE and refused.value.status == 503
    assert "authorizer_factory returned None for business_underwriter" in refused.value.detail


async def test_screening_dispositions_refuse_a_factory_returning_none(monkeypatch: pytest.MonkeyPatch) -> None:
    case, providers, writes = _Case("awaiting_decision"), [], []
    runtime = _runtime_without_grant_check(monkeypatch, case, providers, writes)

    with pytest.raises(CaseError) as refused:
        await dispose_screening_hits(TENANT, CASE_REF, runtime=runtime, actor="user:analyst-01")

    assert refused.value.reason == AUTHORIZATION_UNAVAILABLE and refused.value.status == 503
    assert writes == [] and case.screening_dispositions == [] and case.agent_records == []
    assert providers == []


@pytest.mark.parametrize("env", ["production", "staging", "preview", "uat"])
def test_case_runtime_without_a_factory_cannot_be_built_in_a_strict_runtime(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setattr(config.settings, "env", env)
    with pytest.raises(ValueError, match="authorizer_factory") as refused:
        CaseRuntime(authorizer_factory=None, decision_verifier=RequireDecisionGrant())  # type: ignore[arg-type]
    assert isinstance(refused.value, provider_gateway.AuthorizerRequiredError)
    assert "grant check" in str(refused.value) and repr(env) in str(refused.value)

    # Nor by copying a correctly wired runtime and clearing its factory.
    wired = CaseRuntime(authorizer_factory=case_authorizer, decision_verifier=RequireDecisionGrant())
    with pytest.raises(provider_gateway.AuthorizerRequiredError, match="authorizer_factory"):
        dataclasses.replace(wired, authorizer_factory=None)  # type: ignore[arg-type]


async def test_case_runtime_without_a_factory_refuses_every_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where a runtime with no factory can be built at all - local and test - no agent run starts.

    Every run is refused with ``authorization_unavailable`` as a failed workflow step, never a
    ``TypeError`` from calling ``None``, and before the case moves or a provider is built.
    """
    monkeypatch.setattr(config.settings, "env", "test")
    case, providers, writes = _Case("submitted"), [], []
    runtime = _runtime_without_grant_check(monkeypatch, case, providers, writes, authorizer_factory=None)

    investigated = await run_case_step(
        {"id": "investigate", "action": "investigate"},
        {"tenant_id": str(TENANT), "trigger_payload": {"case_ref": CASE_REF}},
        runtime=runtime,
    )
    assert investigated["status"] == "failed" and investigated["error"] == AUTHORIZATION_UNAVAILABLE
    assert writes == [] and case.state == "submitted" and case.agent_records == []

    case.state = "awaiting_decision"
    disposed = await run_case_step(
        {"id": "dispose", "action": "dispose_screening_hits"},
        {"tenant_id": str(TENANT), "trigger_payload": {"case_ref": CASE_REF}},
        runtime=runtime,
    )
    assert disposed["status"] == "failed" and disposed["error"] == AUTHORIZATION_UNAVAILABLE
    assert writes == [] and case.screening_dispositions == [] and case.agent_records == []
    assert providers == []

    for role in ("business_underwriter", "screening_disposition"):
        with pytest.raises(CaseError) as refused:
            runtime.authorizer_for(str(TENANT), CASE_REF, role, "aml.cdd.onboarding")
        assert refused.value.reason == AUTHORIZATION_UNAVAILABLE and refused.value.status == 503
        assert refused.value.detail == f"no authorizer_factory for {role}"


# --- 3. a gateway with no grant check still refuses every call ------------------------------------


async def test_bare_case_runtime_denies_at_the_gateway_with_authorization_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare: the gateway and agent dependencies a case run uses, built with no grant check at all.

    Only a test runtime can build them, and only through the named sentinel. The gateway must still
    refuse every call before anything is dispatched, as it does for an authorizer that cannot answer.
    """
    monkeypatch.setattr(config.settings, "env", "test")
    unchecked = provider_gateway.NO_AUTHORIZER_FOR_TESTS

    provider = _provider()
    gateway = BUILDERS["gateway"](provider, unchecked)
    assert isinstance(gateway, ProviderToolGateway)
    dispatched: list[str] = []

    async def invoke() -> list[Any]:
        dispatched.append("resolve_business")
        return []

    query = BusinessQuery(name="Brightwater Lantern Works")
    with pytest.raises(ToolRefusedError) as refused:
        await gateway.call("resolve_business", query, invoke)
    assert refused.value.reason == AUTHORIZATION_UNAVAILABLE
    with pytest.raises(ToolRefusedError) as refused:
        await gateway.resolve_business(query, deadline=Deadline.after(5))
    assert refused.value.reason == AUTHORIZATION_UNAVAILABLE
    assert dispatched == [] and provider._state.attempts == {}
    assert [(r.tool, r.outcome, r.reason, r.output_sha256) for r in gateway.records] == [
        ("resolve_business", "denied", AUTHORIZATION_UNAVAILABLE, None)
    ] * 2

    # The same through the underwriter, as the case runtime drives it: the run stops at the first call.
    provider = _provider()
    outcome = await run_underwriter(
        tenant_id=str(TENANT),
        case_id=CASE_REF,
        application=provider.fixture("gb-clean-brightwater").application,
        config=UnderwriterConfig(
            policy=load_case_policies()["business_onboarding_uk"], require_os_isolation=False, llm_model="scripted"
        ),
        deps=UnderwriterDependencies(provider=provider, authorizer=unchecked, clock=lambda: AT),
    )
    assert outcome.status == "failed" and outcome.memo is None
    assert outcome.failure_reason == f"tool_refused:{AUTHORIZATION_UNAVAILABLE}"
    assert [(c["tool"], c["outcome"], c["reason"]) for c in outcome.tool_calls] == [
        ("resolve_business", "denied", AUTHORIZATION_UNAVAILABLE)
    ]
    assert provider._state.attempts == {}


# --- 4. the seed and every production caller pass a real grant check ---------------------------


async def test_seed_script_injects_the_case_authorizer_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import seed_governed_cases as seed

    built: dict[str, Any] = {}

    class _StopAfterBuildError(Exception):
        """Stops the seed once it has built its runtime; nothing is written."""

    def record(**kwargs: Any) -> CaseRuntime:
        built.update(kwargs)
        raise _StopAfterBuildError

    monkeypatch.setattr(case_runtime, "CaseRuntime", record)
    with pytest.raises(_StopAfterBuildError):
        await seed.seed_cases(["gb-clean-brightwater"])
    assert built.get("authorizer_factory") is case_authorizer


def test_production_code_never_passes_the_test_sentinel_or_none() -> None:
    offenders: list[str] = []
    for top in ("api", "auth", "core", "workflows", "connectors", "scripts"):
        for path in (REPO / top).rglob("*.py"):
            relative = path.relative_to(REPO).as_posix()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                named = (
                    (isinstance(node, ast.Name) and node.id == SENTINEL)
                    or (isinstance(node, ast.alias) and node.name == SENTINEL)
                    or (isinstance(node, ast.Attribute) and node.attr == SENTINEL)
                )
                if named and relative != "core/tool_gateway/provider_gateway.py":
                    offenders.append(f"{relative}: {SENTINEL}")
                if isinstance(node, ast.keyword) and node.arg in ("authorizer", "authorizer_factory"):
                    if isinstance(node.value, ast.Constant) and node.value.value is None:
                        offenders.append(f"{relative}:{node.value.lineno}: {node.arg}=None")
    assert offenders == []
