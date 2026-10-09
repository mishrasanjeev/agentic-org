# SPDX-License-Identifier: Apache-2.0
"""Atomic transaction ingestion and detection under concurrent requests and forced tenant RLS."""

from __future__ import annotations

import asyncio
import importlib
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.selectable import Select

from core.lineage import provenance
from core.models.txn_finding import TxnFinding
from core.models.txn_record import TxnRecord
from core.txn import detectors, findings, records

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="Requires local PostgreSQL")
migration = importlib.import_module("migrations.versions.v6_z73_txn_intelligence")
narrative_migration = importlib.import_module("migrations.versions.v6_z74_txn_narratives")


@pytest.fixture
def txn_schema():
    suffix = uuid.uuid4().hex
    schema = "txn_ingest_" + suffix
    role = "txn_ingest_probe_" + suffix
    engine = create_engine(DB_URL.replace("postgresql+asyncpg", "postgresql"), poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
            connection.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
            connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": schema})
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                narrative_migration.upgrade()
            connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}'))
            connection.execute(text(f'GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA "{schema}" TO {role}'))
        yield schema, role
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        engine.dispose()


def movement(ref: str, account: str = "synthetic-account", *, amount: int = 10, source: str = "api") -> dict:
    return {
        "record_ref": ref,
        "account": account,
        "direction": "credit",
        "amount": amount,
        "booked_at": "2026-10-01T09:00:00Z",
        "source": source,
    }


@asynccontextmanager
async def txn_store(txn_schema, monkeypatch, *, participants: int = 0, target: str = "txn_records"):
    import core.database

    schema, role = txn_schema
    engine = create_async_engine(DB_URL, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    barrier = asyncio.Barrier(participants) if participants else None

    class RacingSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            key = "record_ref" if target == "txn_records" else "fingerprint"
            first_read = isinstance(statement, Select) and str(statement).startswith(f"SELECT {target}.{key}")
            target_insert = isinstance(statement, Insert) and statement.table.name == target
            if barrier and target_insert:
                await asyncio.wait_for(barrier.wait(), timeout=10)
            result = await super().execute(statement, *args, **kwargs)
            if barrier and first_read:
                # Both legacy pre-reads see no row before either caller can add one.
                await asyncio.wait_for(barrier.wait(), timeout=10)
            return result

    factory = async_sessionmaker(engine, class_=RacingSession, expire_on_commit=False)

    @asynccontextmanager
    async def session_for():
        async with factory() as session:
            await session.execute(text(f"SET LOCAL ROLE {role}"))
            yield session

    monkeypatch.setattr(core.database, "current_session_factory", lambda: session_for)
    noted = AsyncMock()
    monkeypatch.setattr(provenance, "on_records", noted)
    try:
        yield engine, noted
    finally:
        await engine.dispose()


def structured_movements(account: str = "synthetic-account") -> list[dict]:
    return [
        dict(
            movement(f"{account}-{index}", account, amount=400_000),
            channel="cash",
            booked_at=datetime.now(UTC).isoformat(),
        )
        for index in range(3)
    ]


@pytest.mark.asyncio
async def test_concurrent_detector_runs_keep_one_finding_without_unique_errors(txn_schema, monkeypatch):
    tenant = uuid.uuid4()
    callers = 8
    monkeypatch.setattr(findings, "thresholds_for", AsyncMock(return_value=detectors.Thresholds()))
    async with txn_store(txn_schema, monkeypatch, participants=callers, target="txn_findings") as (engine, _):
        assert (await records.ingest(tenant, structured_movements()))["kept"] == 3
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(findings.detect(tenant) for _ in range(callers)), return_exceptions=True),
            timeout=30,
        )
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert failures == [], f"Concurrent detection failed: {[type(error).__name__ for error in failures]}"
        assert sorted((outcome["new"], outcome["known"]) for outcome in outcomes) == [(0, 1)] * 7 + [(1, 0)]
        assert sum(len(outcome["findings"]) for outcome in outcomes) == 1
        async with engine.connect() as connection:
            stored = (await connection.execute(select(TxnFinding.__table__))).mappings().one()
        assert stored["tenant_id"] == tenant and stored["status"] == "open"
        assert len(stored["record_refs"]) == 3


@pytest.mark.asyncio
async def test_identical_fingerprints_in_different_tenants_remain_independent(txn_schema, monkeypatch):
    import core.database

    first, second = uuid.uuid4(), uuid.uuid4()
    movements = structured_movements()
    monkeypatch.setattr(findings, "thresholds_for", AsyncMock(return_value=detectors.Thresholds()))
    async with txn_store(txn_schema, monkeypatch, participants=2, target="txn_findings"):
        for tenant in (first, second):
            await records.ingest(tenant, movements)
        outcomes = await asyncio.wait_for(asyncio.gather(findings.detect(first), findings.detect(second)), timeout=30)
        assert [outcome["new"] for outcome in outcomes] == [1, 1]
        fingerprint = outcomes[0]["findings"][0]["fingerprint"]
        assert outcomes[1]["findings"][0]["fingerprint"] == fingerprint
        for tenant in (first, second):
            async with core.database.get_tenant_session(tenant) as session:
                rows = (await session.execute(select(TxnFinding))).scalars().all()
                assert [(row.tenant_id, row.fingerprint) for row in rows] == [(tenant, fingerprint)]


@pytest.mark.asyncio
async def test_detector_replay_preserves_human_disposition_and_narrative(txn_schema, monkeypatch):
    tenant = uuid.uuid4()
    monkeypatch.setattr(findings, "thresholds_for", AsyncMock(return_value=detectors.Thresholds()))
    async with txn_store(txn_schema, monkeypatch) as (engine, _):
        await records.ingest(tenant, structured_movements())
        assert (await findings.detect(tenant))["new"] == 1
        disposition = {"outcome": "confirmed", "by": "synthetic-reviewer", "notes": "Reviewed evidence"}
        narrative = {"method": "template", "summary": "Reviewed draft"}
        async with engine.begin() as connection:
            await connection.execute(
                TxnFinding.__table__.update()
                .where(TxnFinding.tenant_id == tenant)
                .values(status="confirmed", disposition=disposition, narrative=narrative, case_ref="synthetic-case")
            )
        replay = await findings.detect(tenant)
        assert replay["new"] == 0 and replay["known"] == 1 and replay["findings"] == []
        async with engine.connect() as connection:
            stored = (await connection.execute(select(TxnFinding.__table__))).mappings().one()
        assert stored["status"] == "confirmed" and stored["disposition"] == disposition
        assert stored["narrative"] == narrative and stored["case_ref"] == "synthetic-case"


@pytest.mark.asyncio
async def test_large_detector_result_is_inserted_without_exceeding_database_parameter_limit(txn_schema, monkeypatch):
    tenant = uuid.uuid4()
    movements = []
    for index in range(1250):
        account = f"synthetic-{index:04d}"
        credit_records = structured_movements(account)
        movements.extend(credit_records)
        movements.append(
            dict(
                movement(f"{account}-debit", account, amount=400_000),
                direction="debit",
                booked_at=(datetime.fromisoformat(credit_records[-1]["booked_at"]) + timedelta(minutes=1)).isoformat(),
            )
        )
    monkeypatch.setattr(findings, "thresholds_for", AsyncMock(return_value=detectors.Thresholds()))
    async with txn_store(txn_schema, monkeypatch) as (engine, _):
        for start in range(0, len(movements), records.MAX_BATCH):
            assert (await records.ingest(tenant, movements[start : start + records.MAX_BATCH]))["kept"] == 500
        detected = await findings.detect(tenant)
        assert detected["records"] == 5000 and detected["new"] == 2500 and detected["known"] == 0
        async with engine.connect() as connection:
            stored = (await connection.execute(select(TxnFinding.fingerprint))).scalars().all()
        assert len(stored) == len(set(stored)) == 2500
        replay = await findings.detect(tenant)
        assert replay["new"] == 0 and replay["known"] == 2500


@pytest.mark.asyncio
async def test_concurrent_same_tenant_reference_is_skipped_and_not_given_duplicate_provenance(txn_schema, monkeypatch):
    tenant = uuid.uuid4()
    callers = 8
    async with txn_store(txn_schema, monkeypatch, participants=callers) as (engine, noted):
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                *(records.ingest(tenant, [movement("same-ref", f"account-{i}", amount=i + 1)]) for i in range(callers)),
                return_exceptions=True,
            ),
            timeout=30,
        )
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert failures == [], f"Concurrent ingestion failed: {[type(error).__name__ for error in failures]}"
        assert sorted((outcome["kept"], outcome["skipped"]) for outcome in outcomes) == [(0, 1)] * 7 + [(1, 0)]
        noted.assert_awaited_once()
        assert noted.call_args.args == (tenant,)
        assert noted.call_args.kwargs["source"] == "api"
        acquired = noted.call_args.kwargs["records"]
        assert len(acquired) == 1 and acquired[0]["record_ref"] == "same-ref"
        async with engine.connect() as connection:
            stored = (await connection.execute(select(TxnRecord.__table__))).mappings().one()
        assert stored["tenant_id"] == tenant
        assert (stored["account"], float(stored["amount"])) == (acquired[0]["account"], acquired[0]["amount"])


@pytest.mark.asyncio
async def test_same_reference_in_different_tenants_is_independent_and_rls_is_preserved(txn_schema, monkeypatch):
    import core.database

    first, second = uuid.uuid4(), uuid.uuid4()
    async with txn_store(txn_schema, monkeypatch, participants=2) as (_, noted):
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                records.ingest(first, [movement("shared-ref", "first-account")]),
                records.ingest(second, [movement("shared-ref", "second-account")]),
            ),
            timeout=30,
        )
        assert [outcome["kept"] for outcome in outcomes] == [1, 1]
        assert {call.args[0] for call in noted.call_args_list} == {first, second}
        assert noted.await_count == 2
        for tenant, expected in ((first, "first-account"), (second, "second-account")):
            async with core.database.get_tenant_session(tenant) as session:
                # No application WHERE clause: the migrated RLS policy enforces isolation.
                rows = (await session.execute(select(TxnRecord))).scalars().all()
                assert [(row.tenant_id, row.account) for row in rows] == [(tenant, expected)]
            assert [row["account"] for row in await records.list_by_refs(tenant, ["shared-ref"])] == [expected]


@pytest.mark.asyncio
async def test_replays_and_batch_duplicates_keep_first_payload_and_only_new_provenance(txn_schema, monkeypatch):
    tenant = uuid.uuid4()
    async with txn_store(txn_schema, monkeypatch) as (engine, noted):
        assert (await records.ingest(tenant, [movement("existing", source="original")]))["kept"] == 1
        noted.reset_mock()
        out = await records.ingest(
            tenant,
            [
                movement("existing", "replayed-account", amount=99, source="ignored"),
                movement("new", "first-account", source="statement:synthetic"),
                movement("new", "duplicate-account", amount=99, source="ignored"),
                movement("another", "other-account", source="switch"),
            ],
        )
        assert out == {
            "received": 4,
            "kept": 2,
            "skipped": 2,
            "accounts": ["duplicate-account", "first-account", "other-account", "replayed-account"],
        }
        assert {(call.kwargs["source"], call.kwargs["records"][0]["record_ref"]) for call in noted.call_args_list} == {
            ("statement:synthetic", "new"),
            ("switch", "another"),
        }
        async with engine.connect() as connection:
            rows = (await connection.execute(select(TxnRecord.__table__))).mappings().all()
        stored = {row["record_ref"]: row for row in rows}
        assert len(stored) == 3
        assert stored["existing"]["source"] == "original" and float(stored["existing"]["amount"]) == 10
        assert stored["new"]["account"] == "first-account" and float(stored["new"]["amount"]) == 10
        noted.reset_mock()
        replay = await records.ingest(tenant, [movement("existing"), movement("new"), movement("another")])
        assert replay["kept"] == 0 and replay["skipped"] == 3
        noted.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_duplicate_aborts_entire_batch_without_records_or_provenance(txn_schema, monkeypatch):
    tenant = uuid.uuid4()
    async with txn_store(txn_schema, monkeypatch) as (engine, noted):
        with pytest.raises(records.TxnError) as refused:
            await records.ingest(tenant, [movement("same-ref"), movement("same-ref", amount=-1)])
        assert refused.value.status == 422 and refused.value.code == "record_invalid"
        async with engine.connect() as connection:
            assert (await connection.execute(select(TxnRecord.__table__))).all() == []
        noted.assert_not_awaited()


@pytest.mark.asyncio
async def test_overlapping_batches_in_reverse_order_report_only_their_inserted_rows(txn_schema, monkeypatch):
    tenant = uuid.uuid4()
    async with txn_store(txn_schema, monkeypatch, participants=2) as (engine, noted):
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                records.ingest(
                    tenant,
                    [movement("shared-a", amount=1), movement("own-a"), movement("shared-b", amount=2)],
                    source="first-batch",
                ),
                records.ingest(
                    tenant,
                    [movement("shared-b", amount=3), movement("own-b"), movement("shared-a", amount=4)],
                    source="second-batch",
                ),
            ),
            timeout=30,
        )
        assert sum(outcome["kept"] for outcome in outcomes) == 4
        assert sum(outcome["skipped"] for outcome in outcomes) == 2
        acquired = [item for call in noted.call_args_list for item in call.kwargs["records"]]
        assert len(acquired) == 4 and len({item["record_ref"] for item in acquired}) == 4
        async with engine.connect() as connection:
            stored = (await connection.execute(select(TxnRecord.__table__))).mappings().all()
        assert {(row["record_ref"], float(row["amount"]), row["source"]) for row in stored} == {
            (item["record_ref"], item["amount"], item["source"]) for item in acquired
        }
        for outcome, source in zip(outcomes, ("first-batch", "second-batch"), strict=True):
            assert outcome["kept"] == sum(item["source"] == source for item in acquired)


@pytest.mark.asyncio
async def test_payload_tenant_is_ignored_and_mismatched_rls_writes_are_rejected(txn_schema, monkeypatch):
    import core.database

    first, second = uuid.uuid4(), uuid.uuid4()
    async with txn_store(txn_schema, monkeypatch) as (engine, noted):
        payload = dict(movement("scoped-ref"), tenant_id=str(second))
        assert (await records.ingest(first, [payload]))["kept"] == 1
        noted.assert_awaited_once()
        assert noted.call_args.args == (first,)
        with pytest.raises(DBAPIError, match="row-level security"):
            async with core.database.get_tenant_session(first) as session:
                await session.execute(
                    TxnRecord.__table__.insert().values(tenant_id=second, **records.check_record(movement("refused")))
                )
        async with engine.connect() as connection:
            rows = (await connection.execute(select(TxnRecord.__table__))).mappings().all()
        assert [(row["tenant_id"], row["record_ref"]) for row in rows] == [(first, "scoped-ref")]
