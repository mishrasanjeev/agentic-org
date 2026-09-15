# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — lifecycle of per-run grants and keeping enforcement from being skipped.

Acceptance criteria covered here:

* the pool never hands out a grant with less than max(120 s, 10% of its
  lifetime) left, and a run swaps in a fresh grant before its grant expires;
* at most one grant is minted per tenant, agent and scope set per process,
  including under concurrency and with Redis unavailable;
* the pool creates Redis clients lazily per event loop and ``init`` never
  blocks or fails on Redis; the API starts it in its lifespan;
* every graph builder requires ``run_grant``; production code never passes the
  test sentinel;
* grant tokens are redacted from LangSmith traces and sealed in checkpoints.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import NO_RUN_GRANT_FOR_TESTS, RunGrant, refresh_run_grant
from auth.token_pool import RunGrantToken, TokenPool, min_remaining_seconds

REPO = pathlib.Path(__file__).resolve().parents[2]
TENANT = str(uuid.UUID(int=0x1F3A))
AGENT = str(uuid.UUID(int=0xA7E))
ROOT = "placeholder-root-grant"  # noqa: S105 - not a credential
SCOPES = ["tool:hubspot:read:get_contact"]


@pytest.fixture
def root_grant(monkeypatch):
    from core.config import external_keys

    monkeypatch.setattr(external_keys, "grantex_root_grant_token", ROOT)


def _client(lifetime: timedelta = timedelta(minutes=15)) -> MagicMock:
    client = MagicMock()
    counter = {"n": 0}

    def _delegate(**_: Any) -> dict[str, Any]:
        counter["n"] += 1
        return {
            "grantToken": f"placeholder-minted-{counter['n']}",
            "grantId": f"grnt_placeholder_{counter['n']}",
            "expiresAt": (datetime.now(UTC) + lifetime).isoformat().replace("+00:00", "Z"),
        }

    client.grants.delegate.side_effect = _delegate
    return client


class _DownRedis:
    async def get(self, *_: Any) -> None:
        import redis.asyncio as aioredis

        raise aioredis.ConnectionError("redis down")

    async def setex(self, *_: Any) -> None:
        import redis.asyncio as aioredis

        raise aioredis.ConnectionError("redis down")


def test_min_remaining_is_the_larger_of_two_minutes_and_ten_percent():
    assert min_remaining_seconds(900) == 120
    assert min_remaining_seconds(3_600) == 360
    assert min_remaining_seconds(0) == 120


def test_a_grant_below_the_minimum_remaining_lifetime_is_not_usable():
    now = time.time()
    assert RunGrantToken("t", "g", now + 121, "minted", 900).usable(now)
    assert not RunGrantToken("t", "g", now + 119, "minted", 900).usable(now)
    assert not RunGrantToken("t", "g", now + 300, "minted", 3_600).usable(now)
    assert not RunGrantToken("t", "g", None, "minted", 900).usable(now)


async def test_concurrent_runs_mint_one_grant_per_tenant_agent_and_scopes(root_grant):
    client = _client()
    pool = TokenPool(grantex_client_factory=lambda: client)
    pool.lazy_redis = False

    grants = await asyncio.gather(
        *(
            pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, grantex_agent_id="ag_1", scopes=SCOPES)
            for _ in range(8)
        )
    )

    assert client.grants.delegate.call_count == 1
    assert {g.token for g in grants} == {"placeholder-minted-1"}


async def test_redis_unavailable_still_mints_once_per_key(root_grant):
    client = _client()
    pool = TokenPool(grantex_client_factory=lambda: client)
    pool.redis = _DownRedis()  # type: ignore[assignment]

    first = await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, grantex_agent_id="ag_1", scopes=SCOPES)
    second = await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, grantex_agent_id="ag_1", scopes=SCOPES)

    assert (first.source, second.source) == ("minted", "pool_cache")
    assert client.grants.delegate.call_count == 1


async def test_a_short_lived_grant_is_used_once_but_never_shared(root_grant):
    client = _client(lifetime=timedelta(seconds=60))
    pool = TokenPool(grantex_client_factory=lambda: client)
    pool.lazy_redis = False

    await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, grantex_agent_id="ag_1", scopes=SCOPES)
    await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, grantex_agent_id="ag_1", scopes=SCOPES)

    assert client.grants.delegate.call_count == 2


async def test_a_delegation_without_an_expiry_is_refused_rather_than_minted_per_call(root_grant):
    from auth.token_pool import GrantMintError

    client = MagicMock()
    client.grants.delegate.return_value = {"grantToken": "placeholder-no-expiry", "grantId": "grnt_x"}
    pool = TokenPool(grantex_client_factory=lambda: client)
    pool.lazy_redis = False
    with pytest.raises(GrantMintError) as err:
        await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, grantex_agent_id="ag_1", scopes=SCOPES)
    assert err.value.sub_reason == "mint_failed"


def test_the_in_process_cache_is_bounded(root_grant, monkeypatch):
    from auth import token_pool as tp

    monkeypatch.setattr(tp, "_LOCAL_CACHE_MAX", 3)
    client = _client()
    pool = TokenPool(grantex_client_factory=lambda: client)
    pool.lazy_redis = False

    async def _mint_many() -> None:
        for n in range(6):
            await pool.get_run_grant_token(
                tenant_id=TENANT, agent_id=str(uuid.UUID(int=n + 1)), grantex_agent_id="ag_1", scopes=SCOPES
            )

    asyncio.run(_mint_many())
    assert len(pool._local_grants) == 3


async def test_lazy_redis_client_is_created_once_per_event_loop_without_connecting():
    pool = TokenPool()
    created: list[object] = []

    def _new() -> object:
        created.append(object())
        return created[-1]

    with patch.object(TokenPool, "_new_redis_client", staticmethod(_new)):
        first = pool._redis_client()
        again = pool._redis_client()
    assert first is again and len(created) == 1


async def test_init_never_fails_or_blocks_when_redis_is_down(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:9/0")
    pool = TokenPool()
    await asyncio.wait_for(pool.init(), timeout=1)
    assert pool.redis is not None
    await asyncio.sleep(0)
    await pool.close()


async def test_init_survives_a_redis_client_that_cannot_be_created():
    pool = TokenPool()
    with patch("redis.asyncio.from_url", side_effect=ConnectionError("no redis")):
        await pool.init()
    assert pool.redis is None and pool._revocation_task is None


def test_api_lifespan_starts_and_closes_the_token_pool():
    from api import main

    src = inspect.getsource(main.lifespan)
    assert "await token_pool.init()" in src
    assert src.index("yield") < src.index("await token_pool.close()")


# ── Refresh before expiry ────────────────────────────────────────────────


def _pool_grant(expires_in: float, token: str = "placeholder-old") -> RunGrant:  # noqa: S107 - not a credential
    return RunGrant(
        mode=EnforcementMode.WARN,
        token=token,
        source="minted",
        grant_id="grnt_old",
        expires_at=time.time() + expires_in,
        ttl_seconds=900,
        tenant_id=TENANT,
        agent_id=AGENT,
        grantex_agent_id="ag_1",
        scopes=tuple(SCOPES),
    )


async def test_a_grant_with_enough_lifetime_is_not_refreshed():
    grant = _pool_grant(600)
    with patch("auth.token_pool.token_pool.get_run_grant_token", side_effect=AssertionError("no refresh")):
        assert await refresh_run_grant(grant) is grant


async def test_an_expiring_pool_grant_is_replaced_by_a_fresh_one():
    grant = _pool_grant(30)
    fresh = RunGrantToken("placeholder-fresh", "grnt_new", time.time() + 900, "minted", 900)

    async def _get(**kwargs: Any) -> RunGrantToken:
        assert kwargs["grantex_agent_id"] == "ag_1" and kwargs["scopes"] == SCOPES
        return fresh

    with patch("auth.token_pool.token_pool.get_run_grant_token", _get):
        refreshed = await refresh_run_grant(grant)
    assert (refreshed.token, refreshed.grant_id, refreshed.mode) == ("placeholder-fresh", "grnt_new", grant.mode)


async def test_supplied_and_configured_tokens_are_never_refreshed():
    for source in ("supplied", "agent_config", "none"):
        grant = RunGrant(mode=EnforcementMode.DENY, token="placeholder", source=source, expires_at=time.time())
        with patch("auth.token_pool.token_pool.get_run_grant_token", side_effect=AssertionError("no refresh")):
            assert await refresh_run_grant(grant) is grant


async def test_the_graph_swaps_in_a_fresh_grant_before_a_tool_call(scripted_model):
    from core.langgraph.agent_graph import build_agent_graph
    from core.test_doubles.scripted_model import final, tool_call

    scripted_model(
        [tool_call("gmail__send_email", to="ap@example.com"), final({"status": "completed", "confidence": 0.95})]
    )
    fresh = RunGrant(**{**_pool_grant(900, token="placeholder-fresh").__dict__})
    enforce = MagicMock()
    enforce.enforce.return_value = MagicMock(allowed=True, reason="", reason_code="", grant_id="grnt_new")
    executed = MagicMock()

    async def _exec(*_: Any, **__: Any) -> dict[str, Any]:
        executed()
        return {"status": "sent"}

    with (
        patch("core.langgraph.agent_graph.refresh_run_grant", return_value=fresh) as refresh,
        patch("core.langgraph.agent_graph.get_grantex_client", return_value=enforce),
        patch("core.langgraph.tool_adapter._execute_connector_tool", _exec),
    ):
        graph = build_agent_graph(
            system_prompt="scripted",
            authorized_tools=["gmail:send_email"],
            connector_config={},
            connector_names=["gmail"],
            confidence_floor=0.5,
            run_grant=_pool_grant(30),
        )
        result = await graph.compile().ainvoke(
            {
                "messages": [SystemMessage(content="scripted"), HumanMessage(content="go")],
                "agent_id": AGENT,
                "agent_type": "analyst",
                "domain": "ops",
                "tenant_id": TENANT,
                "grant_token": "placeholder-old",
                "confidence": 0.0,
                "status": "running",
                "output": {},
                "reasoning_trace": [],
                "tool_calls_log": [],
                "hitl_trigger": "",
                "error": "",
            }
        )
    refresh.assert_awaited()
    assert enforce.enforce.call_args.kwargs["grant_token"] == "placeholder-fresh"
    assert result["grant_token"] == "placeholder-fresh"
    assert executed.called


# ── run_grant cannot be omitted ──────────────────────────────────────────


def test_build_agent_graph_requires_run_grant():
    from core.langgraph.agent_graph import build_agent_graph

    with pytest.raises(TypeError, match="run_grant"):
        build_agent_graph(system_prompt="x", authorized_tools=[])  # type: ignore[call-arg]


def _builder_functions():
    import importlib

    for path in sorted((REPO / "core" / "langgraph" / "agents").glob("*.py")):
        module = importlib.import_module(f"core.langgraph.agents.{path.stem}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ == module.__name__ and name.startswith("build") and name.endswith("graph"):
                yield f"{path.stem}.{name}", fn


BUILDERS = list(_builder_functions())


def test_every_per_type_builder_was_found():
    assert len(BUILDERS) == 37


@pytest.mark.parametrize(("name", "builder"), BUILDERS, ids=[name for name, _ in BUILDERS])
def test_every_graph_builder_requires_run_grant_as_a_keyword(name, builder):
    parameter = inspect.signature(builder).parameters.get("run_grant")
    assert parameter is not None, name
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
    assert parameter.default is inspect.Parameter.empty, name


def test_production_code_never_passes_the_test_sentinel_or_none():
    offenders: list[str] = []
    for top in ("api", "auth", "core", "workflows", "connectors"):
        for path in (REPO / top).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(text)):
                uses_sentinel = (isinstance(node, ast.Name) and node.id == "NO_RUN_GRANT_FOR_TESTS") or (
                    isinstance(node, ast.alias) and node.name == "NO_RUN_GRANT_FOR_TESTS"
                )
                if uses_sentinel and path.name != "run_grants.py":
                    offenders.append(f"{path.relative_to(REPO)}: NO_RUN_GRANT_FOR_TESTS")
                if isinstance(node, ast.keyword) and node.arg == "run_grant":
                    if isinstance(node.value, ast.Constant) and node.value.value is None:
                        offenders.append(f"{path.relative_to(REPO)}:{node.value.lineno}: run_grant=None")
    assert offenders == []
    assert NO_RUN_GRANT_FOR_TESTS is None


# ── Tokens stay out of traces and checkpoints ────────────────────────────


def test_trace_redaction_replaces_grant_tokens_everywhere():
    from observability.trace_redaction import REDACTED, redact_credentials

    payload = {
        "grant_token": "placeholder-grant",
        "messages": [{"content": "hi"}],
        "nested": {"caller_token": "placeholder-caller", "items": [{"grant_token": "placeholder-2"}]},
        "empty": {"grant_token": ""},
    }
    redacted = redact_credentials(payload)
    assert redacted["grant_token"] == REDACTED
    assert redacted["nested"]["caller_token"] == REDACTED
    assert redacted["nested"]["items"][0]["grant_token"] == REDACTED
    assert redacted["messages"] == [{"content": "hi"}]
    assert "placeholder" not in repr(redacted)


def test_trace_redaction_installs_a_redacting_client_only_when_tracing_is_on(monkeypatch):
    from observability import trace_redaction

    monkeypatch.setattr(trace_redaction, "_installed", False)
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        monkeypatch.delenv(name, raising=False)
    with patch("langsmith.configure") as configure:
        assert trace_redaction.install_trace_redaction() is False
        configure.assert_not_called()

        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "placeholder-key")
        assert trace_redaction.install_trace_redaction() is True
    client = configure.call_args.kwargs["client"]
    hidden = client._hide_run_inputs({"grant_token": "placeholder-grant", "task": "x"})
    assert hidden == {"grant_token": "[redacted]", "task": "x"}
    monkeypatch.setattr(trace_redaction, "_installed", False)


def test_sealed_checkpoints_do_not_contain_the_grant_token_in_clear():
    checkpointer = pytest.importorskip(
        "core.langgraph.checkpointer", reason="the sealed checkpoint store ships separately (F-2a)"
    )
    from cryptography.fernet import Fernet

    serializer = checkpointer.SealedSerializer(checkpointer.VaultKeyringCipher([Fernet.generate_key()]))
    state = {"grant_token": "placeholder-grant-in-state", "messages": [AIMessage(content="ok")]}
    with checkpointer.checkpoint_binding("thread-1", ""):
        kind, blob = serializer.dumps_typed(state)
        assert b"placeholder-grant-in-state" not in blob
        assert serializer.loads_typed((kind, blob))["grant_token"] == "placeholder-grant-in-state"


def test_run_grant_ttl_below_five_minutes_is_refused():
    from pydantic import ValidationError

    from core.config import Settings

    with pytest.raises(ValidationError):
        Settings(grants_run_token_ttl_seconds=299)
    assert Settings.model_fields["grants_run_token_ttl_seconds"].default == 900


async def test_the_mint_lock_map_never_exceeds_its_bound(monkeypatch):
    from auth import token_pool as tp

    monkeypatch.setattr(tp, "_LOCKS_MAX", 3)
    pool = TokenPool()
    held = [pool._mint_lock(f"key-{n}") for n in range(3)]
    for lock in held:
        await lock.acquire()
    # All entries held: a new key gets an unshared lock and the map stays at the bound.
    extra = pool._mint_lock("key-extra")
    assert len(pool._mint_locks) == 3 and not extra.locked()
    held[0].release()
    # An unlocked entry is evicted to make room.
    pool._mint_lock("key-new")
    assert len(pool._mint_locks) == 3
    assert any(key[1] == "key-new" for key in pool._mint_locks)


async def test_the_revocation_listener_restarts_after_a_redis_failure(monkeypatch):
    import redis.asyncio as aioredis

    from auth import token_pool as tp

    monkeypatch.setattr(tp, "_REVOCATION_RETRY_MIN_SECONDS", 0.0)
    attempts: list[int] = []
    stop = asyncio.Event()

    class _PubSub:
        async def subscribe(self, channel: str) -> None:
            attempts.append(1)
            if len(attempts) < 3:
                raise aioredis.ConnectionError("redis down")

        async def listen(self):
            stop.set()
            await asyncio.sleep(3600)
            yield {}

    pool = TokenPool()
    pool.redis = MagicMock()
    pool.redis.pubsub.return_value = _PubSub()
    task = asyncio.create_task(pool._supervise_revocations())
    await asyncio.wait_for(stop.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(attempts) == 3
