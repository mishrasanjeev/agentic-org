# SPDX-License-Identifier: Apache-2.0
"""LangGraph checkpoint store: backend selection and lifecycle.

``AGENTICORG_LANGGRAPH_CHECKPOINTER`` selects the backend:

* ``memory`` (default): one in-process ``MemorySaver``. A run paused for
  human approval is lost when the process exits.
* ``postgres``: checkpoints live in the tables created by the
  ``v6z22_langgraph_checkpoints`` Alembic revision, reached through a psycopg
  connection pool. Every channel value is serialized and encrypted with the
  credential-vault keyring before it is written (``SealedSerializer``). The
  encrypted payload is bound to its thread and namespace, so a blob copied to
  another thread (another tenant) is refused; so is a checkpoint that is not
  encrypted or does not decrypt.

The Postgres backend fails closed. If the installed checkpoint library is not
the verified version, the keyring is invalid, the pool cannot connect, or the
schema is missing or at the wrong version, ``CheckpointerUnavailableError`` is
raised with a reason code: at API startup (``api/main.py`` lifespan) and on
every attempt to run an agent. It never falls back to process memory.

The pool is bound to the event loop that opened it. The API opens it in the
lifespan loop. Celery workers open it lazily, on the first agent run, in the
worker process's persistent ``core.tasks.async_runner`` loop: opening it in
``worker_process_init`` would block the child past Celery's start-up
deadline whenever the store is slow, and an outage would kill every worker.
A caller on any other live loop gets ``checkpoint_event_loop_mismatch``
rather than a pool it cannot use.

The keyring is read when the store opens: a keyring change needs a restart of
the API and every worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.base import CipherProtocol, SerializerProtocol
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
    """A stored checkpoint is refused (unencrypted, undecryptable or bound elsewhere). ``reason`` is a stable code."""

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


# The (thread_id, checkpoint_ns) the saver is currently writing or reading.
_BINDING: ContextVar[bytes | None] = ContextVar("agenticorg_checkpoint_binding", default=None)
_BINDING_SIZE = hashlib.sha256().digest_size


@contextmanager
def checkpoint_binding(thread_id: str, checkpoint_ns: str) -> Iterator[None]:
    """Bind serializer calls inside the block to one thread and namespace."""
    digest = hashlib.sha256(
        b"agenticorg-checkpoint-v1\x00" + thread_id.encode("utf-8") + b"\x00" + checkpoint_ns.encode("utf-8")
    ).digest()
    token = _BINDING.set(digest)
    try:
        yield
    finally:
        _BINDING.reset(token)


def _current_binding() -> bytes:
    binding = _BINDING.get()
    if binding is None:
        raise CheckpointIntegrityError("checkpoint_binding_missing")
    return binding


class SealedSerializer(SerializerProtocol):
    """Encrypting serializer that binds each payload to its thread and refuses anything else.

    The thread binding (a SHA-256 of thread id and namespace) is encrypted
    together with the serialized value, so Fernet's MAC covers it: a blob moved
    to another thread decrypts but fails the binding check.
    """

    def __init__(self, cipher: VaultKeyringCipher, serde: SerializerProtocol | None = None) -> None:
        self.cipher = cipher
        self.serde = serde or JsonPlusSerializer()

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        binding = _current_binding()
        type_name, data = self.serde.dumps_typed(obj)
        ciphername, ciphertext = self.cipher.encrypt(binding + data)
        return f"{type_name}+{ciphername}", ciphertext

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        type_name, ciphertext = data
        if "+" not in type_name:
            raise CheckpointIntegrityError("checkpoint_not_encrypted")
        inner_type, ciphername = type_name.split("+", 1)
        binding = _current_binding()
        plaintext = self.cipher.decrypt(ciphername, ciphertext)
        if len(plaintext) < _BINDING_SIZE or not hmac.compare_digest(plaintext[:_BINDING_SIZE], binding):
            raise CheckpointIntegrityError("checkpoint_binding_mismatch")
        return self.serde.loads_typed((inner_type, plaintext[_BINDING_SIZE:]))


def sealed_serializer() -> SealedSerializer:
    """Serializer over the current credential-vault keyring. A malformed keyring is refused."""
    from core.crypto.credential_vault import VaultKeyNotConfiguredError, _load_keyring

    try:
        keys = [key for _kid, key in _load_keyring()]
        return SealedSerializer(VaultKeyringCipher(keys), JsonPlusSerializer())
    except VaultKeyNotConfiguredError as exc:
        raise CheckpointerUnavailableError("checkpoint_encryption_key_missing") from exc
    except (ValueError, TypeError) as exc:
        # The message can quote a keyring entry; keep only the type.
        raise CheckpointerUnavailableError("checkpoint_encryption_key_invalid", type(exc).__name__) from exc


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


def _store_for_loop(loop: asyncio.AbstractEventLoop) -> _OpenStore | None:
    """The open store if it belongs to ``loop``; refuses a store owned by another live loop."""
    global _postgres_store
    store = _postgres_store
    if store is None or store.loop is loop:
        return store
    if not store.loop.is_closed():
        raise _refuse(
            CheckpointerUnavailableError("checkpoint_event_loop_mismatch", "the pool belongs to another event loop")
        )
    # The loop that owned the pool is gone and its connections with it.
    logger.warning("langgraph_checkpointer_discarded", reason="owning_event_loop_closed")
    _postgres_store = None
    return None


async def open_checkpointer() -> BaseCheckpointSaver:
    """Open the configured backend on the running loop and return its saver."""
    global _memory_saver, _postgres_store
    if configured_backend() != BACKEND_POSTGRES:
        if _memory_saver is None:
            _memory_saver = MemorySaver()
        return _memory_saver

    loop = asyncio.get_running_loop()
    async with _lock_for(loop):
        store = _store_for_loop(loop)
        if store is not None:
            return store.saver
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
    """Return the saver for the configured backend, opening it on first use. Never substitutes another backend."""
    if configured_backend() != BACKEND_POSTGRES:
        return await open_checkpointer()
    store = _store_for_loop(asyncio.get_running_loop())
    if store is not None:
        return store.saver
    # Not opened in this process yet (a worker's first run, a script): open it now.
    return await open_checkpointer()


async def close_checkpointer(timeout_seconds: float = 10.0) -> None:
    """Close the Postgres pool opened by this process, if any, on the loop that owns it."""
    global _postgres_store
    store = _postgres_store
    if store is None:
        return
    current = asyncio.get_running_loop()
    if store.loop is current:
        _postgres_store = None
        await store.pool.close()
    elif store.loop.is_closed():
        _postgres_store = None
        logger.warning("langgraph_checkpointer_discarded", reason="owning_event_loop_closed")
        return
    elif store.loop.is_running():
        # Owned by a loop running in another thread: close it there.
        _postgres_store = None
        future = asyncio.run_coroutine_threadsafe(store.pool.close(), store.loop)
        await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout_seconds)
    else:
        logger.error(
            "langgraph_checkpointer_close_refused",
            reason="checkpoint_event_loop_mismatch",
            detail="the owning event loop is idle in this thread; close the store from that loop",
        )
        return
    logger.info("langgraph_checkpointer_closed", backend=BACKEND_POSTGRES)


async def delete_tenant_checkpoints(tenant_id: str) -> int:
    """Delete every checkpoint thread of ``tenant_id`` (tenant offboarding). Returns the threads removed."""
    # Thread ids are "tenant:<canonical uuid>:..." (core/langgraph/thread_ids.py).
    tenant = str(uuid.UUID(str(tenant_id)))
    prefix = f"tenant:{tenant}:"
    saver = await get_checkpointer()
    if isinstance(saver, MemorySaver):
        threads = [thread for thread in list(saver.storage) if thread.startswith(prefix)]
        for thread in threads:
            await saver.adelete_thread(thread)
        return len(threads)
    from core.langgraph.checkpoint_postgres import delete_threads_with_prefix

    store = _postgres_store
    if store is None:
        raise _refuse(CheckpointerUnavailableError("checkpoint_store_unreachable", "store closed during delete"))
    return await delete_threads_with_prefix(store.pool, prefix)
