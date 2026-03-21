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

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


@asynccontextmanager
async def get_tenant_session(tenant_id: UUID) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session with RLS tenant context set."""
    async with async_session_factory() as session:
        await session.execute(
            text("SET LOCAL agenticorg.tenant_id = :tid"),
            {"tid": str(tenant_id)},
        )
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
    """Run on startup — verify connectivity."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))


async def close_db() -> None:
    """Run on shutdown."""
    await engine.dispose()
