"""Async SQLAlchemy engine, session management, and tenant RLS middleware."""

# ruff: noqa: S608 -- legacy startup repair interpolates only hard-coded identifiers.

from __future__ import annotations

import asyncio
import logging
import os
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from prometheus_client import Counter
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass
from sqlalchemy.pool import NullPool

from core.config import is_strict_runtime_env, settings

logger = logging.getLogger(__name__)

ALEMBIC_VERSION_TABLE = "alembic_version"
ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[1] / "alembic.ini"
ALLOW_MULTIPLE_ALEMBIC_HEADS_ENV = "AGENTICORG_ALLOW_MULTIPLE_ALEMBIC_HEADS_FOR_MERGE_PR"
ENABLE_LEGACY_STARTUP_DDL_ENV = "AGENTICORG_ENABLE_LEGACY_STARTUP_DDL"
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


class RuntimeSchemaError(RuntimeError):
    """Raised when a runtime DB is not at the Alembic revision this app expects."""


@dataclass(frozen=True)
class RuntimeSchemaVerification:
    """Result of an Alembic runtime schema verification."""

    database_versions: frozenset[str]
    expected_heads: frozenset[str]
    multiple_heads_allowed: bool = False


class Base(DeclarativeBase, MappedAsDataclass):
    """Declarative base for all ORM models."""

    pass


engine: AsyncEngine = create_async_engine(
    settings.db_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=300,
)

_shared_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class _GuardedSessionFactory:
    """The shared session factory, checked against the pool's owning loop.

    Most code opens sessions through ``get_tenant_session`` / ``get_session``.
    Thirty modules call this factory directly (FINDINGS A-53); wrapping it
    means they honour a private-engine override as well, and are checked
    against the pool's owning loop before any connection work — which the
    pool's own events cannot do, since ``pool_pre_ping`` runs its ping (and
    fails on the foreign loop) before the checkout event fires.
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession], target: AsyncEngine) -> None:
        self._factory = factory
        self._target = target

    def __call__(self, **kwargs: Any) -> AsyncSession:
        # A synchronous caller running on a private engine gets that engine's
        # session even here, so a module that binds this factory directly is
        # carried along instead of reaching back into the shared pool.
        provider = _session_factory_override.get()
        if provider is not None:
            return provider()(**kwargs)
        _refuse_foreign_loop(self._target, "opening a session")
        return self._factory(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._factory, name)

    def __repr__(self) -> str:
        return f"<guarded {self._factory!r}>"


async_session_factory: Any = _GuardedSessionFactory(_shared_session_factory, engine)

# ── Cross-loop guard on the shared pool ─────────────────────────────────────
#
# An asyncpg connection belongs to the event loop that opened it. A pooled
# connection used from a different loop fails a few frames later with
# `AttributeError: 'NoneType' object has no attribute 'send'` or "attached to
# a different loop", and `pool_pre_ping` does not rescue it (a cross-loop
# error is not a disconnect). The guard checks in three places: when a session
# is opened on a foreign loop (the wrapper below, which sees every one), when
# the pool opens a connection there, and when it hands an existing connection
# out there. Measured against this engine, the last two alternate and catch
# about half each: a warm connection is caught at checkout, dies and is
# invalidated, so the next violation finds an empty pool and is caught at
# connect.
CROSS_LOOP_GUARD_ENV = "AGENTICORG_DB_CROSS_LOOP_GUARD"
CROSS_LOOP_GUARD_MODES = ("warn", "raise", "off")
_LOOP_KEY = "agenticorg_owning_loop"

# One cross-loop use trips the guard about twice. Measured against this engine
# by counting trips per call site (1 -> 1, 2 -> 4, 3 -> 5, 5 -> 9, 10 -> 20,
# 20 -> 40): the session wrapper trips on every violation, while ``connect``
# and ``checkout`` alternate and catch about half each. The first violation
# against a cold pool trips once, because the connection dies before a
# replacement is opened. The metric therefore counts *trips*, not distinct
# mistakes — fine for a ratchet, which needs only to be monotonic and
# reproducible, but do not read it as a count of violations.
_cross_loop_checkouts_total = Counter(
    "agenticorg_db_cross_loop_checkouts_total",
    "Guard trips: a pooled engine used from an event loop other than the one that owns it",
    ["mode"],
)


class CrossLoopConnectionError(RuntimeError):
    """A pooled engine was used from an event loop other than its own."""


# Engines the guard watches: the loop that owns each, and what has been
# reported for it. Keyed weakly, because an ``id()`` is reused once an engine
# is collected and would then apply a stale loop binding to a new engine.
_guarded_engines: MutableMapping[AsyncEngine, dict[str, Any]] = weakref.WeakKeyDictionary()


def _initial_guard_mode() -> str:
    """Read the mode once, at import.

    ``warn`` by default: warning changes no outcome (the request fails exactly
    as it did) but turns a mystifying downstream failure into a named one,
    while raising would turn latent pool problems into new 500s in production.
    Nothing in CI sets this variable, so CI runs on the same default and holds
    the line with the ratchet instead (``cross_loop_baseline.txt``, counted in
    ``tests/conftest.py``). ``raise`` is for a developer chasing one of these,
    and for the tests that pin the refusal.
    """
    mode = os.getenv(CROSS_LOOP_GUARD_ENV, "warn").strip().casefold()
    return mode if mode in CROSS_LOOP_GUARD_MODES else "warn"


_guard_mode_value = _initial_guard_mode()


def cross_loop_guard_mode() -> str:
    """The active mode: ``warn``, ``raise`` or ``off``."""
    return _guard_mode_value


def set_cross_loop_guard_mode(mode: str) -> str:
    """Set the mode at run time (tests, an operator toggle); returns the previous one."""
    global _guard_mode_value
    if mode not in CROSS_LOOP_GUARD_MODES:
        raise ValueError(f"mode must be one of {CROSS_LOOP_GUARD_MODES}, not {mode!r}")
    previous, _guard_mode_value = _guard_mode_value, mode
    return previous


def cross_loop_message(where: str) -> str:
    """The diagnostic a cross-loop use produces. One sentence per idea."""
    return (
        f"the shared database pool was used from a second event loop ({where}). "
        "An asyncpg connection belongs to the loop that opened it, so this leaves "
        "the pool holding connections no request can use. Run database work from "
        "a synchronous caller through core.database.run_db_coroutine_sync, or "
        "through core.tasks.async_runner.run_async in a worker process; never "
        "call asyncio.run against the shared engine. "
        f"Set {CROSS_LOOP_GUARD_ENV}=raise to make this a failure, or off to "
        "silence it."
    )


def _running_loop() -> object | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _pool_is_empty(target: AsyncEngine) -> bool:
    """Whether the pool holds no connection a foreign loop could trip over."""
    pool = target.pool
    checked_in = getattr(pool, "checkedin", None)
    checked_out = getattr(pool, "checkedout", None)
    if checked_in is None or checked_out is None:
        return True
    return checked_in() == 0 and checked_out() == 0


def _refuse_foreign_loop(target: AsyncEngine, where: str) -> None:
    """Raise (or report once) when ``target``'s pool is used from another loop."""
    state = _guarded_engines.get(target)
    if state is None:
        return
    mode = _guard_mode_value
    if mode == "off":
        return
    current_loop = _running_loop()
    if current_loop is None:
        return
    owning_loop = state.get("loop")
    if owning_loop is None:
        state["loop"] = current_loop
        return
    if owning_loop is current_loop:
        return
    # The loop that owned the pool is gone and left nothing behind (a test that
    # disposed its engine, a one-shot script): there is no connection for this
    # loop to trip over, so adopt it.
    if getattr(owning_loop, "is_closed", lambda: False)() and _pool_is_empty(target):
        state["loop"] = current_loop
        return
    _cross_loop_checkouts_total.labels(mode=("warn" if mode == "warn" else "raise")).inc()
    message = cross_loop_message(where)
    if mode == "warn":
        # One line per foreign loop, not one per session: a single poisoned
        # process would otherwise drive alerting on volume alone.
        already_reported: set[int] = state.setdefault("reported", set())
        if id(current_loop) not in already_reported:
            already_reported.add(id(current_loop))
            logger.warning("db_cross_loop_use: %s", message)
        return
    raise CrossLoopConnectionError(message)


def install_cross_loop_guard(target: AsyncEngine) -> None:
    """Bind ``target``'s pool to the first event loop that uses it.

    Installed on the shared engine below; a pooled engine built by a test can
    ask for the same protection.

    Two hooks are added here — the pool opening a connection, and the pool
    handing an existing one out — and :class:`_GuardedSessionFactory` checks
    when a session is opened. All three fire on this engine: the wrapper on
    every violation, and ``connect`` and ``checkout`` alternately on about half
    each, because a warm connection caught at checkout is invalidated and the
    next violation then finds an empty pool. The wrapper is what makes a
    warm-pool reuse visible to a caller that binds ``async_session_factory``
    itself (FINDINGS A-53): ``pool_pre_ping`` can fail on the foreign loop
    before the ``checkout`` listener is reached.
    """
    _guarded_engines[target] = {"loop": None}

    @event.listens_for(target.sync_engine, "connect")
    def _record_owning_loop(_dbapi_connection: Any, connection_record: Any) -> None:
        connection_record.info[_LOOP_KEY] = _running_loop()
        _refuse_foreign_loop(target, "opening a connection")

    @event.listens_for(target.sync_engine, "checkout")
    def _check_out_on_the_owning_loop(
        _dbapi_connection: Any, _connection_record: Any, _connection_proxy: Any
    ) -> None:
        _refuse_foreign_loop(target, "checking a pooled connection out")


def uninstall_cross_loop_guard(target: AsyncEngine) -> None:
    """Forget ``target``'s loop binding (used when a test disposes its engine)."""
    _guarded_engines.pop(target, None)


install_cross_loop_guard(engine)


# Session factory the session helpers use. Normally the shared, pooled one;
# ``run_db_coroutine_sync`` overrides it for the duration of a coroutine it
# runs on a throwaway event loop (see that function).
_session_factory_override: ContextVar[Callable[[], async_sessionmaker[AsyncSession]] | None] = ContextVar(
    "agenticorg_session_factory_override", default=None
)


def current_session_factory() -> async_sessionmaker[AsyncSession]:
    """The session factory for this context: the shared one unless overridden."""
    provider = _session_factory_override.get()
    if provider is not None:
        return provider()
    # The module-level factory, so a test that replaces it is honoured. In
    # production that is the guarded wrapper, which does the loop check.
    return async_session_factory


def run_db_coroutine_sync[T](make_coroutine: Callable[[], Awaitable[T]]) -> T:
    """Run a database coroutine from a synchronous caller, on its own engine.

    An asyncpg connection belongs to the event loop that opened it. The shared
    engine pools connections, so a coroutine run on a throwaway loop must not
    take one from that pool (it fails on the foreign loop) and must not leave
    one in it (the next request to check it out fails, and ``pool_pre_ping``
    does not rescue it: a cross-loop ``RuntimeError`` is not a disconnect).

    This runs ``make_coroutine()`` on a new loop with a ``NullPool`` engine of
    its own, and disposes that engine before returning, so nothing outlives the
    loop. Callers that are already async must await the coroutine instead; a
    synchronous caller inside a running loop has to hand this to a worker
    thread, which is what ``core.ai_providers.resolver`` does.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "run_db_coroutine_sync cannot be called from a running event loop: "
            "await the coroutine, or submit this call to a worker thread."
        )

    async def _run() -> T:
        # Built on first use and from the engine in place now, so a coroutine
        # that opens no session pays for no connection, and a test that
        # replaced ``engine`` is followed rather than ``settings.db_url``.
        state: dict[str, Any] = {}

        def _provider() -> async_sessionmaker[AsyncSession]:
            # Loop-local: `state` is mutated without a guard, which is safe on
            # the single private loop. A coroutine that called
            # `current_session_factory()` from `asyncio.to_thread` could build
            # two engines here and leave one undisposed.
            if "factory" not in state:
                private_engine = create_async_engine(
                    engine.url.render_as_string(hide_password=False),
                    echo=engine.echo,
                    poolclass=NullPool,
                )
                state["engine"] = private_engine
                state["factory"] = async_sessionmaker(
                    private_engine, class_=AsyncSession, expire_on_commit=False
                )
            return state["factory"]

        token = _session_factory_override.set(_provider)
        try:
            return await make_coroutine()
        finally:
            _session_factory_override.reset(token)
            private = state.get("engine")
            if private is not None:
                await private.dispose()

    return asyncio.run(_run())


_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


async def _bind_tenant_context(session: AsyncSession, tid_str: str, company_str: str) -> None:
    """Apply the tenant/company RLS GUCs to the session's current transaction."""
    # set_config(..., is_local=true) is the parameterized equivalent of
    # SET LOCAL and avoids interpolating tenant context into SQL text.
    # UUID validation remains defense-in-depth for tenant context boundaries.
    await session.execute(
        text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
        {"tenant_id": tid_str},
    )
    await session.execute(
        text("SELECT set_config('agenticorg.company_id', :company_id, true)"),
        {"company_id": company_str},
    )


@asynccontextmanager
async def get_tenant_session(
    tenant_id: UUID,
    company_id: UUID | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session with exact tenant and optional company RLS context."""
    async with current_session_factory()() as session:
        import re as _re

        tid_str = str(tenant_id)
        if not _re.fullmatch(_UUID_RE, tid_str):
            raise ValueError(f"Invalid tenant_id format: {tid_str}")
        await session.execute(
            text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
            {"tenant_id": tid_str},
        )
        company_str = str(company_id) if company_id is not None else ""
        if company_str and not _re.fullmatch(_UUID_RE, company_str):
            raise ValueError(f"Invalid company_id format: {company_str}")
        await session.execute(
            text("SELECT set_config('agenticorg.company_id', :company_id, true)"),
            {"company_id": company_str},
        )

        # ``set_config(..., is_local=true)`` lives only for the current
        # transaction. A handler that calls ``session.commit()`` and keeps
        # using the session (refresh, follow-up SELECT, second write) would
        # silently continue *without* tenant context — RLS then hides every
        # row (or, for the pre-auth tables, shows every tenant's rows).
        # Wrap commit so the context is re-bound after each commit.
        original_commit = session.commit

        async def _commit_and_rebind() -> None:
            await original_commit()
            await _bind_tenant_context(session, tid_str, company_str)

        session.commit = _commit_and_rebind  # type: ignore[method-assign]
        try:
            yield session
            await original_commit()
        # enterprise-gate: broad-except-ok reason=tenant-session-failure-rolls-back-and-reraises
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a raw session (for non-tenant-scoped operations like health checks)."""
    async with current_session_factory()() as session:
        try:
            yield session
            await session.commit()
        # enterprise-gate: broad-except-ok reason=raw-session-failure-rolls-back-and-reraises
        except Exception:
            await session.rollback()
            raise


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUTHY_ENV_VALUES


def get_expected_alembic_heads() -> frozenset[str]:
    """Return the migration script heads bundled with this application build."""
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG_PATH)))
    return frozenset(script.get_heads())


def _multiple_alembic_heads_allowed() -> bool:
    return _truthy_env(ALLOW_MULTIPLE_ALEMBIC_HEADS_ENV)


def _schema_error(message: str) -> RuntimeSchemaError:
    return RuntimeSchemaError(f"{message} Run `python scripts/alembic_migrate.py` before starting the app.")


async def get_database_alembic_versions(conn: AsyncConnection) -> frozenset[str]:
    """Read the DB's Alembic revisions from the authoritative version table."""
    table_exists = await conn.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
    if not table_exists:
        raise _schema_error("Database is not Alembic-managed: missing `alembic_version` table.")

    result = await conn.execute(text("SELECT version_num FROM alembic_version"))
    versions = frozenset(str(version) for version in result.scalars().all() if version)
    if not versions:
        raise _schema_error("Database is not Alembic-managed: `alembic_version` has no rows.")
    return versions


async def verify_runtime_schema_current(
    conn: AsyncConnection,
    *,
    expected_heads: frozenset[str] | set[str] | None = None,
) -> RuntimeSchemaVerification:
    """Fail if the connected DB is missing, stale, or divergent from Alembic heads."""
    expected = frozenset(expected_heads or get_expected_alembic_heads())
    if not expected:
        raise _schema_error("Application has no Alembic heads to verify against.")

    multiple_heads_allowed = _multiple_alembic_heads_allowed()
    if len(expected) > 1 and not multiple_heads_allowed:
        heads = ", ".join(sorted(expected))
        raise _schema_error(
            "Application has multiple Alembic heads without an approved merge-head plan "
            f"({heads}). Add an Alembic merge revision or explicitly set "
            f"`{ALLOW_MULTIPLE_ALEMBIC_HEADS_ENV}=1` only for a merge-head PR."
        )

    versions = await get_database_alembic_versions(conn)
    if len(versions) > 1 and versions != expected:
        current = ", ".join(sorted(versions))
        wanted = ", ".join(sorted(expected))
        raise _schema_error(f"Database has multiple Alembic versions ({current}) but this build expects ({wanted}).")

    if versions != expected:
        current = ", ".join(sorted(versions))
        wanted = ", ".join(sorted(expected))
        raise _schema_error(
            f"Database schema revision is stale or divergent: current=({current}), expected=({wanted})."
        )

    return RuntimeSchemaVerification(
        database_versions=versions,
        expected_heads=expected,
        multiple_heads_allowed=multiple_heads_allowed,
    )


async def init_db() -> None:
    """Run on startup: verify connectivity and Alembic-managed schema state.

    Strict runtimes fail closed when the database is not at the exact Alembic
    head(s) bundled with this app build. Startup DDL is forbidden there.
    """
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
        if is_strict_runtime_env(settings.env):
            await verify_runtime_schema_current(conn)
            return

        if not _truthy_env(ENABLE_LEGACY_STARTUP_DDL_ENV):
            try:
                await verify_runtime_schema_current(conn)
            except RuntimeSchemaError as exc:
                logger.warning("Relaxed runtime schema verification did not pass: %s", exc)
            await _seed_demo_ca_companies_if_enabled()
            return

    await _legacy_startup_schema_repair_for_local_only()


async def _legacy_startup_schema_repair_for_local_only() -> None:
    """Local-only emergency schema repair path retained for unstamped dev DBs."""
    if is_strict_runtime_env(settings.env):
        raise RuntimeSchemaError(
            f"`{ENABLE_LEGACY_STARTUP_DDL_ENV}` is ignored in strict runtimes; "
            "run `python scripts/alembic_migrate.py` instead."
        )
    if not _truthy_env(ENABLE_LEGACY_STARTUP_DDL_ENV):
        raise RuntimeSchemaError(
            f"Legacy startup DDL is disabled unless `{ENABLE_LEGACY_STARTUP_DDL_ENV}=1` is set "
            "in a relaxed local/dev/test environment."
        )
    if os.getenv("AGENTICORG_DDL_MANAGED_BY_ALEMBIC"):
        logger.warning(
            "`AGENTICORG_DDL_MANAGED_BY_ALEMBIC` no longer controls startup DDL; "
            "using `%s=1` because the runtime is relaxed.",
            ENABLE_LEGACY_STARTUP_DDL_ENV,
        )

    async with engine.begin() as conn:
        # Serialize startup DDL across concurrently-booting pods with a
        # transaction-scoped advisory lock. Without this, a rolling deploy
        # that restarts N pods at once has them racing on ALTER TABLE ...
        # ENABLE ROW LEVEL SECURITY (AccessExclusiveLock) and deadlocking
        # against each other — the April 16 outage. The lock is released
        # automatically when this BEGIN..COMMIT transaction ends. Any
        # magic int64 works; the constant below is arbitrary but stable.
        await conn.execute(text("SELECT pg_advisory_xact_lock(4815162342);"))

        # v4.0.0: Ensure prompt_amendments column exists on agents table.
        # Safe to run every startup (IF NOT EXISTS check).
        await conn.execute(
            text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'agents' AND column_name = 'prompt_amendments'
                ) THEN
                    ALTER TABLE agents ADD COLUMN prompt_amendments JSONB DEFAULT '[]'::jsonb;
                END IF;
            END $$;
        """)
        )

        # v4.3.0: Ensure connector_ids column exists on agents table.
        await conn.execute(
            text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'agents' AND column_name = 'connector_ids'
                ) THEN
                    ALTER TABLE agents ADD COLUMN connector_ids JSONB DEFAULT '[]'::jsonb;
                END IF;
            END $$;
        """)
        )

        # v4.1.0: Ensure company_id column exists on operational tables.
        # Enables CA multi-tenant use case where a tenant manages N client
        # companies.  Nullable FK — existing rows keep company_id = NULL.
        _company_tables = [
            "agents",
            "workflow_definitions",
            "workflow_runs",
            "audit_log",
            "tool_calls",
            "connectors",
        ]
        for _tbl in _company_tables:
            # Table names come from the hardcoded _company_tables list above,
            # never from user input — safe to interpolate.
            await conn.execute(
                text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = '{_tbl}' AND column_name = 'company_id'
                    ) THEN
                        ALTER TABLE {_tbl} ADD COLUMN company_id UUID;
                    END IF;
                END $$;
            """)  # noqa: S608  # nosec B608
            )

        # v4.1.0: Ensure the companies table exists (CA multi-company model).
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS companies (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                name VARCHAR(255) NOT NULL,
                gstin VARCHAR(15),
                pan VARCHAR(10) NOT NULL,
                tan VARCHAR(10),
                cin VARCHAR(21),
                state_code VARCHAR(2),
                registered_address TEXT,
                industry VARCHAR(100),
                fy_start_month VARCHAR(2) NOT NULL DEFAULT '04',
                fy_end_month VARCHAR(2) NOT NULL DEFAULT '03',
                signatory_name VARCHAR(255),
                signatory_designation VARCHAR(100),
                signatory_email VARCHAR(255),
                compliance_email VARCHAR(255),
                dsc_serial VARCHAR(100),
                dsc_expiry DATE,
                pf_registration VARCHAR(50),
                esi_registration VARCHAR(50),
                pt_registration VARCHAR(50),
                bank_name VARCHAR(255),
                bank_account_number VARCHAR(50),
                bank_ifsc VARCHAR(11),
                bank_branch VARCHAR(255),
                tally_config JSONB,
                gst_auto_file BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                user_roles JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                UNIQUE (tenant_id, gstin)
            );
        """)
        )

        # v4.2.0: Add new columns to companies if missing.
        for _col, _type, _default in [
            ("subscription_status", "VARCHAR(20) NOT NULL DEFAULT 'trial'", None),
            ("client_health_score", "INT DEFAULT 100", None),
            ("document_vault_enabled", "BOOLEAN NOT NULL DEFAULT TRUE", None),
            ("compliance_alerts_email", "VARCHAR(255)", None),
        ]:
            await conn.execute(
                text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'companies' AND column_name = '{_col}'
                    ) THEN
                        ALTER TABLE companies ADD COLUMN {_col} {_type};
                    END IF;
                END $$;
            """)  # noqa: S608  # nosec B608
            )

        # v4.2.0: Ensure ca_subscriptions table exists.
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS ca_subscriptions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                plan VARCHAR(50) NOT NULL DEFAULT 'ca_pro',
                status VARCHAR(20) NOT NULL DEFAULT 'trial',
                max_clients INT NOT NULL DEFAULT 7,
                price_inr INT NOT NULL DEFAULT 4999,
                price_usd INT NOT NULL DEFAULT 59,
                billing_cycle VARCHAR(20) NOT NULL DEFAULT 'monthly',
                trial_ends_at TIMESTAMPTZ,
                current_period_start TIMESTAMPTZ,
                current_period_end TIMESTAMPTZ,
                cancelled_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                UNIQUE (tenant_id)
            );
        """)
        )
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS industry_pack_installs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                pack_name VARCHAR(100) NOT NULL,
                installed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                agent_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                workflow_ids JSONB NOT NULL DEFAULT '[]'::jsonb
            );
        """)
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_industry_pack_installs_tenant_id ON industry_pack_installs(tenant_id)")
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_industry_pack_installs_pack_name ON industry_pack_installs(pack_name)")
        )

        # v4.2.0: Ensure filing_approvals table exists.
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS filing_approvals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NOT NULL REFERENCES companies(id),
                filing_type VARCHAR(50) NOT NULL,
                filing_period VARCHAR(20) NOT NULL,
                filing_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                requested_by VARCHAR(255) NOT NULL,
                approved_by VARCHAR(255),
                approved_at TIMESTAMPTZ,
                rejection_reason TEXT,
                auto_approved BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ
            );
        """)
        )

        # v4.2.0: Ensure gstn_uploads table exists.
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS gstn_uploads (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NOT NULL REFERENCES companies(id),
                upload_type VARCHAR(50) NOT NULL,
                filing_period VARCHAR(20) NOT NULL,
                file_name VARCHAR(500) NOT NULL,
                file_path VARCHAR(1000),
                file_size_bytes BIGINT,
                status VARCHAR(20) NOT NULL DEFAULT 'generated',
                gstn_arn VARCHAR(100),
                uploaded_at TIMESTAMPTZ,
                uploaded_by VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ
            );
        """)
        )

        # v5.0.0: Ensure kpi_cache table exists.
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS kpi_cache (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID REFERENCES companies(id),
                role VARCHAR(20) NOT NULL,
                metric_name VARCHAR(100) NOT NULL,
                metric_value JSONB NOT NULL,
                source VARCHAR(50) NOT NULL DEFAULT 'agent',
                computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ttl_seconds INT NOT NULL DEFAULT 3600,
                stale BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kpi_cache_tenant_role ON kpi_cache(tenant_id, role)"))
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_kpi_cache_metric ON kpi_cache(tenant_id, role, metric_name)")
        )

        # v5.0.0: Ensure agent_task_results table exists.
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS agent_task_results (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                agent_id UUID NOT NULL,
                agent_type VARCHAR(100) NOT NULL,
                domain VARCHAR(50) NOT NULL,
                task_type VARCHAR(100) NOT NULL,
                task_input JSONB NOT NULL DEFAULT '{}'::jsonb,
                task_output JSONB NOT NULL DEFAULT '{}'::jsonb,
                confidence FLOAT,
                tool_calls JSONB DEFAULT '[]'::jsonb,
                llm_model VARCHAR(100),
                tokens_used INT DEFAULT 0,
                cost_usd FLOAT DEFAULT 0.0,
                duration_ms INT DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'completed',
                error_message TEXT,
                hitl_required BOOLEAN NOT NULL DEFAULT FALSE,
                hitl_decision VARCHAR(20),
                company_id UUID REFERENCES companies(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_results_tenant ON agent_task_results(tenant_id)"))
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_agent_results_domain ON agent_task_results(tenant_id, domain)")
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_agent_results_created ON agent_task_results(created_at)")
        )

        # v5.0.0: Ensure connector_configs table exists.
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS connector_configs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NULL REFERENCES companies(id) ON DELETE RESTRICT,
                connector_name VARCHAR(100) NOT NULL,
                display_name VARCHAR(255),
                auth_type VARCHAR(50) NOT NULL DEFAULT 'api_key',
                credentials_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
                config JSONB NOT NULL DEFAULT '{}'::jsonb,
                status VARCHAR(20) NOT NULL DEFAULT 'configured',
                last_health_check TIMESTAMPTZ,
                health_status VARCHAR(20) DEFAULT 'unknown',
                last_sync_at TIMESTAMPTZ,
                sync_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ
            );
        """)
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_connector_configs_tenant ON connector_configs(tenant_id)")
        )
        await conn.execute(
            text("ALTER TABLE connector_configs ADD COLUMN IF NOT EXISTS company_id UUID NULL")
        )
        await conn.execute(
            text("ALTER TABLE connector_configs DROP CONSTRAINT IF EXISTS uq_connector_config_tenant")
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_connector_configs_tenant_global "
                "ON connector_configs(tenant_id, connector_name) WHERE company_id IS NULL"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_connector_configs_tenant_company "
                "ON connector_configs(tenant_id, company_id, connector_name) "
                "WHERE company_id IS NOT NULL"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_connector_configs_tenant_company "
                "ON connector_configs(tenant_id, company_id)"
            )
        )
        await conn.execute(text("ALTER TABLE connector_configs ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text("ALTER TABLE connector_configs FORCE ROW LEVEL SECURITY"))
        await conn.execute(text("DROP POLICY IF EXISTS tenant_isolation ON connector_configs"))
        await conn.execute(
            text(
                "DROP POLICY IF EXISTS connector_configs_tenant_isolation "
                "ON connector_configs"
            )
        )
        await conn.execute(
            text(
                "DROP POLICY IF EXISTS connector_configs_scope_isolation "
                "ON connector_configs"
            )
        )
        await conn.execute(
            text("""
            CREATE POLICY connector_configs_scope_isolation ON connector_configs
            USING (
                tenant_id::text = current_setting('agenticorg.tenant_id', true)
                AND company_id IS NOT DISTINCT FROM
                    NULLIF(current_setting('agenticorg.company_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id::text = current_setting('agenticorg.tenant_id', true)
                AND company_id IS NOT DISTINCT FROM
                    NULLIF(current_setting('agenticorg.company_id', true), '')::uuid
            )
        """)
        )

        # v4.3.0: gstn_auto_upload flag on companies
        await conn.execute(
            text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'companies' AND column_name = 'gstn_auto_upload'
                ) THEN
                    ALTER TABLE companies ADD COLUMN gstn_auto_upload BOOLEAN NOT NULL DEFAULT FALSE;
                END IF;
            END $$;
        """)
        )

        # v4.3.0: Ensure gstn_credentials table exists.
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS gstn_credentials (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NOT NULL REFERENCES companies(id),
                gstin VARCHAR(15) NOT NULL,
                username VARCHAR(255) NOT NULL,
                password_encrypted TEXT NOT NULL,
                encryption_key_ref VARCHAR(100) NOT NULL DEFAULT 'default',
                portal_type VARCHAR(20) NOT NULL DEFAULT 'gstn',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_verified_at TIMESTAMPTZ,
                last_login_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                UNIQUE (company_id, portal_type)
            );
        """)
        )

        # v4.3.0: Ensure compliance_deadlines table exists.
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS compliance_deadlines (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NOT NULL REFERENCES companies(id),
                deadline_type VARCHAR(50) NOT NULL,
                filing_period VARCHAR(20) NOT NULL,
                due_date DATE NOT NULL,
                alert_7d_sent BOOLEAN NOT NULL DEFAULT FALSE,
                alert_1d_sent BOOLEAN NOT NULL DEFAULT FALSE,
                filed BOOLEAN NOT NULL DEFAULT FALSE,
                filed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                UNIQUE (company_id, deadline_type, filing_period)
            );
        """)
        )

        # ── v4.6.0: Enterprise readiness — run every startup, idempotent ──

        # 1. User i18n + department assignment
        for _col, _type in [
            ("timezone", "VARCHAR(64) NOT NULL DEFAULT 'UTC'"),
            ("locale", "VARCHAR(10) NOT NULL DEFAULT 'en'"),
            ("department_id", "UUID"),
        ]:
            await conn.execute(
                text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = '{_col}'
                    ) THEN
                        ALTER TABLE users ADD COLUMN {_col} {_type};
                    END IF;
                END $$;
            """)  # noqa: S608  # nosec B608
            )

        # 2. Company.currency (ISO 4217)
        await conn.execute(
            text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'companies' AND column_name = 'currency'
                ) THEN
                    ALTER TABLE companies ADD COLUMN currency CHAR(3) NOT NULL DEFAULT 'INR';
                END IF;
            END $$;
        """)
        )

        # 3. Departments + cost centers (org hierarchy)
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS departments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                code VARCHAR(50),
                parent_id UUID REFERENCES departments(id) ON DELETE SET NULL,
                manager_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, company_id, name)
            );
        """)
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_departments_tenant_company ON departments(tenant_id, company_id);")
        )

        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS cost_centers (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                department_id UUID REFERENCES departments(id) ON DELETE SET NULL,
                code VARCHAR(50) NOT NULL,
                name VARCHAR(255) NOT NULL,
                budget_limit NUMERIC(14, 2),
                fiscal_year INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, company_id, code)
            );
        """)
        )

        # Add FK from users.department_id to departments.id (now that the
        # table exists).  PostgreSQL doesn't support IF NOT EXISTS on FK so
        # we check information_schema.
        await conn.execute(
            text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE constraint_name = 'fk_users_department'
                      AND table_name = 'users'
                ) THEN
                    ALTER TABLE users
                    ADD CONSTRAINT fk_users_department
                    FOREIGN KEY (department_id)
                    REFERENCES departments(id) ON DELETE SET NULL;
                END IF;
            END $$;
        """)
        )

        # 4. Agent maturity + cost center pointer
        for _col, _type in [
            ("maturity", "VARCHAR(20) NOT NULL DEFAULT 'beta'"),
            ("cost_center_id", "UUID"),
        ]:
            await conn.execute(
                text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'agents' AND column_name = '{_col}'
                    ) THEN
                        ALTER TABLE agents ADD COLUMN {_col} {_type};
                    END IF;
                END $$;
            """)  # noqa: S608  # nosec B608
            )

        # 5. User delegation table (approval forwarding)
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS user_delegations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                delegator_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                delegate_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                reason VARCHAR(255),
                starts_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                ends_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_delegation_different_users CHECK (delegator_id <> delegate_id)
            );
        """)
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_delegations_active "
                "ON user_delegations(tenant_id, delegator_id) "
                "WHERE revoked_at IS NULL;"
            )
        )

        # 6. Feature flags table
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS feature_flags (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID,
                flag_key VARCHAR(100) NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT FALSE,
                rollout_percentage INTEGER NOT NULL DEFAULT 0
                    CHECK (rollout_percentage BETWEEN 0 AND 100),
                description VARCHAR(500),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, flag_key)
            );
        """)
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_feature_flags_key ON feature_flags(flag_key);"))

        # 7. Budget alerts table
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS budget_alerts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
                cost_center_id UUID REFERENCES cost_centers(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                period VARCHAR(20) NOT NULL,
                threshold_usd NUMERIC(14, 2) NOT NULL,
                warn_at_percent INTEGER NOT NULL DEFAULT 80
                    CHECK (warn_at_percent BETWEEN 1 AND 100),
                notify_channels VARCHAR(255) NOT NULL DEFAULT 'email',
                last_triggered_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        )

        # 8. SSO configuration per tenant
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS sso_configs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                provider_key VARCHAR(50) NOT NULL,
                provider_type VARCHAR(20) NOT NULL DEFAULT 'oidc',
                display_name VARCHAR(100) NOT NULL,
                config JSONB NOT NULL DEFAULT '{}'::jsonb,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                jit_provisioning BOOLEAN NOT NULL DEFAULT TRUE,
                default_role VARCHAR(50) NOT NULL DEFAULT 'analyst',
                allowed_domains JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, provider_key)
            );
        """)
        )

        # 8b. Tenant BYOK KEK resource — customer-managed KMS key
        await conn.execute(
            text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'tenants' AND column_name = 'byok_kek_resource'
                ) THEN
                    ALTER TABLE tenants ADD COLUMN byok_kek_resource VARCHAR(500) NOT NULL DEFAULT '';
                END IF;
            END $$;
        """)
        )

        # 8c. Invoices
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS invoices (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                invoice_number VARCHAR(50) NOT NULL,
                period_start TIMESTAMPTZ NOT NULL,
                period_end TIMESTAMPTZ NOT NULL,
                issue_date DATE NOT NULL,
                due_date DATE NOT NULL,
                currency CHAR(3) NOT NULL DEFAULT 'USD',
                subtotal NUMERIC(14, 2) NOT NULL,
                tax NUMERIC(14, 2) NOT NULL DEFAULT 0,
                total NUMERIC(14, 2) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'draft',
                line_items JSONB NOT NULL DEFAULT '[]'::jsonb,
                pdf_url VARCHAR(500),
                payment_provider VARCHAR(20),
                payment_ref VARCHAR(100),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, invoice_number)
            );
        """)
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_invoices_tenant_period ON invoices(tenant_id, period_start);")
        )

        # 9. Approval policies (configurable multi-step approval chains)
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS approval_policies (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                name VARCHAR(100) NOT NULL,
                description VARCHAR(500),
                workflow_id UUID,
                agent_id UUID,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, name)
            );
        """)
        )

        # v4.7.0 hotfix: the initial v4.7.0 ship created approval_policies
        # with is_active as VARCHAR(10). Convert to BOOLEAN if still varchar.
        await conn.execute(
            text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'approval_policies'
                      AND column_name = 'is_active'
                      AND data_type = 'character varying'
                ) THEN
                    ALTER TABLE approval_policies
                        ALTER COLUMN is_active DROP DEFAULT,
                        ALTER COLUMN is_active TYPE BOOLEAN
                        USING (is_active::text IN ('true', 't', '1')),
                        ALTER COLUMN is_active SET DEFAULT TRUE;
                END IF;
            END $$;
        """)
        )
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS approval_steps (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                policy_id UUID NOT NULL REFERENCES approval_policies(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                approver_role VARCHAR(50) NOT NULL,
                quorum_required INTEGER NOT NULL DEFAULT 1,
                quorum_total INTEGER NOT NULL DEFAULT 1,
                mode VARCHAR(20) NOT NULL DEFAULT 'sequential',
                condition VARCHAR(500),
                step_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE (policy_id, sequence),
                CHECK (quorum_required >= 1),
                CHECK (quorum_required <= quorum_total)
            );
        """)
        )

        # 9a. Tenant branding (white-label)
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS tenant_branding (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL UNIQUE,
                product_name VARCHAR(100) NOT NULL DEFAULT 'AgenticOrg',
                logo_url VARCHAR(500),
                favicon_url VARCHAR(500),
                primary_color VARCHAR(7) NOT NULL DEFAULT '#7c3aed',
                accent_color VARCHAR(7) NOT NULL DEFAULT '#1e293b',
                custom_domain VARCHAR(255),
                support_email VARCHAR(255),
                footer_text VARCHAR(500),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        )

        # 9b. Workflow A/B variants
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS workflow_variants (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                workflow_id UUID NOT NULL,
                variant_name VARCHAR(100) NOT NULL,
                weight INTEGER NOT NULL DEFAULT 50 CHECK (weight BETWEEN 0 AND 100),
                definition JSONB NOT NULL DEFAULT '{}'::jsonb,
                run_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (workflow_id, variant_name)
            );
        """)
        )

        # 9d. v4.4.0 safety net — report_schedules.
        # The v4.4.0 alembic migration created this table, but envs that
        # were stamped past v4.4.0 without ever running it (e.g. prod
        # 2026-04-22 cutover) ended up missing the table. The 2026-04-22
        # company_id migration explicitly guards on table existence and
        # logs that this safety-net path is the canonical creator. The
        # 27-Apr TC_001 reopen (Aishwarya, "report schedule create 500")
        # surfaced because instrumentation revealed
        # `relation "report_schedules" does not exist`. Idempotent on
        # repeat startups.
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS report_schedules (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NULL,
                name VARCHAR(200) NOT NULL,
                report_type VARCHAR(50) NOT NULL,
                cron_expression VARCHAR(100) NOT NULL,
                recipients JSONB NOT NULL DEFAULT '[]'::jsonb,
                delivery_channel VARCHAR(20) NOT NULL DEFAULT 'email',
                format VARCHAR(10) NOT NULL DEFAULT 'pdf',
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                last_run_at TIMESTAMPTZ,
                next_run_at TIMESTAMPTZ,
                config JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ
            );
        """)
        )
        # If the table already existed from an older env without the
        # v488 company_id migration, ensure the column is present.
        await conn.execute(
            text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'report_schedules'
                      AND column_name = 'company_id'
                ) THEN
                    ALTER TABLE report_schedules ADD COLUMN company_id UUID NULL;
                END IF;
            END $$;
        """)
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_report_schedules_tenant ON report_schedules(tenant_id);")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_report_schedules_tenant_company "
                "ON report_schedules(tenant_id, company_id);"
            )
        )

        # 9c. RLS for ALL v4.7 tenant-scoped tables
        # (Missing from the original v4.7.0 ship — found in gap analysis #6)
        _v47_rls_tables = [
            "sso_configs",
            "approval_policies",
            "invoices",
            "tenant_branding",
            "workflow_variants",
            "report_schedules",
        ]
        for _rls_tbl in _v47_rls_tables:
            await conn.execute(text(f"ALTER TABLE {_rls_tbl} ENABLE ROW LEVEL SECURITY;"))  # noqa: S608
            await conn.execute(text(f"ALTER TABLE {_rls_tbl} FORCE ROW LEVEL SECURITY;"))  # noqa: S608
            await conn.execute(
                text(
                    f"DROP POLICY IF EXISTS {_rls_tbl}_tenant_isolation ON {_rls_tbl};"  # noqa: S608
                )
            )
            await conn.execute(
                text(
                    f"CREATE POLICY {_rls_tbl}_tenant_isolation ON {_rls_tbl} "  # noqa: S608
                    "USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));"
                )
            )
        # approval_steps is a child of approval_policies — RLS via FK cascade,
        # but add direct policy too for defense in depth.
        await conn.execute(text("ALTER TABLE approval_steps ENABLE ROW LEVEL SECURITY;"))
        await conn.execute(text("ALTER TABLE approval_steps FORCE ROW LEVEL SECURITY;"))
        await conn.execute(text("DROP POLICY IF EXISTS approval_steps_tenant_isolation ON approval_steps;"))
        await conn.execute(
            text("""
            CREATE POLICY approval_steps_tenant_isolation ON approval_steps
            USING (policy_id IN (
                SELECT id FROM approval_policies
                WHERE tenant_id::text = current_setting('agenticorg.tenant_id', true)
            ));
        """)
        )

        # 10. Audit log immutability trigger — rejects UPDATE/DELETE
        await conn.execute(
            text("""
            CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION
                  'audit_log is append-only — UPDATE/DELETE rejected'
                  USING ERRCODE = 'insufficient_privilege';
            END;
            $$ LANGUAGE plpgsql;
        """)
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;"))
        await conn.execute(
            text("""
            CREATE TRIGGER audit_log_immutable
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();
        """)
        )

    await _seed_demo_ca_companies_if_enabled()


async def _seed_demo_ca_companies_if_enabled() -> None:
    """Seed demo CA companies in relaxed demo/dev environments only."""
    if os.getenv("AGENTICORG_ENV", "production").lower() not in ("demo", "development", "dev"):
        return
    try:
        from core.seed_ca_demo import seed_ca_demo

        async with async_session_factory() as session:
            await seed_ca_demo(session)
            await session.commit()
    # enterprise-gate: broad-except-ok reason=demo-seed-failure-is-relaxed-env-sidecar-only
    except Exception as exc:
        logger.debug("CA demo seed skipped: %s", exc)


async def close_db() -> None:
    """Run on shutdown."""
    await engine.dispose()
