"""Async SQLAlchemy engine, session management, and tenant RLS middleware."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass

from core.config import settings


class Base(DeclarativeBase, MappedAsDataclass):
    """Declarative base for all ORM models."""

    pass


engine: AsyncEngine = create_async_engine(
    settings.db_url,
    echo=settings.env == "development",
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_tenant_session(tenant_id: UUID) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session with RLS tenant context set."""
    async with async_session_factory() as session:
        # asyncpg does not support bound parameters in SET statements,
        # so we sanitise the UUID and interpolate directly.
        safe_tid = str(tenant_id).replace("'", "")
        await session.execute(text(f"SET LOCAL agenticorg.tenant_id = '{safe_tid}'"))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a raw session (for non-tenant-scoped operations like health checks)."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Run on startup — verify connectivity and apply safe schema additions."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

        # v4.0.0: Ensure prompt_amendments column exists on agents table.
        # Safe to run every startup (IF NOT EXISTS check).
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'agents' AND column_name = 'prompt_amendments'
                ) THEN
                    ALTER TABLE agents ADD COLUMN prompt_amendments JSONB DEFAULT '[]'::jsonb;
                END IF;
            END $$;
        """))


async def close_db() -> None:
    """Run on shutdown."""
    await engine.dispose()
