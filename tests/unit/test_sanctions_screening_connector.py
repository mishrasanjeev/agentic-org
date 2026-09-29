# SPDX-License-Identifier: Apache-2.0
"""The provider-neutral sanctions screening connector, and grants under the deprecated ``sanctions_api`` id.

The screening connector used to call one commercial screening service directly.
``sanctions_screening`` screens through the verification provider seam. ``sanctions_api`` stays
registered, with a deprecation warning, as a separate legacy connector that keeps its old tools
and requests (``tests/unit/test_sanctions_api_legacy_connector.py``), so existing configurations
and agents keep the connector they use; an agent moves to ``sanctions_screening`` by linking it.
A grant held under either id covers the tools the two connectors share.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
import structlog
from grantex import Grantex
from langchain_core.messages import AIMessage

import connectors  # noqa: F401 - registers the native connectors
from auth import grantex_registration
from auth.grant_enforcement import DenialReason, EnforcementMode, GrantCallContext, check_tool_grant
from auth.run_grants import RunGrant
from connectors.framework.verification_provider import (
    BusinessSubject,
    Capability,
    CapabilityNotSupported,
    Deadline,
    PersonSubject,
    ProviderTimeout,
    ScreeningResult,
    ScreenOptions,
    VerificationProvider,
)
from connectors.ops import sanctions_screening as module
from connectors.ops.sanctions_api import SanctionsApiConnector
from connectors.ops.sanctions_screening import SanctionsScreeningConnector, ScreeningUnavailableError
from connectors.providers.mock import MockProvider
from connectors.providers.registry import ProviderRegistry
from connectors.registry import ConnectorRegistry
from core.agents import base as base_agent
from core.agents.base import BaseAgent
from core.langgraph import agent_graph, grantex_auth, tool_adapter
from core.langgraph.tool_adapter import _build_tool_index, build_tools_for_agent
from core.tool_gateway.gateway import ToolGateway
from scripts import check_denylist as dl

REPO_ROOT = Path(__file__).resolve().parents[2]
# The tools the two connectors share (OLD_TOOLS), the neutral connector's (TOOLS) and the legacy
# connector's (LEGACY_TOOLS).
OLD_TOOLS = {"screen_entity", "screen_transaction", "batch_screen"}
TOOLS = OLD_TOOLS | {"screen_person", "screen_business"}
LEGACY_ONLY_TOOLS = {"get_alert", "generate_report"}
LEGACY_TOOLS = OLD_TOOLS | LEGACY_ONLY_TOOLS
CONNECTOR_TOOLS = {"sanctions_api": LEGACY_TOOLS, "sanctions_screening": TOOLS}
# Synthetic entries from connectors/providers/mock/fixtures/watchlist.json.
LISTED_PERSON = "Jorund Halvesen"
LISTED_BUSINESS = "Corvane Maritime Logistics Ltd"
CLEAN_NAME = "Nimbus Example Traders"


class PersonOnlyProvider(VerificationProvider):
    name = "acme_kyb"
    capabilities = frozenset({Capability.SCREEN_PERSON})

    async def screen_person(self, s: PersonSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        result = await MockProvider().screen_person(s, opts, deadline=deadline)
        return result.model_copy(update={"provider": self.name})


class ResolveOnlyProvider(VerificationProvider):
    name = "acme_kyb"
    capabilities = frozenset({Capability.RESOLVE})


@pytest.fixture(autouse=True)
def _development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", "test")
    monkeypatch.delenv("AGENTICORG_MOCK_PROVIDER_URL", raising=False)
    monkeypatch.setattr(ProviderRegistry, "_providers", dict(ProviderRegistry._providers))


async def _connected(config: dict | None = None) -> SanctionsScreeningConnector:
    connector = SanctionsScreeningConnector(config=config)
    await connector.connect()
    return connector


# ── The deprecated id ───────────────────────────────────────────────────────


def test_old_id_still_resolves_with_a_deprecation_warning() -> None:
    cls = ConnectorRegistry.get("sanctions_api")
    assert cls is SanctionsApiConnector
    # The old id is the legacy connector, not a subclass (alias) of the neutral one.
    assert not issubclass(cls, SanctionsScreeningConnector)
    with structlog.testing.capture_logs() as logs, pytest.warns(DeprecationWarning, match="sanctions_screening"):
        connector = cls(config={"api_key": "placeholder-not-a-key"})
    assert connector.name == "sanctions_api"
    [entry] = [entry for entry in logs if entry["event"] == "connector_id_deprecated"]
    assert entry["connector"] == "sanctions_api"
    assert entry["replacement"] == "sanctions_screening"
    assert entry["log_level"] == "warning"
    # Its own five tools, get_alert and generate_report included, rather than the neutral connector's.
    assert set(connector._tool_registry) == LEGACY_TOOLS


def test_the_new_id_is_not_deprecated() -> None:
    with structlog.testing.capture_logs() as logs:
        SanctionsScreeningConnector(config={})
    assert not [entry for entry in logs if entry["event"] == "connector_id_deprecated"]


def test_catalog_and_counts_list_only_the_new_id() -> None:
    assert "sanctions_screening" in ConnectorRegistry.all_names()
    assert "sanctions_api" not in ConnectorRegistry.all_names()
    assert "sanctions_api" in ConnectorRegistry.all_names(include_deprecated=True)
    assert SanctionsApiConnector not in ConnectorRegistry.by_category("ops")


def test_agents_that_name_the_old_id_keep_their_tools() -> None:
    index = _build_tool_index(include_connector_aliases=True)
    assert index["sanctions_api:screen_entity"][0] == "sanctions_api"
    assert index["sanctions_api__batch_screen"][0] == "sanctions_api"
    # A bare name binds to the legacy connector, as it did before sanctions_screening existed,
    # unless the agent links the neutral one: moving to it is explicit, never a side effect.
    assert index["screen_entity"][0] == "sanctions_api"
    assert index["get_alert"][0] == "sanctions_api"
    scoped = _build_tool_index(connector_names=["sanctions_api"])
    assert scoped["screen_transaction"][0] == "sanctions_api"
    linked = _build_tool_index(connector_names=["sanctions_screening"])
    assert linked["screen_entity"][0] == "sanctions_screening"
    assert "get_alert" not in linked


# ── Grants under either id ──────────────────────────────────────────────────
#
# An agent's tools bind to the legacy ``sanctions_api`` connector unless its connectors name only
# ``sanctions_screening``, while its grant names whichever id was current when it was issued.
# Grantex checks a scope per connector id, so every pairing must still be allowed for the tools the
# two connectors share, by the legacy check (``grants_enforce_closed`` off) and by the F-1 check
# (warn / deny). A tool only the legacy connector has is granted under the old id, whose manifest
# lists it.

# What an agent registered before the rename holds (the scopes are stored and re-minted as is).
OLD_GRANT = [
    "agenticorg:ops:read",
    "tool:sanctions_api:read:screen_entity",
    "tool:sanctions_api:read:screen_transaction",
    "tool:sanctions_api:read:batch_screen",
]
BINDINGS = {"old id": ["sanctions_api"], "new id": ["sanctions_screening"], "no connectors": None}
# Every tool an agent can be given on each binding. The old id is the legacy connector; an agent
# that names no connectors can be given both connectors' tools (the shared ones bind to the old id).
BOUND_TOOLS = {"old id": LEGACY_TOOLS, "new id": TOOLS, "no connectors": LEGACY_TOOLS | TOOLS}


GRANTEX_TEST_URL = "https://grantex.invalid"
REVOCATION_STATUS_URL = f"{GRANTEX_TEST_URL}/v1/revocations/status"
# What the auth service answers for a grant that is neither revoked nor suspended.
LIVE_GRANT_STATUS = {"status": "active", "revoked": False, "grantId": "grnt_placeholder"}


def _grantex_client(monkeypatch: pytest.MonkeyPatch) -> Grantex:
    """A real SDK client with the manifests production loads; only token verification is stubbed."""
    monkeypatch.setenv("GRANTEX_MANIFESTS_DIR", str(REPO_ROOT / "manifests"))
    client = Grantex(api_key="placeholder-api-key", base_url=GRANTEX_TEST_URL)
    grantex_auth._load_all_manifests(client)
    return client


@contextmanager
def _auth_service(revocation_status: httpx.Response | Exception | None = None) -> Iterator[respx.MockRouter]:
    """The auth service's revocation status endpoint, which the SDK's online check calls.

    The grant is live unless ``revocation_status`` gives another answer (or an error). No other
    request to the service is expected; one would fail the test rather than reach the network.
    """
    status = revocation_status if revocation_status is not None else httpx.Response(200, json=LIVE_GRANT_STATUS)
    with respx.mock(assert_all_called=False) as service:
        route = service.get(REVOCATION_STATUS_URL)
        if isinstance(status, Exception):
            route.mock(side_effect=status)
        else:
            route.mock(return_value=status)
        yield service


def _sdk_checks_revocation_by_default() -> bool:
    """grantex 0.7 asks the auth service about every grant before ``enforce()`` allows it."""
    return getattr(Grantex(api_key="placeholder-api-key"), "_revocation_check", "offline") != "offline"


async def _check(
    monkeypatch: pytest.MonkeyPatch,
    scopes: list[str],
    tools: list[str],
    connector_names: list[str] | None,
    mode: EnforcementMode,
    revocation_status: httpx.Response | Exception | None = None,
) -> dict[str, dict]:
    """Run every tool the agent gets through the scope check; tool name -> the check's result.

    The SDK's online revocation check reaches the auth service's status endpoint, answered here:
    by default the grant is live, and ``revocation_status`` replaces that answer.
    """
    client = _grantex_client(monkeypatch)
    monkeypatch.setattr(agent_graph, "get_grantex_client", lambda: client)
    built = build_tools_for_agent(tools, connector_names=connector_names)
    assert sorted(tool.name for tool in built) == sorted(tools)
    refs = agent_graph._tool_grant_refs(built)
    run_grant = None if mode is EnforcementMode.OFF else RunGrant(mode=mode, token="placeholder", source="minted")
    grant = SimpleNamespace(grant_id="grnt_placeholder", agent_did="did:placeholder", scopes=scopes)
    results: dict[str, dict] = {}
    with patch("grantex._client.verify_grant_token", return_value=grant), _auth_service(revocation_status):
        for tool in built:
            call = {"name": tool.name, "args": {"name": CLEAN_NAME}, "id": f"call-{tool.name}"}
            state = {"messages": [AIMessage(content="", tool_calls=[call])], "grant_token": "placeholder"}
            results[tool.name] = await agent_graph.validate_tool_scopes(state, refs, run_grant)
    return results


@pytest.mark.parametrize("mode", [EnforcementMode.OFF, EnforcementMode.DENY], ids=["legacy", "deny"])
@pytest.mark.parametrize("binding", list(BINDINGS))
async def test_a_grant_issued_under_the_old_id_still_allows_its_tools(
    monkeypatch: pytest.MonkeyPatch, binding: str, mode: EnforcementMode
) -> None:
    results = await _check(monkeypatch, OLD_GRANT, sorted(OLD_TOOLS), BINDINGS[binding], mode)
    assert results == dict.fromkeys(OLD_TOOLS, {})


@pytest.mark.parametrize("mode", [EnforcementMode.OFF, EnforcementMode.DENY], ids=["legacy", "deny"])
@pytest.mark.parametrize("registration", ["api", "runtime"])
@pytest.mark.parametrize("binding", list(BINDINGS))
async def test_a_grant_issued_now_allows_every_tool_on_either_id(
    monkeypatch: pytest.MonkeyPatch, binding: str, registration: str, mode: EnforcementMode
) -> None:
    # Every tool of the connector the agent is bound to: on the old id that is the legacy
    # connector's five, get_alert and generate_report included, not the neutral connector's.
    tools = sorted(BOUND_TOOLS[binding])
    connector_names = BINDINGS[binding]
    scopes = (
        grantex_registration._tools_to_scopes(tools, "ops", connector_names=connector_names)
        if registration == "api"
        else grantex_auth._tools_to_scopes(tools)
    )
    results = await _check(monkeypatch, scopes, tools, connector_names, mode)
    assert results == dict.fromkeys(tools, {})


@pytest.mark.parametrize("mode", [EnforcementMode.OFF, EnforcementMode.DENY], ids=["legacy", "deny"])
@pytest.mark.parametrize("binding", list(BINDINGS))
async def test_a_grant_issued_under_the_old_id_does_not_cover_the_new_tools(
    monkeypatch: pytest.MonkeyPatch, binding: str, mode: EnforcementMode
) -> None:
    new_tools = ["screen_business", "screen_person"]
    if binding == "old id":
        # The old id is the legacy connector, which has no such tools: the agent is not given them.
        assert build_tools_for_agent(new_tools, connector_names=BINDINGS[binding]) == []
        return
    # The old id's manifest does not list them and the grant holds no scope under the new id.
    results = await _check(monkeypatch, OLD_GRANT, new_tools, BINDINGS[binding], mode)
    assert [result["status"] for result in results.values()] == ["failed", "failed"]


@pytest.mark.parametrize("binding", ["old id", "new id"])
def test_new_grants_name_the_live_id(binding: str) -> None:
    connector_names = BINDINGS[binding]
    scopes = [
        *grantex_registration._tools_to_scopes(sorted(TOOLS), "ops", connector_names=connector_names),
        *grantex_auth._tools_to_scopes(sorted(TOOLS)),
    ]
    assert not [scope for scope in scopes if scope.startswith("tool:sanctions_api:")]
    assert {scope.rsplit(":", 1)[1] for scope in scopes if scope.startswith("tool:sanctions_screening:read:")} == TOOLS


@pytest.mark.parametrize("binding", ["old id", "no connectors"])
def test_tools_only_the_legacy_connector_has_are_granted_under_the_old_id(binding: str) -> None:
    # The neutral connector's manifest does not list them, so a scope under the live id would
    # never cover them: Grantex answers "unknown tool" there, and the grant holds nothing else.
    tools = sorted(LEGACY_ONLY_TOOLS)
    expected = [f"tool:sanctions_api:read:{tool}" for tool in tools]
    for scopes in (
        grantex_registration._tools_to_scopes(tools, "ops", connector_names=BINDINGS[binding]),
        grantex_registration._tools_to_scopes([f"sanctions_api:{tool}" for tool in tools], "ops"),
        grantex_auth._tools_to_scopes(tools),
    ):
        assert sorted(scope for scope in scopes if scope.startswith("tool:")) == expected


def test_only_a_renamed_connector_has_other_ids() -> None:
    assert ConnectorRegistry.ids_of("sanctions_screening") == ("sanctions_screening", "sanctions_api")
    assert ConnectorRegistry.ids_of("sanctions_api") == ("sanctions_api", "sanctions_screening")
    assert ConnectorRegistry.live_id("sanctions_api") == "sanctions_screening"
    assert ConnectorRegistry.ids_of("jira") == ("jira",)
    assert ConnectorRegistry.live_id("not_a_connector") == "not_a_connector"


async def test_an_error_checking_the_other_id_is_a_denial() -> None:
    class Client:
        def enforce(self, *, grant_token: str, connector: str, tool: str, amount: float | None = None):
            if connector == "sanctions_api":
                raise ConnectionError("jwks unreachable")
            return SimpleNamespace(allowed=False, reason=f"No scope grants access to connector '{connector}'.")

    check = await check_tool_grant(
        mode=EnforcementMode.DENY,
        grant_token="placeholder",
        connector="sanctions_screening",
        tool="screen_entity",
        context=GrantCallContext(tenant_id="t", agent_id="a", agent_type="risk_sentinel", runtime="test"),
        client_factory=Client,
    )
    assert not check.dispatch_allowed
    assert check.denial is not None
    assert check.denial.reason is DenialReason.ENFORCEMENT_UNAVAILABLE


@pytest.mark.parametrize("mode", [EnforcementMode.OFF, EnforcementMode.DENY], ids=["legacy", "deny"])
@pytest.mark.parametrize("binding", list(BINDINGS))
async def test_a_grant_for_neither_id_is_still_denied(
    monkeypatch: pytest.MonkeyPatch, binding: str, mode: EnforcementMode
) -> None:
    results = await _check(monkeypatch, ["tool:jira:read:search_issues"], sorted(OLD_TOOLS), BINDINGS[binding], mode)
    for result in results.values():
        assert result["status"] == "failed"


# ── Revocation ──────────────────────────────────────────────────────────────
#
# A grant that covers every tool is still refused, on every tool and in every mode, when the auth
# service says it is revoked or suspended, or cannot say: grantex 0.7 checks revocation online by
# default and fails closed, and nothing here may turn that off.

REVOCATION_UNKNOWN_OR_NOT_LIVE = {
    "revoked": httpx.Response(200, json={"status": "revoked", "revoked": True, "grantId": "grnt_placeholder"}),
    "suspended": httpx.Response(200, json={"status": "suspended", "revoked": True, "grantId": "grnt_placeholder"}),
    "unknown to the service": httpx.Response(200, json={"status": "unknown", "revoked": True}),
    "status endpoint not served": httpx.Response(404, json={"message": "Not found", "code": "NOT_FOUND"}),
    "service unreachable": httpx.ConnectError("Name or service not known"),
}


@pytest.mark.skipif(
    not _sdk_checks_revocation_by_default(), reason="the installed grantex SDK does not check revocation by default"
)
@pytest.mark.parametrize("mode", [EnforcementMode.OFF, EnforcementMode.DENY], ids=["legacy", "deny"])
@pytest.mark.parametrize("answer", list(REVOCATION_UNKNOWN_OR_NOT_LIVE))
async def test_a_grant_not_known_to_be_live_is_refused(
    monkeypatch: pytest.MonkeyPatch, answer: str, mode: EnforcementMode
) -> None:
    # An unreachable service is retried; the retries need not wait here.
    monkeypatch.setattr("grantex._http.HttpClient._retry_delay", lambda _self, _attempt: 0.0)
    tools = sorted(TOOLS)
    scopes = grantex_auth._tools_to_scopes(tools)
    results = await _check(
        monkeypatch, scopes, tools, ["sanctions_screening"], mode, REVOCATION_UNKNOWN_OR_NOT_LIVE[answer]
    )
    assert [result.get("status") for result in results.values()] == ["failed"] * len(tools)
    # The same grant is allowed when the service says it is live.
    assert await _check(monkeypatch, scopes, tools, ["sanctions_screening"], mode) == dict.fromkeys(tools, {})


# ── Grants under either id on every dispatch path ───────────────────────────
#
# Each path gets connector-qualified tools, as an agent's ``authorized_tools`` name them: LangGraph
# (``validate_tool_scopes``), ``BaseAgent`` without a gateway (``execute_agent_tool``) and
# ``ToolGateway.execute``. In ``off`` each asks Grantex about the grant token directly; in warn and
# deny each checks the run grant, and the gateway also keeps its ``off`` check of a token passed to
# it. Every one of those checks accepts a scope held under either id of the renamed connector, and
# never a scope for a connector that ``register_deprecated`` does not link to it.

DISPATCH_PATHS = ["langgraph", "base_agent", "tool_gateway"]
MODES = [EnforcementMode.OFF, EnforcementMode.WARN, EnforcementMode.DENY]
MODE_IDS = [mode.value for mode in MODES]
TENANT = str(uuid.UUID(int=0x5C4E))
AGENT = str(uuid.UUID(int=0xA6E))
GRANT_TOKEN = "placeholder-grant-token"  # noqa: S105 - not a credential
GRANT_DENIAL_EVENTS = {"grant_enforcement_would_deny", "grant_enforcement_denied"}
# What an agent registered or re-scoped after the rename holds.
NEW_GRANT = ["agenticorg:ops:read", *(f"tool:sanctions_screening:read:{tool}" for tool in sorted(TOOLS))]
GRANTS = {"sanctions_api": (OLD_GRANT, sorted(OLD_TOOLS)), "sanctions_screening": (NEW_GRANT, sorted(TOOLS))}


async def _dispatch(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    mode: EnforcementMode,
    scopes: list[str],
    connector: str,
    tools: list[str],
) -> tuple[dict[str, bool], list[tuple[str, str]], list[dict]]:
    """Call each of ``tools`` on ``connector`` through ``path``, with a grant that holds ``scopes``.

    Returns tool -> whether the call got past the grant checks, the ``(connector, tool)`` calls
    that reached the connector, and the grant denials recorded on the way.
    """
    client = _grantex_client(monkeypatch)
    monkeypatch.setattr(grantex_auth, "get_grantex_client", lambda: client)
    monkeypatch.setattr(agent_graph, "get_grantex_client", lambda: client)
    if mode is EnforcementMode.OFF:
        run_grant = RunGrant(mode=mode)
    else:
        run_grant = RunGrant(mode=mode, token=GRANT_TOKEN, source="minted")
    dispatched: list[tuple[str, str]] = []

    async def execute_connector_tool(connector_name: str, tool_name: str, *_args: object, **_kwargs: object) -> dict:
        dispatched.append((connector_name, tool_name))
        return {"screened": tool_name}

    class Connector:
        async def execute_tool(self, tool_name: str, params: dict) -> dict:
            return await execute_connector_tool(connector, tool_name)

    monkeypatch.setattr(tool_adapter, "_execute_connector_tool", execute_connector_tool)
    monkeypatch.setattr(tool_adapter, "load_connector_config", AsyncMock(return_value={}))
    monkeypatch.setattr(base_agent, "resolve_run_grant", AsyncMock(return_value=run_grant))
    gateway = ToolGateway()
    gateway.register_connector(connector, Connector(), tenant_id=TENANT)
    authorized_tools = [f"{connector}:{tool}" for tool in tools]
    grant = SimpleNamespace(grant_id="grnt_placeholder", agent_did="did:placeholder", scopes=scopes)
    passed: dict[str, bool] = {}
    with (
        patch("grantex._client.verify_grant_token", return_value=grant),
        _auth_service(),
        structlog.testing.capture_logs() as logs,
    ):
        for tool in tools:
            params = {"name": CLEAN_NAME}
            if path == "base_agent":
                agent = BaseAgent(agent_id=AGENT, tenant_id=TENANT, authorized_tools=authorized_tools)
                agent.grant_token = GRANT_TOKEN
                result = await agent._call_tool(connector, tool, params)
            elif path == "tool_gateway":
                result = await gateway.execute(
                    tenant_id=TENANT,
                    agent_id=AGENT,
                    agent_scopes=[],
                    connector_name=connector,
                    tool_name=tool,
                    params=params,
                    grant_token=GRANT_TOKEN,
                    run_grant=run_grant,
                )
            else:
                # Connector-qualified, as the other two paths are: bare names are covered above.
                built = build_tools_for_agent([f"{connector}:{tool}"], connector_names=[connector])
                call = {"name": built[0].name, "args": params, "id": f"call-{tool}"}
                state = {"messages": [AIMessage(content="", tool_calls=[call])], "grant_token": GRANT_TOKEN}
                result = await agent_graph.validate_tool_scopes(state, agent_graph._tool_grant_refs(built), run_grant)
            passed[tool] = "error" not in result and result.get("status") != "failed"
    return passed, dispatched, [entry for entry in logs if entry["event"] in GRANT_DENIAL_EVENTS]


@pytest.mark.parametrize("mode", MODES, ids=MODE_IDS)
@pytest.mark.parametrize("bound", ["sanctions_api", "sanctions_screening"], ids=["on old id", "on new id"])
@pytest.mark.parametrize("held", list(GRANTS), ids=["grant under old id", "grant under new id"])
@pytest.mark.parametrize("path", DISPATCH_PATHS)
async def test_a_grant_under_either_id_allows_the_tools_on_every_path(
    monkeypatch: pytest.MonkeyPatch, path: str, held: str, bound: str, mode: EnforcementMode
) -> None:
    scopes, held_tools = GRANTS[held]
    # The granted tools the bound connector has: the old id is the legacy connector, which has no
    # screen_person or screen_business, so on it a grant under the new id covers the shared tools.
    tools = [tool for tool in held_tools if tool in CONNECTOR_TOOLS[bound]]
    passed, dispatched, denials = await _dispatch(monkeypatch, path, mode, scopes, bound, tools)
    assert passed == dict.fromkeys(tools, True)
    assert denials == []  # warn lets a denied call through, so a recorded denial would hide here
    if path != "langgraph":  # the graph node only checks; the tool node dispatches afterwards
        assert dispatched == [(bound, tool) for tool in tools]


@pytest.mark.parametrize("mode", MODES, ids=MODE_IDS)
@pytest.mark.parametrize("issued", ["before the rename", "now"])
@pytest.mark.parametrize("path", DISPATCH_PATHS)
async def test_the_legacy_connectors_own_tools_stay_granted_on_every_path(
    monkeypatch: pytest.MonkeyPatch, path: str, issued: str, mode: EnforcementMode
) -> None:
    tools = sorted(LEGACY_ONLY_TOOLS)
    if issued == "now":  # as registration computes it for an agent's authorized_tools
        refs = [f"sanctions_api:{tool}" for tool in tools]
        scopes = grantex_registration._tools_to_scopes(refs, "ops", connector_names=["sanctions_api"])
    else:
        scopes = ["agenticorg:ops:read", *(f"tool:sanctions_api:read:{tool}" for tool in tools)]
    passed, dispatched, denials = await _dispatch(monkeypatch, path, mode, scopes, "sanctions_api", tools)
    assert passed == dict.fromkeys(tools, True)
    assert denials == []
    if path != "langgraph":
        assert dispatched == [("sanctions_api", tool) for tool in tools]


# A scope for one connector covers another only when ``register_deprecated`` links their ids.
UNLINKED = {
    "no scope under either id": (
        ["agenticorg:ops:read", "tool:jira:read:search_issues"],
        "sanctions_screening",
        "screen_entity",
    ),
    "another connector's scope for the tool": (
        ["agenticorg:ops:read", "tool:github:write:create_issue"],
        "jira",
        "create_issue",
    ),
}


@pytest.mark.parametrize("mode", MODES, ids=MODE_IDS)
@pytest.mark.parametrize("case", list(UNLINKED))
@pytest.mark.parametrize("path", DISPATCH_PATHS)
async def test_only_a_linked_id_counts_on_every_path(
    monkeypatch: pytest.MonkeyPatch, path: str, case: str, mode: EnforcementMode
) -> None:
    scopes, connector, tool = UNLINKED[case]
    passed, dispatched, denials = await _dispatch(monkeypatch, path, mode, scopes, connector, [tool])
    if mode is EnforcementMode.WARN:  # recorded, and the call goes on unless a legacy check refuses it
        assert [(entry["event"], entry["reason"]) for entry in denials] == [
            ("grant_enforcement_would_deny", "tool_not_granted")
        ]
        return
    assert passed == {tool: False}
    assert dispatched == []
    expected = [] if mode is EnforcementMode.OFF else [("grant_enforcement_denied", "tool_not_granted")]
    assert [(entry["event"], entry["reason"]) for entry in denials] == expected


# ── Provider neutrality ─────────────────────────────────────────────────────


def test_default_implementation_names_no_provider() -> None:
    assert SanctionsScreeningConnector.base_url == ""
    assert module.DEFAULT_PROVIDER == "mock"
    source = inspect.getsource(module)
    assert not re.search(r"https?://", source), "the connector must not point at a screening service"
    denylist = dl.load(REPO_ROOT / "config" / "denylist.sha256")
    for number, line in enumerate(source.splitlines(), start=1):
        assert denylist.matches(line) == [], f"sanctions_screening.py:{number} names a denylisted vendor"


def test_grantex_manifest_declares_every_tool_as_read() -> None:
    manifest = json.loads((REPO_ROOT / "manifests" / "sanctions_screening.json").read_text("utf-8"))
    assert manifest["connector"] == "sanctions_screening"
    assert manifest["tools"] == dict.fromkeys(sorted(TOOLS), "read")
    assert set(SanctionsScreeningConnector(config={})._tool_registry) == TOOLS


# ── Screening through the provider seam ─────────────────────────────────────


async def test_screen_entity_reports_every_candidate_the_provider_returns() -> None:
    connector = await _connected()
    listed = await connector.execute_tool("screen_entity", {"name": LISTED_PERSON, "type": "individual"})
    assert listed["provider"] == "mock"
    assert listed["hit_count"] >= 1
    [screening] = listed["screenings"]
    assert screening["subject_kind"] == "person"
    assert screening["hits"][0]["list_type"] == "sanctions"
    clean = await connector.execute_tool("screen_entity", {"name": CLEAN_NAME, "type": "entity", "min_score": 99})
    assert clean["hit_count"] == 0
    assert [s["subject_kind"] for s in clean["screenings"]] == ["business"]


async def test_an_unspecified_type_screens_both_people_and_businesses() -> None:
    connector = await _connected()
    result = await connector.execute_tool("screen_entity", {"name": LISTED_BUSINESS})
    assert sorted(s["subject_kind"] for s in result["screenings"]) == ["business", "person"]
    assert result["hit_count"] >= 1


async def test_typed_tools_screen_one_kind() -> None:
    connector = await _connected()
    person = await connector.execute_tool("screen_person", {"name": LISTED_PERSON, "nationality": "no"})
    business = await connector.execute_tool("screen_business", {"name": LISTED_BUSINESS, "jurisdiction": "GB"})
    assert [s["subject_kind"] for s in person["screenings"]] == ["person"]
    assert person["screenings"][0]["subject"]["nationalities"] == ["NO"]
    assert [s["subject_kind"] for s in business["screenings"]] == ["business"]
    assert business["hit_count"] >= 1


async def test_screen_transaction_screens_both_parties() -> None:
    connector = await _connected()
    result = await connector.execute_tool(
        "screen_transaction",
        {"sender_name": CLEAN_NAME, "receiver_name": LISTED_PERSON, "receiver_type": "individual", "amount": 10},
    )
    assert result["sender"]["hit_count"] == 0
    assert result["receiver"]["hit_count"] >= 1
    assert result["hit_count"] == result["receiver"]["hit_count"]


async def test_batch_screen_reports_each_entity() -> None:
    connector = await _connected()
    result = await connector.execute_tool(
        "batch_screen",
        {"entities": [{"name": CLEAN_NAME, "type": "entity"}, {"name": LISTED_PERSON, "type": "individual"}]},
    )
    assert [entry["name"] for entry in result["results"]] == [CLEAN_NAME, LISTED_PERSON]
    assert result["results"][0]["hit_count"] == 0
    assert result["hit_count"] == result["results"][1]["hit_count"] >= 1


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        ("screen_entity", {}),
        ("screen_entity", {"name": "  "}),
        ("screen_entity", {"name": CLEAN_NAME, "type": "trust"}),
        ("screen_entity", {"name": CLEAN_NAME, "list_types": ["gossip"]}),
        ("screen_person", {"name": CLEAN_NAME, "date_of_birth": "31/12/1980"}),
        ("screen_transaction", {"sender_name": CLEAN_NAME}),
        ("batch_screen", {}),
        ("batch_screen", {"entities": []}),
        ("batch_screen", {"entities": [{"name": CLEAN_NAME}] * 51}),
        ("batch_screen", {"entities": ["not an object"]}),
    ],
)
async def test_invalid_input_is_refused(tool: str, params: dict) -> None:
    connector = await _connected()
    with pytest.raises(ValueError):
        await connector.execute_tool(tool, params)


# ── One deadline per tool call ──────────────────────────────────────────────
#
# The provider contract (``Deadline`` in ``connectors/framework/verification_provider.py``): create
# one deadline per unit of work and pass the same one down. A tool call is the unit: 50 names
# without a type are 100 screenings, and a fresh timeout for each would let one call run for 100.

SCREENING_SECONDS = 0.05


class SlowProvider(VerificationProvider):
    """Takes ``SCREENING_SECONDS`` per screening, bounded by the deadline it is given, which it records."""

    name = "acme_kyb"
    capabilities = frozenset({Capability.SCREEN_PERSON, Capability.SCREEN_BUSINESS})

    def __init__(self) -> None:
        self.deadlines: list[Deadline] = []

    async def _work(self, capability: Capability, deadline: Deadline) -> Deadline:
        self.deadlines.append(deadline)
        async with deadline.enforce(self.name, capability):
            await asyncio.sleep(SCREENING_SECONDS)
        return deadline

    async def screen_person(self, s: PersonSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        answer_by = await self._work(Capability.SCREEN_PERSON, deadline)
        result = await MockProvider().screen_person(s, opts, deadline=answer_by)
        return result.model_copy(update={"provider": self.name})

    async def screen_business(self, s: BusinessSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        answer_by = await self._work(Capability.SCREEN_BUSINESS, deadline)
        result = await MockProvider().screen_business(s, opts, deadline=answer_by)
        return result.model_copy(update={"provider": self.name})


class DeadlineIgnoringProvider(SlowProvider):
    """Breaks the contract: takes its time whatever the deadline, so only the connector can stop it."""

    async def _work(self, capability: Capability, deadline: Deadline) -> Deadline:
        self.deadlines.append(deadline)
        await asyncio.sleep(SCREENING_SECONDS)
        return Deadline.after(60)


@pytest.mark.parametrize(
    "provider_cls", [SlowProvider, DeadlineIgnoringProvider], ids=["provider keeps it", "provider ignores it"]
)
async def test_a_batch_stops_at_the_tool_deadline_not_per_screening(provider_cls: type[SlowProvider]) -> None:
    ProviderRegistry.register_native("acme_kyb", provider_cls)
    connector = await _connected({"provider": "acme_kyb"})
    connector.timeout_ms = 300
    provider = connector._provider
    entities = [{"name": f"{CLEAN_NAME} {number}"} for number in range(module.MAX_BATCH)]  # no type: 2 each
    started = time.monotonic()
    with pytest.raises(ProviderTimeout):
        await connector.execute_tool("batch_screen", {"entities": entities})
    # A fresh 0.3 s timeout per screening never fires: all 100 would run, 5 s in all.
    assert time.monotonic() - started < 2.0
    assert 0 < len(provider.deadlines) < 2 * module.MAX_BATCH
    assert len({id(deadline) for deadline in provider.deadlines}) == 1


@pytest.mark.parametrize(
    ("tool", "params", "screenings"),
    [
        ("screen_entity", {"name": CLEAN_NAME}, 2),
        ("screen_transaction", {"sender_name": CLEAN_NAME, "receiver_name": LISTED_PERSON}, 4),
        ("batch_screen", {"entities": [{"name": CLEAN_NAME}, {"name": LISTED_PERSON, "type": "individual"}]}, 3),
    ],
)
async def test_every_screening_in_a_tool_call_shares_the_deadline_set_when_it_starts(
    tool: str, params: dict, screenings: int
) -> None:
    ProviderRegistry.register_native("acme_kyb", SlowProvider)
    connector = await _connected({"provider": "acme_kyb"})
    provider = connector._provider
    started = time.monotonic()
    await connector.execute_tool(tool, params)
    finished = time.monotonic()
    assert len(provider.deadlines) == screenings
    [deadline] = {id(deadline): deadline for deadline in provider.deadlines}.values()
    budget = connector.timeout_ms / 1000
    assert started + budget <= deadline.expires_at <= finished + budget
    assert deadline.expires_at < started + budget + SCREENING_SECONDS  # not restarted by a later screening


# ── Failing closed ──────────────────────────────────────────────────────────


async def test_the_mock_is_refused_outside_local_and_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", "production")
    connector = SanctionsScreeningConnector(config={})
    with pytest.raises(ScreeningUnavailableError, match="unavailable"):
        await connector.connect()
    health = await connector.health_check()
    assert health["status"] == "not_configured"
    with pytest.raises(ScreeningUnavailableError, match="not connected"):
        await connector.execute_tool("screen_entity", {"name": LISTED_PERSON})


@pytest.mark.parametrize("provider", ["nobody", "Acme KYB", "../mock"])
async def test_an_unknown_or_malformed_provider_fails_closed(provider: str) -> None:
    with pytest.raises(ScreeningUnavailableError):
        await _connected({"provider": provider})


async def test_a_provider_without_screening_fails_closed() -> None:
    ProviderRegistry.register_native("acme_kyb", ResolveOnlyProvider)
    with pytest.raises(ScreeningUnavailableError, match="no screening"):
        await _connected({"provider": "acme_kyb"})


async def test_a_provider_that_screens_only_people_refuses_to_guess_the_kind() -> None:
    ProviderRegistry.register_native("acme_kyb", PersonOnlyProvider)
    connector = await _connected({"provider": "acme_kyb"})
    result = await connector.execute_tool("screen_entity", {"name": LISTED_PERSON, "type": "individual"})
    assert result["provider"] == "acme_kyb"
    assert result["hit_count"] >= 1
    with pytest.raises(CapabilityNotSupported):
        await connector.execute_tool("screen_entity", {"name": LISTED_PERSON})
    with pytest.raises(CapabilityNotSupported):
        await connector.execute_tool("screen_business", {"name": LISTED_BUSINESS})


async def test_health_names_the_provider_without_claiming_it_is_reachable() -> None:
    # The seam has no probe, so a provider with wrong credentials builds just as well as a working
    # one. Reporting ``healthy`` would mark the connector active on a guess (UI-HEALTH-404).
    connector = await _connected()
    health = await connector.health_check()
    assert health["status"] == "configured"
    assert health["provider"] == "mock"
    assert "not contacted" in health["reason"]
