# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — stored Grantex scopes never run ahead of the agent's registration.

* ``PATCH /agents/{id}`` pushes the recomputed scopes of a registered agent to
  Grantex (``agents.update``, off the event loop) before storing them; any
  failure refuses the PATCH with a reason code and changes nothing;
* scope lists are de-duplicated and capped at 100 with a clear error;
* the token pool delegates only the stored scopes the registration also
  carries, and refuses to mint when it cannot read the registration;
* the backfill writes only ``config.grantex.grantex_scopes`` on live agents,
  and reports a failure per agent instead of stopping.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from structlog.testing import capture_logs

from auth.grantex_registration import MAX_AGENT_SCOPES, ScopeLimitExceededError, bounded_scopes

TENANT = str(uuid.UUID(int=0x1F9A))
ROOT = "placeholder-root-grant"  # noqa: S105 - not a credential
OLD = ["agenticorg:sales:read", "tool:hubspot:execute:list_contacts"]
NEW = ["agenticorg:sales:read", "tool:hubspot:read:list_contacts"]


# ── bounded_scopes ────────────────────────────────────────────────────────


def test_scopes_are_deduplicated_in_order():
    assert bounded_scopes(["a", "b", "a", "", "c", "b"]) == ["a", "b", "c"]


def test_at_most_one_hundred_distinct_scopes_are_accepted():
    assert len(bounded_scopes([f"s{i}" for i in range(MAX_AGENT_SCOPES)] * 2)) == MAX_AGENT_SCOPES
    with pytest.raises(ScopeLimitExceededError, match="101 distinct Grantex scopes; at most 100") as exc:
        bounded_scopes([f"s{i}" for i in range(MAX_AGENT_SCOPES + 1)])
    assert exc.value.count == MAX_AGENT_SCOPES + 1


# ── PATCH /agents/{id} ────────────────────────────────────────────────────


def _agent(config: dict[str, Any]) -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.UUID(int=0xA1)
    agent.tenant_id = uuid.UUID(TENANT)
    agent.company_id = None
    agent.domain = "sales"
    agent.status = "shadow"
    agent.visibility = "tenant"
    agent.owner_user_id = None
    agent.connector_ids = []
    agent.authorized_tools = ["tool_that_was_there"]
    agent.system_prompt_text = "prompt"
    agent.config = config
    return agent


def _registered(scopes: list[str] | None = None) -> dict[str, Any]:
    return {
        "grantex": {"grantex_agent_id": "ag_1", "grantex_did": "did:x", "grantex_scopes": list(scopes or OLD)},
        "k": 1,
    }


async def _patch(agent: MagicMock, tools: list[str], *, client: Any, scopes: list[str] | None = None) -> dict[str, Any]:
    from api.v1 import agents

    result = MagicMock()
    result.scalar_one_or_none.return_value = agent
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    body = MagicMock()
    body.model_dump.return_value = {"authorized_tools": tools}
    with (
        patch.object(agents, "get_tenant_session", return_value=ctx),
        patch.object(agents, "_validate_authorized_tools", return_value=[]),
        patch.object(agents, "_resolve_connector_configs", AsyncMock(return_value=({}, ["hubspot"]))),
        patch("auth.grantex_registration._tools_to_scopes", return_value=list(scopes if scopes is not None else NEW)),
        patch("auth.grantex_registration._get_grantex_client", return_value=client),
    ):
        return await agents.update_agent(agent_id=agent.id, body=body, tenant_id=TENANT)


async def test_patch_updates_the_grantex_registration_off_the_loop_before_storing_the_scopes():
    agent = _agent(_registered())
    client = MagicMock()
    stored_at_update: list[list[str]] = []
    client._http.patch.side_effect = lambda *_a, **_k: stored_at_update.append(
        list(agent.config["grantex"]["grantex_scopes"])
    )
    to_thread = AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k))
    with patch("api.v1.agents.asyncio.to_thread", to_thread):
        assert (await _patch(agent, ["list_contacts"], client=client))["updated"] is True
    assert to_thread.await_args.args[0].__name__ == "update_agent_scopes"
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", {"scopes": NEW})
    assert stored_at_update == [OLD]  # Grantex first, storage after
    assert agent.config["grantex"] == {"grantex_agent_id": "ag_1", "grantex_did": "did:x", "grantex_scopes": NEW}
    assert agent.config["k"] == 1


@pytest.mark.parametrize(
    ("client", "status", "reason_code"),
    [
        (MagicMock(**{"_http.patch.side_effect": RuntimeError("refused")}), 502, "grantex_update_failed"),
        (None, 503, "grantex_unconfigured"),
    ],
)
async def test_patch_refuses_and_keeps_the_stored_scopes_when_grantex_does_not_take_them(client, status, reason_code):
    agent = _agent(_registered())
    with capture_logs() as logs, pytest.raises(HTTPException) as exc:
        await _patch(agent, ["list_contacts"], client=client)
    assert exc.value.status_code == status
    assert exc.value.detail["reason_code"] == reason_code
    assert agent.config["grantex"]["grantex_scopes"] == OLD
    assert [e["reason_code"] for e in logs if e["event"] == "grantex_scopes_refresh_failed"] == [reason_code]


async def test_patch_refuses_more_scopes_than_a_registration_carries():
    agent = _agent(_registered())
    client = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await _patch(agent, ["many"], client=client, scopes=[f"tool:hubspot:read:t{i}" for i in range(101)])
    assert (exc.value.status_code, exc.value.detail["reason_code"]) == (422, "scope_limit_exceeded")
    assert "at most 100" in exc.value.detail["message"]
    client._http.patch.assert_not_called()
    assert agent.config["grantex"]["grantex_scopes"] == OLD


async def test_patch_of_a_registered_agent_refuses_when_its_scopes_cannot_be_computed():
    from api.v1 import agents

    agent = _agent(_registered())
    client = MagicMock()
    with (
        patch.object(agents, "_resolve_connector_configs", AsyncMock(side_effect=RuntimeError("bindings"))),
        pytest.raises(HTTPException) as exc,
    ):
        result = MagicMock()
        result.scalar_one_or_none.return_value = agent
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        body = MagicMock()
        body.model_dump.return_value = {"authorized_tools": ["list_contacts"]}
        with (
            patch.object(agents, "get_tenant_session", return_value=ctx),
            patch.object(agents, "_validate_authorized_tools", return_value=[]),
            patch("auth.grantex_registration._get_grantex_client", return_value=client),
        ):
            await agents.update_agent(agent_id=agent.id, body=body, tenant_id=TENANT)
    assert (exc.value.status_code, exc.value.detail["reason_code"]) == (503, "scope_computation_failed")
    client._http.patch.assert_not_called()


async def test_patch_with_unchanged_scopes_does_not_call_grantex():
    agent = _agent(_registered(NEW))
    client = MagicMock()
    await _patch(agent, ["list_contacts"], client=client)
    client._http.patch.assert_not_called()
    assert agent.config["grantex"]["grantex_scopes"] == NEW


async def test_patch_of_an_unregistered_agent_stores_scopes_without_grantex():
    agent = _agent({"k": 1})
    await _patch(agent, ["list_contacts"], client=None)
    assert agent.config == {"k": 1, "grantex": {"grantex_scopes": NEW}}


# ── Token pool: delegate only registered scopes ───────────────────────────


def _pool_client(registered: Any) -> MagicMock:
    client = MagicMock()
    if isinstance(registered, Exception):
        client.agents.get.side_effect = registered
    else:
        client.agents.get.return_value = SimpleNamespace(scopes=registered)
    client.grants.delegate.return_value = {
        "grantToken": "placeholder-run-grant",
        "grantId": "grnt_1",
        "expiresAt": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
    }
    return client


async def _mint(client: MagicMock, scopes: list[str], monkeypatch) -> Any:
    from auth.token_pool import TokenPool
    from core.config import external_keys

    monkeypatch.setattr(external_keys, "grantex_root_grant_token", ROOT)
    pool = TokenPool(grantex_client_factory=lambda: client)
    return await pool._mint_run_grant(grantex_agent_id="ag_1", scopes=scopes, ttl_seconds=900)


async def test_pool_delegates_the_stored_scopes_the_registration_also_carries(monkeypatch):
    client = _pool_client(("tool:hubspot:read:list_contacts", "agenticorg:sales:read", "tool:other:read:x"))
    stored = ["tool:hubspot:write:create_contact", "agenticorg:sales:read", "tool:hubspot:read:list_contacts"]
    with capture_logs() as logs:
        grant = await _mint(client, stored, monkeypatch)
    assert grant.token == "placeholder-run-grant"
    assert client.grants.delegate.call_args.kwargs["scopes"] == [
        "agenticorg:sales:read",
        "tool:hubspot:read:list_contacts",
    ]
    [warning] = [e for e in logs if e["event"] == "run_grant_scopes_not_registered"]
    assert (warning["stored_count"], warning["delegated_count"]) == (3, 2)


@pytest.mark.parametrize(
    ("registered", "sub_reason"),
    [
        (("tool:other:read:x",), "agent_not_registered"),
        (RuntimeError("unreachable"), "mint_failed"),
        (None, "mint_failed"),
    ],
)
async def test_pool_refuses_to_mint_without_a_registered_scope_in_common(registered, sub_reason, monkeypatch):
    from auth.token_pool import GrantMintError

    client = _pool_client(registered)
    with pytest.raises(GrantMintError) as exc:
        await _mint(client, ["tool:hubspot:read:list_contacts"], monkeypatch)
    assert exc.value.sub_reason == sub_reason
    client.grants.delegate.assert_not_called()


# ── Backfill ─────────────────────────────────────────────────────────────


def test_backfill_statement_sets_only_the_scope_key_on_a_live_registered_agent():
    from sqlalchemy.dialects import postgresql

    from scripts.refresh_grantex_scopes import scope_update_statement

    statement = scope_update_statement(uuid.UUID(TENANT), uuid.UUID(int=0xA1), NEW)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "SET config=jsonb_set(agents.config, " in sql
    assert "agents.status != " in sql and "agents.config ? " in sql
    assert ["grantex", "grantex_scopes"] in compiled.params.values()
    assert "deleted" in compiled.params.values() and NEW in compiled.params.values()


async def _refresh(*, scopes: list[str] | None = None, persist: AsyncMock | None = None, client: Any = None):
    from scripts.refresh_grantex_scopes import refresh_agent_scopes

    persist = persist or AsyncMock()
    client = client or MagicMock()
    with patch("auth.grantex_registration._tools_to_scopes", return_value=list(scopes if scopes is not None else NEW)):
        result = await refresh_agent_scopes(
            agent_id="agent-1",
            domain="sales",
            authorized_tools=["list_contacts"],
            config=_registered(),
            connector_names=["hubspot"],
            grantex_client=client,
            apply=True,
            persist=persist,
        )
    return result, client, persist


async def test_backfill_reports_a_storage_failure_for_the_agent():
    result, client, _ = await _refresh(persist=AsyncMock(side_effect=RuntimeError("db down")))
    assert result.outcome == "storage_failed"
    client._http.patch.assert_called_once()
    assert json.loads(result.as_json())["error"] == "storage write failed: RuntimeError"


async def test_backfill_deduplicates_and_reports_an_agent_over_the_scope_limit():
    result, client, persist = await _refresh(scopes=NEW + NEW)
    assert persist.await_args.args[0] == NEW
    result, client, persist = await _refresh(scopes=[f"tool:hubspot:read:t{i}" for i in range(101)])
    assert result.outcome == "scope_limit_exceeded" and "at most 100" in result.error
    client._http.patch.assert_not_called()
    persist.assert_not_awaited()


class _Rows:
    def __init__(self, rows: list[Any], rowcount: int = 1) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def scalars(self) -> _Rows:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, agents: list[Any], log: dict[str, Any]) -> None:
        self.agents = agents
        self.log = log

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def execute(self, statement: Any) -> _Rows:
        from sqlalchemy.sql.dml import Update

        if isinstance(statement, Update):
            self.log["writes"].append(statement)
            outcomes = self.log["write_outcomes"]
            outcome = outcomes.pop(0) if outcomes else 1
            if isinstance(outcome, Exception):
                raise outcome
            return _Rows([], rowcount=outcome)
        self.log["selects"].append(statement)
        return _Rows(self.agents)


def _row(grantex_agent_id: str, connector_ids: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        domain="sales",
        authorized_tools=["list_contacts"],
        config={"grantex": {"grantex_agent_id": grantex_agent_id, "grantex_scopes": OLD}},
        connector_ids=connector_ids or [],
        company_id=None,
    )


def _run(
    agents: list[Any], *, write_outcomes: list[Any], resolve: AsyncMock | None = None, capsys
) -> tuple[int, dict, list]:
    from scripts import refresh_grantex_scopes as script

    log: dict[str, Any] = {"writes": [], "selects": [], "write_outcomes": list(write_outcomes)}
    client = MagicMock()
    with (
        patch("auth.grantex_registration._get_grantex_client", return_value=client),
        patch("auth.grantex_registration._tools_to_scopes", return_value=list(NEW)),
        patch("core.database.get_tenant_session", lambda *_a, **_k: _Session(agents, log)),
        patch("api.v1.agents._resolve_connector_configs", resolve or AsyncMock(return_value=({}, ["hubspot"]))),
        patch.object(script, "_tenant_ids", AsyncMock(return_value=[uuid.UUID(TENANT)])),
    ):
        code = asyncio.run(script.run(script.build_parser().parse_args(["--tenant", TENANT, "--apply"])))
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    return code, log, lines


def test_backfill_command_continues_after_per_agent_failures(capsys):
    agents = [_row("ag_1"), _row("ag_2"), _row("ag_3"), _row("ag_4", ["registry-hubspot"])]
    resolve = AsyncMock(side_effect=RuntimeError("bindings unreadable"))
    code, log, lines = _run(agents, write_outcomes=[RuntimeError("db down"), 0, 1], resolve=resolve, capsys=capsys)
    assert code == 1
    assert [line["outcome"] for line in lines[:4]] == [
        "storage_failed",  # the write raised
        "storage_failed",  # the row was deleted or lost its Grantex config meanwhile
        "updated",
        "connector_lookup_failed",
    ]
    assert lines[-1]["summary"] == {"storage_failed": 2, "updated": 1, "connector_lookup_failed": 1}


def test_backfill_command_selects_only_agents_that_are_not_deleted(capsys):
    from sqlalchemy.dialects import postgresql

    _, log, _ = _run([], write_outcomes=[], capsys=capsys)
    [select] = log["selects"]
    compiled = select.compile(dialect=postgresql.dialect())
    assert "agents.status != " in str(compiled) and "deleted" in compiled.params.values()


# ── Grantex scope update (compatibility PATCH) ────────────────────────────


def test_scope_update_sends_a_patch_because_the_sdk_update_posts_to_an_unserved_route():
    from auth.grantex_registration import update_agent_scopes

    client = MagicMock()
    update_agent_scopes(client, "ag/1", NEW)
    client._http.patch.assert_called_once_with("/v1/agents/ag%2F1", {"scopes": NEW})
    client.agents.update.assert_not_called()


def test_scope_update_fails_when_the_client_cannot_send_a_patch():
    from auth.grantex_registration import update_agent_scopes

    with pytest.raises(RuntimeError, match="cannot send PATCH"):
        update_agent_scopes(SimpleNamespace(_http=SimpleNamespace()), "ag_1", NEW)
