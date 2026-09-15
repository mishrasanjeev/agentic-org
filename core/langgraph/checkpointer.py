# SPDX-License-Identifier: Apache-2.0
"""LangGraph checkpoint store: backend selection and lifecycle.

``AGENTICORG_LANGGRAPH_CHECKPOINTER`` selects the backend:

* ``memory`` (default): one in-process ``MemorySaver``. A run paused for
  human approval is lost when the process exits.
* ``postgres``: checkpoints live in the tables created by the
  ``v6z22_langgraph_checkpoints`` Alembic revision, reached through a psycopg
  connection pool. Every channel value is serialized and encrypted with the
  credential-vault keyring before it is written (``SealedSerializer``), and a
  checkpoint that is not encrypted, or does not decrypt, is refused.

The Postgres backend fails closed. If the pool cannot connect or the schema is
missing or at the wrong version, ``CheckpointerUnavailableError`` is raised
with a reason code: at API startup (``api/main.py`` lifespan), at worker
startup (``core/tasks/celery_app.py``) and on every later attempt to run an
agent. It never falls back to process memory.

The pool is bound to the event loop that opened it. The API opens it in the
lifespan loop and each Celery worker process in its persistent
``core.tasks.async_runner`` loop; a caller on any other live loop gets
``checkpoint_event_loop_mismatch`` rather than a pool it cannot use.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.base import CipherProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from prometheus_client import Counter

from core.config import settings

logger = structlog.get_logger()

BACKEND_MEMORY = "memory"
BACKEND_POSTGRES = "postgres"

checkpointer_unavailable_total = Counter(
    "agenticorg_checkpointer_unavailable_total",
    "Agent checkpoint store refusals (Postgres backend), by reason code",
    ["reason"],
)


class CheckpointerUnavailableError(RuntimeError):
    """The configured checkpoint store cannot be used. ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"agent checkpoint store unavailable: {reason}" + (f" ({detail})" if detail else ""))


class CheckpointIntegrityError(ValueError):
    """A stored checkpoint is not encrypted or does not decrypt. ``reason`` is a stable code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"agent checkpoint rejected: {reason}")


class VaultKeyringCipher(CipherProtocol):
    """Fernet over the credential-vault keyring: the first key encrypts, every key decrypts."""

    name = "fernet"

    def __init__(self, keys: list[bytes]) -> None:
        if not keys:
            raise CheckpointerUnavailableError("checkpoint_encryption_key_missing")
        self._fernet = MultiFernet([Fernet(key) for key in keys])

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        return self.name, self._fernet.encrypt(plaintext)

    def decrypt(self, ciphername: str, ciphertext: bytes) -> bytes:
        if ciphername != self.name:
            raise CheckpointIntegrityError("checkpoint_cipher_unknown")
        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise CheckpointIntegrityError("checkpoint_decrypt_failed") from exc


class SealedSerializer(EncryptedSerializer):
    """``EncryptedSerializer`` that refuses to load anything that was stored unencrypted."""

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        type_name = data[0]
        if "+" not in type_name:
            raise CheckpointIntegrityError("checkpoint_not_encrypted")
        return super().loads_typed(data)


def sealed_serializer() -> SealedSerializer:
    from core.crypto.credential_vault import _load_keyring

    return SealedSerializer(VaultKeyringCipher([key for _kid, key in _load_keyring()]), JsonPlusSerializer())


def checkpoint_conninfo(db_url: str) -> str:
    """Translate the SQLAlchemy/asyncpg URL into a libpq URL for psycopg."""
    try:
        parts = urlsplit(db_url)
    except ValueError as exc:
        raise CheckpointerUnavailableError("checkpoint_db_url_invalid") from exc
    scheme = parts.scheme.split("+", 1)[0]
    if scheme not in {"postgresql", "postgres"} or not (parts.hostname or "host=" in parts.query):
        raise CheckpointerUnavailableError("checkpoint_db_url_invalid")
    query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "ssl":  # asyncpg spelling of libpq's sslmode
            key = "sslmode"
            value = {"true": "require", "false": "disable"}.get(value.lower(), value)
        query.append((key, value))
    return urlunsplit(("postgresql", parts.netloc, parts.path, urlencode(query), ""))


@dataclass
class _OpenStore:
    saver: BaseCheckpointSaver
    pool: Any
    loop: asyncio.AbstractEventLoop


# enterprise-gate: process-local-ok reason=memory-backend-is-the-documented-process-local-default
_memory_saver: MemorySaver | None = None
# enterprise-gate: process-local-ok reason=per-process-connection-pool-handle-bound-to-one-event-loop
_postgres_store: _OpenStore | None = None
# enterprise-gate: process-local-ok reason=per-event-loop-open-lock
_open_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None


def configured_backend() -> str:
    return str(settings.langgraph_checkpointer)


def _refuse(error: CheckpointerUnavailableError) -> CheckpointerUnavailableError:
    checkpointer_unavailable_total.labels(reason=error.reason).inc()
    logger.error("langgraph_checkpointer_unavailable", reason=error.reason, detail=error.detail)
    return error


def _lock_for(loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
    global _open_lock
    if _open_lock is None or _open_lock[0] is not loop:
        _open_lock = (loop, asyncio.Lock())
    return _open_lock[1]


async def open_checkpointer() -> BaseCheckpointSaver:
    """Open the configured backend on the running loop and return its saver."""
    global _memory_saver, _postgres_store
    if configured_backend() != BACKEND_POSTGRES:
        if _memory_saver is None:
            _memory_saver = MemorySaver()
        return _memory_saver

    loop = asyncio.get_running_loop()
    async with _lock_for(loop):
        if _postgres_store is not None and _postgres_store.loop is loop:
            return _postgres_store.saver
        try:
            from core.langgraph.checkpoint_postgres import open_sealed_saver

            conninfo = checkpoint_conninfo(settings.langgraph_checkpoint_db_url or settings.db_url)
            saver, pool = await open_sealed_saver(
                conninfo=conninfo,
                serde=sealed_serializer(),
                max_size=settings.langgraph_checkpoint_pool_max_size,
                timeout_seconds=settings.langgraph_checkpoint_connect_timeout_seconds,
            )
        except CheckpointerUnavailableError as exc:
            raise _refuse(exc) from exc
        except ImportError as exc:
            raise _refuse(CheckpointerUnavailableError("checkpoint_driver_missing", type(exc).__name__)) from exc
        _postgres_store = _OpenStore(saver=saver, pool=pool, loop=loop)
        logger.info("langgraph_checkpointer_opened", backend=BACKEND_POSTGRES)
        return saver


async def get_checkpointer() -> BaseCheckpointSaver:
    """Return the saver for the configured backend. Never substitutes another backend."""
    global _postgres_store
    if configured_backend() != BACKEND_POSTGRES:
        return await open_checkpointer()
    store = _postgres_store
    if store is not None:
        loop = asyncio.get_running_loop()
        if store.loop is loop:
            return store.saver
        if not store.loop.is_closed():
            raise _refuse(
                CheckpointerUnavailableError(
                    "checkpoint_event_loop_mismatch", "the pool belongs to another running event loop"
                )
            )
        # The loop that owned the pool is gone and its connections with it.
        _postgres_store = None
    # Not opened in this process yet (a solo worker, a script): open it now.
    return await open_checkpointer()


async def close_checkpointer() -> None:
    """Close the Postgres pool opened by this process, if any."""
    global _postgres_store
    store = _postgres_store
    _postgres_store = None
    if store is None:
        return
    if store.loop is not asyncio.get_running_loop():
        logger.warning("langgraph_checkpointer_close_skipped", reason="checkpoint_event_loop_mismatch")
        return
    await store.pool.close()
    logger.info("langgraph_checkpointer_closed", backend=BACKEND_POSTGRES)
