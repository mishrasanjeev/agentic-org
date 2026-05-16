from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from core.cdc.receiver import SqlAlchemyCDCEventStore, handle_cdc_webhook, replay_cdc_event
from core.models.cdc import CDCEvent, CDCEventDeadLetter


async def _isolated_session_factory(db_engine: AsyncEngine):
    import core.models  # noqa: F401 - registers all ORM models
    from core.models.base import BaseModel as ORMBase

    engine = create_async_engine(
        db_engine.url.render_as_string(hide_password=False),
        echo=False,
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(ORMBase.metadata.create_all)
    return engine, async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def _sign(payload: dict, secret: str = "test-secret") -> str:  # noqa: S107
    return hmac.new(
        secret.encode(),
        json.dumps(payload, sort_keys=True).encode(),
        hashlib.sha256,
    ).hexdigest()


@pytest.mark.asyncio
async def test_cdc_event_store_persists_and_dedupes_in_postgres(
    db_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDC_WEBHOOK_SECRET_XERO", "test-secret")
    engine, session_factory = await _isolated_session_factory(db_engine)
    tenant_id = uuid.uuid4()
    payload = {
        "event_id": "evt-pg-1",
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-pg-1",
    }
    store_a = SqlAlchemyCDCEventStore(session_factory)
    store_b = SqlAlchemyCDCEventStore(session_factory)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
                    "VALUES (:id, 'cdc tenant', :slug, 'enterprise', 'IN', '{}'::jsonb) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant_id, "slug": f"cdc-{tenant_id.hex[:8]}"},
            )

        first = await handle_cdc_webhook(
            str(tenant_id),
            "xero",
            payload,
            _sign(payload),
            store=store_a,
        )
        second = await handle_cdc_webhook(
            str(tenant_id),
            "xero",
            payload,
            _sign(payload),
            store=store_b,
        )

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(CDCEvent).where(CDCEvent.tenant_id == tenant_id)
                )
            ).scalars().all()

        assert first["status"] == "accepted"
        assert second["status"] == "duplicate"
        assert len(rows) == 1
        assert rows[0].provider_event_id == "evt-pg-1"
        assert rows[0].processing_status == "processed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cdc_replay_failure_writes_postgres_dead_letter(
    db_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDC_WEBHOOK_SECRET_XERO", "test-secret")
    engine, session_factory = await _isolated_session_factory(db_engine)
    tenant_id = uuid.uuid4()
    payload = {
        "event_id": "evt-pg-fail",
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-pg-fail",
    }
    store = SqlAlchemyCDCEventStore(session_factory)

    def _boom(event: dict, tenant_id: str) -> list[str]:
        raise RuntimeError("trigger failure")

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
                    "VALUES (:id, 'cdc tenant', :slug, 'enterprise', 'IN', '{}'::jsonb) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant_id, "slug": f"cdc-{tenant_id.hex[:8]}"},
            )

        monkeypatch.setattr("core.cdc.triggers.evaluate_triggers", _boom)
        accepted = await handle_cdc_webhook(
            str(tenant_id),
            "xero",
            payload,
            _sign(payload),
            store=store,
        )
        event_id = accepted["event_id"]

        replay = await replay_cdc_event(
            tenant_id=str(tenant_id),
            event_id=event_id,
            actor="admin",
            store=store,
        )

        async with session_factory() as session:
            event = (
                await session.execute(
                    select(CDCEvent).where(CDCEvent.id == uuid.UUID(event_id))
                )
            ).scalar_one()
            dead_letters = (
                await session.execute(
                    select(CDCEventDeadLetter).where(
                        CDCEventDeadLetter.cdc_event_id == uuid.UUID(event_id)
                    )
                )
            ).scalars().all()

        assert accepted["processing_status"] == "dead_lettered"
        assert replay["status"] == "failed"
        assert event.processing_status == "dead_lettered"
        assert event.replay_status == "failed"
        assert len(dead_letters) == 2
    finally:
        await engine.dispose()
