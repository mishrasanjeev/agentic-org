# SPDX-License-Identifier: Apache-2.0
"""Sample governed cases seed (scripts/seed_governed_cases.py), without a database or Grantex."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import seed_governed_cases as seed
from scripts.seed_dev import SeedError


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _Session:
    """Enough session for the flag upsert: no row exists, so one is added."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushes = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result(None)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flushes += 1


class _Runtime:
    def __init__(self) -> None:
        self.session = _Session()
        self.llm_model = ""

    def session_factory(self, _tenant_id: uuid.UUID) -> Any:
        @asynccontextmanager
        async def _open() -> Any:
            yield self.session

        return _open()

    def clock(self) -> Any:
        from datetime import UTC, datetime

        return datetime.now(UTC)


def test_fixture_list_is_parsed_and_an_empty_list_is_refused() -> None:
    assert seed.parse_fixtures(" gb-clean-brightwater , us-thin-file-brambleway ") == (
        "gb-clean-brightwater",
        "us-thin-file-brambleway",
    )
    with pytest.raises(SeedError):
        seed.parse_fixtures(" , ")


def test_unknown_fixture_is_refused_before_anything_is_written() -> None:
    with pytest.raises(SeedError, match="unknown mock fixture"):
        seed.load_applications(["gb-clean-brightwater", "not-a-fixture"])


def test_default_fixtures_cover_the_scenarios_the_console_screens_show() -> None:
    applications = seed.load_applications(seed.DEFAULT_FIXTURES)
    assert set(applications) == set(seed.DEFAULT_FIXTURES)
    for application in applications.values():
        assert application["legal_name"]
        assert application["jurisdiction"][:2] in {"GB", "US"}


async def test_seed_enables_the_flag_then_investigates_and_disposes_each_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.cases import runtime as case_runtime
    from core.cases import store as case_store

    created: list[str] = []
    investigated: list[str] = []
    disposed: list[str] = []

    async def fake_create_case(_session: Any, **kwargs: Any) -> Any:
        ref = f"case_{len(created):024x}"
        created.append(kwargs["application"]["legal_name"])
        return type("Case", (), {"case_ref": ref})()

    async def fake_investigate(_tenant: Any, case_ref: str, **_kwargs: Any) -> dict[str, Any]:
        investigated.append(case_ref)
        return {"state": "awaiting_decision", "tier": "medium", "screening_hits": 1}

    async def fake_dispose(_tenant: Any, case_ref: str, **_kwargs: Any) -> dict[str, Any]:
        disposed.append(case_ref)
        return {"proposed": 1, "outcomes": ["false_positive"], "failed": []}

    monkeypatch.setattr(case_store, "create_case", fake_create_case)
    monkeypatch.setattr(case_runtime, "investigate_case", fake_investigate)
    monkeypatch.setattr(case_runtime, "dispose_screening_hits", fake_dispose)

    runtime = _Runtime()
    summary = await seed.seed_cases(["gb-clean-brightwater", "us-false-positive-oakhollow"], runtime=runtime)

    flag = runtime.session.added[0]
    assert (flag.flag_key, flag.enabled, flag.rollout_percentage) == (seed.FLAG_KEY, True, 100)
    assert len(created) == 2
    assert investigated == disposed
    assert summary["cases"]["gb-clean-brightwater"]["dispositions_proposed"] == 1
    assert summary["tenant_id"] == str(seed.seed_id("tenant"))


async def test_a_refused_disposition_is_reported_and_does_not_stop_the_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.cases import runtime as case_runtime
    from core.cases import store as case_store
    from core.cases.states import CaseError

    async def fake_create_case(_session: Any, **_kwargs: Any) -> Any:
        return type("Case", (), {"case_ref": "case_000000000000000000000001"})()

    async def fake_investigate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"state": "awaiting_decision", "screening_hits": 2}

    async def fake_dispose(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise CaseError("transition_not_allowed")

    monkeypatch.setattr(case_store, "create_case", fake_create_case)
    monkeypatch.setattr(case_runtime, "investigate_case", fake_investigate)
    monkeypatch.setattr(case_runtime, "dispose_screening_hits", fake_dispose)

    summary = await seed.seed_cases(["gb-clean-brightwater"], runtime=_Runtime())
    assert summary["cases"]["gb-clean-brightwater"]["dispositions_refused"] == "transition_not_allowed"


@pytest.mark.parametrize("runtime_env", ["", "production", "staging"])
def test_the_seed_refuses_a_production_like_runtime(runtime_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("must not run against a production-like runtime")

    monkeypatch.setattr(seed.asyncio, "run", no_run)
    assert seed.main([], {"AGENTICORG_ENV": runtime_env}) == 2


# ── Case agents and the development root grant ───────────────────────────────
#
# Every governed-case provider call is checked against a delegated grant for
# the one active, shared agent of its role (core/cases/grant_authorizer.py).
# Without those agents every sample case ended `failed` on
# `tool_refused:grant_missing`, so the seed now provides them.


class _ApiError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _Agents:
    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}
        self.fail_get_with: int | None = None

    def register(self, *, name: str, scopes: list[str], description: str = "") -> Any:
        agent = SimpleNamespace(id=f"ag_{len(self.registered) + 1:04d}", name=name, scopes=list(scopes), did="")
        self.registered[agent.id] = agent
        return agent

    def get(self, agent_id: str) -> Any:
        if self.fail_get_with is not None:
            raise _ApiError(self.fail_get_with)
        if agent_id not in self.registered:
            raise _ApiError(404)
        return self.registered[agent_id]

    def list(self) -> Any:
        return SimpleNamespace(agents=list(self.registered.values()))


class _Tokens:
    def __init__(self) -> None:
        self.exchanged: list[Any] = []

    def exchange(self, params: Any) -> Any:
        self.exchanged.append(params)
        return SimpleNamespace(grant_token=f"root-grant-for-{params.agent_id}", grant_id="grnt_root")


class _Grantex:
    """The platform's Grantex client, as far as the seed uses it; ``sandbox`` approves at once."""

    def __init__(self, *, sandbox: bool = True) -> None:
        self.agents = _Agents()
        self.tokens = _Tokens()
        self.sandbox = sandbox
        self.authorized: list[Any] = []

    def authorize(self, params: Any) -> Any:
        self.authorized.append(params)
        return SimpleNamespace(code="code-1" if self.sandbox else None, consent_url="https://issuer.example/consent")


def test_case_agent_roles_are_the_reference_agents_with_their_own_tool_sets() -> None:
    from core.agents.business_underwriter.agent import TOOL_SET as UNDERWRITER_TOOLS
    from core.agents.screening_disposition.agent import TOOL_SET as DISPOSITION_TOOLS
    from core.cases.grant_authorizer import CASE_AGENT_ROLES

    roles = {role.agent_type: role for role in seed.case_agent_roles()}
    assert set(roles) == set(CASE_AGENT_ROLES)
    assert roles["business_underwriter"].tools == tuple(sorted(UNDERWRITER_TOOLS))
    assert roles["screening_disposition"].tools == tuple(sorted(DISPOSITION_TOOLS))
    for role in roles.values():
        assert (Path(__file__).resolve().parents[2] / role.prompt_ref).is_file()


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"GRANTEX_API_KEY": "k"},
        # The platform client falls back to the hosted issuer when no URL is named.
        {"GRANTEX_BASE_URL": "", "GRANTEX_API_KEY": "k"},
        {"GRANTEX_BASE_URL": "https://issuer.example", "GRANTEX_API_KEY": "k"},
        {"GRANTEX_BASE_URL": "http://grantex:3001?host=issuer.example", "GRANTEX_API_KEY": "k"},
        {"GRANTEX_BASE_URL": "http://grantex:3001"},
    ],
)
def test_the_seed_refuses_any_grantex_but_the_stacks_own(environ: dict[str, str]) -> None:
    with pytest.raises(SeedError, match="GRANTEX_"):
        seed.assert_local_grantex(environ)


@pytest.mark.parametrize("url", ["http://grantex:3001", "http://127.0.0.1:13001/", "http://localhost:3001"])
def test_the_seed_accepts_the_stacks_grantex(url: str) -> None:
    seed.assert_local_grantex({"GRANTEX_BASE_URL": url, "GRANTEX_API_KEY": "k"})


def test_main_refuses_before_anything_is_written_when_grantex_is_not_the_stacks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("must not seed against an issuer nobody chose")

    monkeypatch.setattr(seed.asyncio, "run", no_run)
    assert seed.main([], {"AGENTICORG_ENV": "development"}) == 2
    assert "GRANTEX_BASE_URL" in capsys.readouterr().err


def test_the_root_grant_covers_every_case_agent_scope_and_is_issued_to_the_seed_principal() -> None:
    client = _Grantex()
    scopes = ["tool:mock:read:resolve_business", "tool:mock:read:screen_person"]
    token = seed.issue_root_grant(client, scopes)

    [holder] = client.agents.registered.values()
    assert holder.name == seed.ROOT_GRANT_HOLDER and holder.scopes == scopes
    [request] = client.authorized
    assert (request.agent_id, request.user_id, list(request.scopes)) == (holder.id, seed.ROOT_GRANT_PRINCIPAL, scopes)
    assert request.expires_in == seed.ROOT_GRANT_LIFETIME
    assert client.tokens.exchanged[0].code == "code-1"
    assert token == f"root-grant-for-{holder.id}"

    # A second run reuses the holder instead of registering another agent.
    seed.issue_root_grant(client, scopes)
    assert len(client.agents.registered) == 1


def test_a_live_developer_key_is_refused_because_only_a_person_can_consent_for_it() -> None:
    client = _Grantex(sandbox=False)
    with pytest.raises(SeedError, match="sandbox"):
        seed.issue_root_grant(client, ["tool:mock:read:resolve_business"])
    assert client.tokens.exchanged == []


def _stored(client: _Grantex, tools: tuple[str, ...]) -> tuple[dict[str, Any], list[str]]:
    scopes = [f"tool:mock:read:{tool}" for tool in tools]
    agent = client.agents.register(name="Underwriter (business_underwriter)", scopes=scopes)
    return {"grantex_agent_id": agent.id, "grantex_did": "", "grantex_scopes": scopes}, list(tools)


def test_a_registration_grantex_still_holds_is_reused() -> None:
    client = _Grantex()
    tools = ("resolve_business", "screen_person")
    config, stored_tools = _stored(client, tools)
    assert seed.current_registration(client, config, tools, stored_tools) == config


@pytest.mark.parametrize("change", ["forgotten", "scopes", "tools", "unregistered"])
def test_a_registration_that_no_longer_matches_is_replaced(change: str) -> None:
    client = _Grantex()
    tools = ("resolve_business", "screen_person")
    config, stored_tools = _stored(client, tools)
    if change == "forgotten":  # the auth service's database was reset
        client.agents.registered.clear()
    elif change == "scopes":
        client.agents.registered[config["grantex_agent_id"]].scopes = ["tool:mock:read:resolve_business"]
    elif change == "tools":
        stored_tools = ["resolve_business"]
    else:
        config = {}
    assert seed.current_registration(client, config, tools, stored_tools) is None


def test_an_unreadable_registration_fails_the_seed_instead_of_registering_again() -> None:
    client = _Grantex()
    config, stored_tools = _stored(client, ("resolve_business",))
    client.agents.fail_get_with = 503
    with pytest.raises(SeedError, match="cannot read"):
        seed.current_registration(client, config, ("resolve_business",), stored_tools)


class _AgentSession:
    """Enough session for the agent upsert: rows by id, and no conflicting row."""

    def __init__(self) -> None:
        self.rows: dict[Any, Any] = {}

    async def get(self, _model: Any, row_id: Any) -> Any:
        return self.rows.get(row_id)

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    def add(self, row: Any) -> None:
        self.rows[row.id] = row

    async def flush(self) -> None:
        return None


def _agent_sessions() -> tuple[_AgentSession, Any]:
    session = _AgentSession()

    @asynccontextmanager
    async def _open(_tenant_id: uuid.UUID) -> Any:
        yield session

    return session, _open


async def test_prepare_registers_both_case_agents_allows_the_case_purpose_and_holds_the_root_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import external_keys

    monkeypatch.setattr(external_keys, "grantex_root_grant_token", "")
    client = _Grantex()
    registered: list[dict[str, Any]] = []

    def register(**kwargs: Any) -> dict[str, Any]:
        registered.append(kwargs)
        scopes = [f"tool:mock:read:{tool}" for tool in kwargs["authorized_tools"]]
        agent = client.agents.register(name=kwargs["name"], scopes=scopes)
        return {"grantex_agent_id": agent.id, "grantex_did": "", "grantex_scopes": scopes}

    session, sessions = _agent_sessions()
    tenant = seed.seed_id("tenant")
    agents = await seed.prepare_case_agents(tenant, grantex=client, register=register, session_factory=sessions)

    assert set(agents) == {"business_underwriter", "screening_disposition"}
    assert [r["agent_type"] for r in registered] == ["business_underwriter", "screening_disposition"]
    for role in seed.case_agent_roles():
        row = session.rows[seed.seed_id(f"agent:case:{role.agent_type}")]
        assert str(row.id) == agents[role.agent_type]
        assert (row.tenant_id, row.agent_type) == (tenant, role.agent_type)
        assert (row.status, row.visibility) == ("active", "tenant")
        assert row.owner_user_id is None and row.company_id is None
        assert row.authorized_tools == list(role.tools)
        grantex = row.config["grantex"]
        assert grantex["case_purposes"] == [seed.CASE_PURPOSE]
        assert grantex["grantex_scopes"] == [f"tool:mock:read:{tool}" for tool in role.tools]
        assert "grant_token" not in grantex

    # The root grant is held in this process only, and covers both roles' scopes.
    root = client.authorized[0]
    assert set(root.scopes) == {f"tool:mock:read:{tool}" for role in seed.case_agent_roles() for tool in role.tools}
    assert external_keys.grantex_root_grant_token.startswith("root-grant-for-")
    assert "root-grant" not in repr(agents)

    # Run again: nothing is registered twice.
    await seed.prepare_case_agents(tenant, grantex=client, register=register, session_factory=sessions)
    assert len(registered) == 2


async def test_a_failed_registration_fails_the_seed_rather_than_leaving_cases_to_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import external_keys

    monkeypatch.setattr(external_keys, "grantex_root_grant_token", "")
    _session, sessions = _agent_sessions()
    with pytest.raises(SeedError, match="could not register"):
        await seed.prepare_case_agents(
            seed.seed_id("tenant"), grantex=_Grantex(), register=lambda **_kwargs: None, session_factory=sessions
        )
    assert external_keys.grantex_root_grant_token == ""
