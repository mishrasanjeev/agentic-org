# SPDX-License-Identifier: Apache-2.0
"""Checkpoint store selection, fail-closed lifecycle and encryption at rest (PRD F-2)."""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from core.langgraph import checkpointer as cp

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "migrations" / "versions" / "v6_z22_langgraph_checkpoints.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("v6_z22_langgraph_checkpoints", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "_memory_saver", None)
    monkeypatch.setattr(cp, "_postgres_store", None)
    monkeypatch.setattr(cp, "_open_lock", None)


def _use_postgres(monkeypatch: pytest.MonkeyPatch, url: str = "postgresql+asyncpg://u:p@127.0.0.1:1/db") -> None:
    monkeypatch.setattr(cp.settings, "langgraph_checkpointer", "postgres")
    monkeypatch.setattr(cp.settings, "langgraph_checkpoint_db_url", url)
    monkeypatch.setattr(cp.settings, "langgraph_checkpoint_connect_timeout_seconds", 1.0)


# ── Backend selection ───────────────────────────────────────────────────────


def test_setting_defaults_to_memory() -> None:
    from core.config import Settings

    assert Settings.model_fields["langgraph_checkpointer"].default == "memory"


async def test_memory_backend_returns_one_process_memory_saver() -> None:
    first = await cp.get_checkpointer()
    assert isinstance(first, MemorySaver)
    assert await cp.get_checkpointer() is first


async def test_postgres_backend_that_cannot_connect_fails_closed_without_memory_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_postgres(monkeypatch)
    before = cp.checkpointer_unavailable_total.labels(reason="checkpoint_store_unreachable")._value.get()

    with pytest.raises(cp.CheckpointerUnavailableError) as exc_info:
        await cp.open_checkpointer()
    assert exc_info.value.reason == "checkpoint_store_unreachable"
    assert "p@" not in str(exc_info.value)  # no credentials in the error

    # Every later attempt is refused too; memory is never substituted.
    with pytest.raises(cp.CheckpointerUnavailableError):
        await cp.get_checkpointer()
    assert cp._memory_saver is None
    assert cp._postgres_store is None
    after = cp.checkpointer_unavailable_total.labels(reason="checkpoint_store_unreachable")._value.get()
    assert after == before + 2


async def test_schema_refusal_reason_is_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_postgres(monkeypatch)
    refusal = cp.CheckpointerUnavailableError("checkpoint_schema_stale", "checkpoint_migrations is at 8")
    with (
        patch("core.langgraph.checkpoint_postgres.open_sealed_saver", new=AsyncMock(side_effect=refusal)),
        pytest.raises(cp.CheckpointerUnavailableError) as exc_info,
    ):
        await cp.get_checkpointer()
    assert exc_info.value.reason == "checkpoint_schema_stale"


async def test_postgres_store_is_opened_once_per_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_postgres(monkeypatch)
    saver, pool = object(), AsyncMock()
    opener = AsyncMock(return_value=(saver, pool))
    with patch("core.langgraph.checkpoint_postgres.open_sealed_saver", new=opener):
        results = await asyncio.gather(*(cp.get_checkpointer() for _ in range(5)))
    assert all(result is saver for result in results)
    assert opener.await_count == 1
    kwargs = opener.await_args.kwargs
    assert kwargs["conninfo"] == "postgresql://u:p@127.0.0.1:1/db"
    assert isinstance(kwargs["serde"], cp.SealedSerializer)

    await cp.close_checkpointer()
    pool.close.assert_awaited_once()
    assert cp._postgres_store is None


async def test_pool_owned_by_another_running_loop_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_postgres(monkeypatch)
    other_loop = asyncio.new_event_loop()
    try:
        monkeypatch.setattr(cp, "_postgres_store", cp._OpenStore(saver=object(), pool=AsyncMock(), loop=other_loop))
        opener = AsyncMock()
        with (
            patch("core.langgraph.checkpoint_postgres.open_sealed_saver", new=opener),
            pytest.raises(cp.CheckpointerUnavailableError) as exc_info,
        ):
            await cp.get_checkpointer()
        assert exc_info.value.reason == "checkpoint_event_loop_mismatch"
        opener.assert_not_awaited()
    finally:
        other_loop.close()


async def test_pool_from_a_closed_loop_is_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_postgres(monkeypatch)
    dead_loop = asyncio.new_event_loop()
    dead_loop.close()
    monkeypatch.setattr(cp, "_postgres_store", cp._OpenStore(saver=object(), pool=AsyncMock(), loop=dead_loop))
    fresh = object()
    opener = AsyncMock(return_value=(fresh, AsyncMock()))
    with patch("core.langgraph.checkpoint_postgres.open_sealed_saver", new=opener):
        assert await cp.get_checkpointer() is fresh


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+asyncpg://u:p@db.internal:5432/app", "postgresql://u:p@db.internal:5432/app"),
        ("postgresql+asyncpg://u:p@db.internal/app?ssl=require", "postgresql://u:p@db.internal/app?sslmode=require"),
        ("postgresql+asyncpg://u:p@db.internal/app?ssl=true", "postgresql://u:p@db.internal/app?sslmode=require"),
        (
            "postgresql+asyncpg://u:p@/app?host=/cloudsql/project:region:instance",
            "postgresql://u:p@/app?host=%2Fcloudsql%2Fproject%3Aregion%3Ainstance",
        ),
        ("postgres://u:p@db.internal/app", "postgresql://u:p@db.internal/app"),
    ],
)
def test_conninfo_translates_the_asyncpg_url_for_libpq(url: str, expected: str) -> None:
    assert cp.checkpoint_conninfo(url) == expected


@pytest.mark.parametrize("url", ["sqlite:///x.db", "mysql://u:p@h/db", "postgresql:///no-host", "not a url"])
def test_conninfo_rejects_urls_that_are_not_postgres(url: str) -> None:
    with pytest.raises(cp.CheckpointerUnavailableError) as exc_info:
        cp.checkpoint_conninfo(url)
    assert exc_info.value.reason == "checkpoint_db_url_invalid"


# ── Encryption at rest ──────────────────────────────────────────────────────


def test_sealed_serializer_round_trips_and_writes_ciphertext_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v1:unit-test-checkpoint-key")
    serde = cp.sealed_serializer()
    value = {"grant_token": "grant-sentinel-0001", "messages": ["hello"]}

    type_name, blob = serde.dumps_typed(value)

    assert type_name.endswith("+fernet")
    assert b"grant-sentinel-0001" not in blob
    assert serde.loads_typed((type_name, blob)) == value


def test_sealed_serializer_refuses_unencrypted_checkpoint_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v1:unit-test-checkpoint-key")
    plaintext = JsonPlusSerializer().dumps_typed({"status": "completed"})
    with pytest.raises(cp.CheckpointIntegrityError) as exc_info:
        cp.sealed_serializer().loads_typed(plaintext)
    assert exc_info.value.reason == "checkpoint_not_encrypted"


def test_sealed_serializer_refuses_tampered_or_foreign_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v1:unit-test-checkpoint-key")
    serde = cp.sealed_serializer()
    type_name, blob = serde.dumps_typed({"status": "completed"})

    with pytest.raises(cp.CheckpointIntegrityError) as tampered:
        serde.loads_typed((type_name, blob[:-4] + b"AAAA"))
    assert tampered.value.reason == "checkpoint_decrypt_failed"

    with pytest.raises(cp.CheckpointIntegrityError) as foreign:
        serde.loads_typed((type_name.replace("+fernet", "+aes"), blob))
    assert foreign.value.reason == "checkpoint_cipher_unknown"

    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v9:some-other-key")
    with pytest.raises(cp.CheckpointIntegrityError) as wrong_key:
        cp.sealed_serializer().loads_typed((type_name, blob))
    assert wrong_key.value.reason == "checkpoint_decrypt_failed"


def test_checkpoints_written_before_a_key_rotation_still_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v1:unit-test-checkpoint-key")
    written = cp.sealed_serializer().dumps_typed({"status": "paused"})
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v2:rotated-checkpoint-key,v1:unit-test-checkpoint-key")
    assert cp.sealed_serializer().loads_typed(written) == {"status": "paused"}


def test_cipher_requires_a_key() -> None:
    with pytest.raises(cp.CheckpointerUnavailableError) as exc_info:
        cp.VaultKeyringCipher([])
    assert exc_info.value.reason == "checkpoint_encryption_key_missing"
    assert cp.VaultKeyringCipher([Fernet.generate_key()]).encrypt(b"x")[0] == "fernet"


# ── Migration mirrors the pinned library ────────────────────────────────────


def _normalise(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.replace("CONCURRENTLY ", "")).strip()


def test_pinned_checkpoint_library_version() -> None:
    # SealedAsyncPostgresSaver.aput and the v6z22 DDL mirror this exact
    # version. Upgrading it means reviewing both and, if the library added
    # migrations, shipping a revision that applies them.
    assert importlib.metadata.version("langgraph-checkpoint-postgres") == "3.1.2"


def test_migration_ddl_is_the_library_migration_list() -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    migration = _load_migration()
    library = [_normalise(sql) for sql in AsyncPostgresSaver.MIGRATIONS]
    ours = [_normalise(sql) for sql in migration.CHECKPOINT_MIGRATIONS]
    assert ours == library


def test_migration_revision_chain_and_stamp() -> None:
    migration = _load_migration()
    assert migration.revision == "v6z22_langgraph_checkpoints"
    assert len(migration.revision) <= 32
    assert migration.down_revision == "v6z21_resource_ownership"

    executed: list[str] = []
    with patch.object(migration, "op", new=type("_Op", (), {"execute": staticmethod(executed.append)})):
        migration.upgrade()
        stamp = executed[-1]
        migration.downgrade()
    assert "generate_series(0, 9)" in stamp
    assert "ON CONFLICT (v) DO NOTHING" in stamp
    # No runtime-only DDL: plain CREATE INDEX inside Alembic's transaction.
    assert not any("CONCURRENTLY" in sql for sql in executed)
    assert executed[-4:] == [f"DROP TABLE IF EXISTS {t}" for t in reversed(migration.CHECKPOINT_TABLES)]


def test_migrate_wrapper_requires_the_checkpoint_tables() -> None:
    wrapper = (REPO / "scripts" / "alembic_migrate.py").read_text(encoding="utf-8")
    block = re.search(r"REQUIRED_RUNTIME_TABLES = frozenset\((.*?)\n\)", wrapper, re.S)
    assert block is not None
    for table in _load_migration().CHECKPOINT_TABLES:
        assert f'"{table}"' in block.group(1)


# ── Process lifecycle ───────────────────────────────────────────────────────


async def test_api_startup_fails_when_the_configured_store_is_unavailable() -> None:
    from api import main

    refusal = cp.CheckpointerUnavailableError("checkpoint_store_unreachable")
    with (
        patch("core.database.init_db", new=AsyncMock()),
        patch("core.langgraph.checkpointer.open_checkpointer", new=AsyncMock(side_effect=refusal)) as opener,
        pytest.raises(cp.CheckpointerUnavailableError),
    ):
        async with main.lifespan(main.app):
            pytest.fail("the app must not start")
    opener.assert_awaited_once()


async def test_api_shutdown_closes_the_store() -> None:
    from api import main

    closer = AsyncMock()
    with (
        patch("core.database.init_db", new=AsyncMock()),
        patch("core.database.close_db", new=AsyncMock()),
        patch("api.v1.health.close_health_resources", new=AsyncMock()),
        patch("core.langgraph.checkpointer.open_checkpointer", new=AsyncMock()),
        patch("core.langgraph.checkpointer.close_checkpointer", new=closer),
        patch("redis.asyncio.from_url", side_effect=ConnectionError("no redis in unit tests")),
        patch("core.langgraph.grantex_auth.get_grantex_client", side_effect=RuntimeError("no grantex")),
    ):
        async with main.lifespan(main.app):
            closer.assert_not_awaited()
    closer.assert_awaited_once()


def test_worker_process_opens_and_closes_the_store_on_its_runner_loop() -> None:
    from core.tasks import celery_app

    seen: list[Any] = []

    async def _open() -> None:
        seen.append(("open", asyncio.get_running_loop()))

    async def _close() -> None:
        seen.append(("close", asyncio.get_running_loop()))

    with (
        patch("core.langgraph.checkpointer.open_checkpointer", new=_open),
        patch("core.langgraph.checkpointer.close_checkpointer", new=_close),
    ):
        celery_app._open_checkpointer_in_worker()
        celery_app._close_checkpointer_in_worker()

    from core.tasks.async_runner import _loop_for_current_process

    assert [name for name, _ in seen] == ["open", "close"]
    assert seen[0][1] is seen[1][1] is _loop_for_current_process()


async def test_runner_compiles_graphs_with_the_configured_store() -> None:
    from core.langgraph import runner

    sentinel = object()
    fake_graph = type("G", (), {})()
    compiled_with: list[Any] = []

    def _compile(checkpointer: Any = None) -> Any:
        compiled_with.append(checkpointer)
        raise RuntimeError("stop after compile")

    fake_graph.compile = _compile
    with (
        patch.object(runner, "get_checkpointer", new=AsyncMock(return_value=sentinel)),
        patch.object(runner, "build_agent_graph", return_value=fake_graph),
        pytest.raises(RuntimeError, match="stop after compile"),
    ):
        await runner.resume_agent(
            agent_id="a", thread_id="t", decision={"action": "approve"}, system_prompt="s", authorized_tools=[]
        )
    assert compiled_with == [sentinel]


async def test_runner_surfaces_an_unavailable_store_instead_of_running() -> None:
    from core.langgraph import runner

    refusal = cp.CheckpointerUnavailableError("checkpoint_store_unreachable")
    fake_graph = type("G", (), {"compile": lambda self, checkpointer=None: pytest.fail("must not compile")})()
    with (
        patch.object(runner, "get_checkpointer", new=AsyncMock(side_effect=refusal)),
        patch.object(runner, "build_agent_graph", return_value=fake_graph),
        patch("core.billing.metering.gate_agent_run", new=AsyncMock(return_value=None)),
        patch.object(runner, "prefetch_llm_credential", new=AsyncMock(return_value=None)),
        pytest.raises(cp.CheckpointerUnavailableError),
    ):
        await runner.run_agent(
            agent_id="a",
            agent_type="t",
            domain="ops",
            tenant_id="",
            system_prompt="s",
            authorized_tools=[],
            task_input={"action": "process", "inputs": {}, "context": {}},
        )
