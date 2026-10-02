# SPDX-License-Identifier: Apache-2.0
"""The tamper-evident audit chain: sealing, verification, the tasks and the evidence section."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.config import settings
from core.governance import audit_chain
from core.tool_gateway.audit_logger import sign_audit_record

TENANT = uuid.uuid4()
KEY = settings.secret_key.encode()


def _row(index: int, *, signed: bool = True, **over) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "event_type": "tool.list_ledgers",
        "actor_type": "agent",
        "actor_id": f"agent-{index}",
        "agent_id": None,
        "workflow_run_id": None,
        "resource_type": "tool_call",
        "resource_id": "list_ledgers",
        "action": "execute",
        "outcome": "success",
        "details": {"index": index},
        "trace_id": "",
        "created_at": datetime(2026, 10, 2, 10, 0, tzinfo=UTC) + timedelta(seconds=index),
        "signature": None,
        "chain_seq": None,
        "chain_prev": None,
        "chain_hash": None,
        "sealed_at": None,
    }
    base.update(over)
    row = SimpleNamespace(**base)
    if signed:
        row.signature = sign_audit_record(row, KEY)
    return row


class _Store:
    """An in-memory audit table the query seams read from."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def sealed(self) -> list[SimpleNamespace]:
        return sorted((r for r in self.rows if r.chain_seq is not None), key=lambda r: r.chain_seq)

    async def head_row(self, _session, _tid, *, lock=False):
        sealed = self.sealed()
        return sealed[-1] if sealed else None

    async def unsealed_rows(self, _session, _tid, limit):
        waiting = sorted((r for r in self.rows if r.chain_seq is None), key=lambda r: (r.created_at, str(r.id)))
        return waiting[:limit]

    async def unsealed_count(self, _session, _tid):
        return sum(1 for r in self.rows if r.chain_seq is None)

    async def sealed_rows(self, _session, _tid, from_seq, limit):
        return [r for r in self.sealed() if r.chain_seq >= from_seq][:limit]


@pytest.fixture
def store(monkeypatch):
    rows: list[SimpleNamespace] = []
    table = _Store(rows)
    monkeypatch.setattr(audit_chain, "_head_row", table.head_row)
    monkeypatch.setattr(audit_chain, "_unsealed_rows", table.unsealed_rows)
    monkeypatch.setattr(audit_chain, "_unsealed_count", table.unsealed_count)
    monkeypatch.setattr(audit_chain, "_sealed_rows", table.sealed_rows)

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield object()

    monkeypatch.setattr("core.database.get_tenant_session", _ctx)
    return table


def _seal_all(table: _Store, batch: int = 100) -> audit_chain.SealOutcome:
    outcome = asyncio.run(audit_chain.seal(TENANT, batch=batch))
    while outcome.more:
        outcome = asyncio.run(audit_chain.seal(TENANT, batch=batch))
    return outcome


class TestLink:
    def test_the_link_depends_on_the_previous_link_the_payload_and_the_signature(self):
        row = _row(1)
        first = audit_chain.link_hash(audit_chain.GENESIS, row)
        assert len(first) == 64 and first == audit_chain.link_hash(audit_chain.GENESIS, row)
        assert audit_chain.link_hash("a" * 64, row) != first
        edited = _row(1, details={"index": 1, "amount": 10})
        assert audit_chain.link_hash(audit_chain.GENESIS, edited) != first
        unsigned = _row(1, signed=False)
        assert audit_chain.link_hash(audit_chain.GENESIS, unsigned) != first


class TestSeal:
    def test_seals_in_write_order_from_the_genesis_value(self, store):
        store.rows.extend(_row(i) for i in (3, 1, 2))
        outcome = asyncio.run(audit_chain.seal(TENANT, batch=10))
        assert outcome.sealed == 3 and outcome.more is False and outcome.head.seq == 3
        sealed = store.sealed()
        assert [r.actor_id for r in sealed] == ["agent-1", "agent-2", "agent-3"]
        assert sealed[0].chain_prev == audit_chain.GENESIS
        assert sealed[1].chain_prev == sealed[0].chain_hash and sealed[2].chain_prev == sealed[1].chain_hash
        assert sealed[2].chain_hash == outcome.head.hash and all(r.sealed_at is not None for r in sealed)

    def test_continues_from_the_existing_head_in_batches(self, store):
        store.rows.extend(_row(i) for i in range(1, 6))
        first = asyncio.run(audit_chain.seal(TENANT, batch=2))
        assert first.sealed == 2 and first.more is True and first.head.seq == 2
        second = asyncio.run(audit_chain.seal(TENANT, batch=2))
        assert second.head.seq == 4 and store.sealed()[2].chain_prev == first.head.hash
        third = asyncio.run(audit_chain.seal(TENANT, batch=2))
        assert third.sealed == 1 and third.more is False and third.head.seq == 5
        assert asyncio.run(audit_chain.seal(TENANT, batch=2)).sealed == 0

    def test_nothing_to_seal_keeps_the_head(self, store):
        outcome = asyncio.run(audit_chain.seal(TENANT))
        assert outcome.sealed == 0 and outcome.head == audit_chain.Head()


class TestVerify:
    def test_an_intact_chain_verifies_and_counts_unsigned_rows(self, store):
        store.rows.extend(_row(i) for i in range(1, 8))
        store.rows.append(_row(8, signed=False))
        _seal_all(store, batch=3)
        store.rows.append(_row(9))
        result = asyncio.run(audit_chain.verify(TENANT))
        assert result.status == "verified" and result.verified == 8 and result.unsigned == 1
        assert result.checked_from == 1 and result.checked_to == 8 and result.unsealed == 1
        assert result.first_break is None and result.head.seq == 8
        assert result.to_dict()["status"] == "verified"

    def test_an_empty_chain_is_empty(self, store):
        store.rows.append(_row(1))
        result = asyncio.run(audit_chain.verify(TENANT))
        assert result.status == "empty" and result.verified == 0 and result.unsealed == 1

    def test_an_edited_row_breaks_its_link(self, store):
        store.rows.extend(_row(i) for i in range(1, 6))
        _seal_all(store)
        store.sealed()[2].details = {"index": 3, "amount": "changed"}
        result = asyncio.run(audit_chain.verify(TENANT))
        assert result.status == "broken" and result.verified == 2
        assert result.first_break is not None and (result.first_break.seq, result.first_break.reason) == (
            3,
            "link_hash",
        )
        assert result.first_break.row_id == str(store.sealed()[2].id)

    def test_a_removed_row_leaves_a_gap(self, store):
        store.rows.extend(_row(i) for i in range(1, 6))
        _seal_all(store)
        removed = store.sealed()[1]
        store.rows.remove(removed)
        result = asyncio.run(audit_chain.verify(TENANT))
        assert (result.first_break.seq, result.first_break.reason) == (3, "sequence_gap") and result.verified == 1

    def test_reordered_rows_break_the_previous_link(self, store):
        store.rows.extend(_row(i) for i in range(1, 6))
        _seal_all(store)
        second, third = store.sealed()[1], store.sealed()[2]
        second.chain_seq, third.chain_seq = 3, 2
        result = asyncio.run(audit_chain.verify(TENANT))
        assert (result.first_break.seq, result.first_break.reason) == (2, "previous_link") and result.verified == 1

    def test_a_forged_signature_is_a_break_of_its_own(self, store):
        store.rows.extend(_row(i) for i in range(1, 4))
        _seal_all(store)
        row = store.sealed()[1]
        row.signature = "f" * 64
        row.chain_hash = audit_chain.link_hash(row.chain_prev, row)
        store.sealed()[2].chain_prev = row.chain_hash
        store.sealed()[2].chain_hash = audit_chain.link_hash(row.chain_hash, store.sealed()[2])
        result = asyncio.run(audit_chain.verify(TENANT))
        assert (result.first_break.seq, result.first_break.reason) == (2, "signature")

    def test_verification_from_a_later_sequence_and_under_a_limit(self, store):
        store.rows.extend(_row(i) for i in range(1, 11))
        _seal_all(store)
        result = asyncio.run(audit_chain.verify(TENANT, from_seq=4, limit=3))
        assert result.status == "verified" and (result.checked_from, result.checked_to, result.verified) == (4, 6, 3)
        store.rows.remove(store.sealed()[2])
        missing = asyncio.run(audit_chain.verify(TENANT, from_seq=4))
        assert (missing.first_break.seq, missing.first_break.reason) == (3, "sequence_gap")

    def test_status_reports_the_head_and_the_backlog(self, store, monkeypatch):
        store.rows.extend(_row(i) for i in range(1, 4))
        _seal_all(store)
        store.rows.append(_row(4))
        monkeypatch.setattr(audit_chain.settings, "audit_chain_enabled", True)
        current = asyncio.run(audit_chain.status(TENANT))
        assert current["enabled"] is True and current["head"]["seq"] == 3 and current["unsealed"] == 1
        assert current["head"]["hash"] == store.sealed()[-1].chain_hash


class TestTasks:
    def test_sealing_is_off_by_default(self):
        from core.tasks import audit_chain_tasks

        assert settings.audit_chain_enabled is False
        with patch.object(audit_chain_tasks, "_tenant_ids", AsyncMock(side_effect=AssertionError("never read"))):
            assert asyncio.run(audit_chain_tasks._seal_audit_chains_async()) == {
                "enabled": False,
                "tenants": 0,
                "sealed": 0,
                "errors": 0,
            }

    def test_sealing_drains_each_tenant_and_isolates_a_failing_one(self, monkeypatch):
        from core.tasks import audit_chain_tasks

        monkeypatch.setattr(settings, "audit_chain_enabled", True)
        t1, t2 = uuid.uuid4(), uuid.uuid4()
        calls: list[uuid.UUID] = []

        async def _seal(tenant_id, **_k):
            calls.append(tenant_id)
            if tenant_id == t2:
                raise RuntimeError("tenant database unavailable")
            more = calls.count(tenant_id) < 3
            return audit_chain.SealOutcome(
                tenant_id=tenant_id, sealed=5 if more else 1, head=audit_chain.Head(), more=more
            )

        with (
            patch.object(audit_chain_tasks, "_tenant_ids", AsyncMock(return_value=[t1, t2])),
            patch.object(audit_chain, "seal", _seal),
        ):
            result = asyncio.run(audit_chain_tasks._seal_audit_chains_async(max_rounds=10))
        assert result == {"enabled": True, "tenants": 2, "sealed": 11, "errors": 1}
        assert calls.count(t1) == 3 and calls.count(t2) == 1

    def test_verification_reports_every_broken_tenant(self):
        from core.tasks import audit_chain_tasks

        t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        async def _verify(tenant_id, **_k):
            if tenant_id == t3:
                raise RuntimeError("tenant database unavailable")
            result = audit_chain.Verification(
                tenant_id=tenant_id, head=audit_chain.Head(seq=4, hash="h"), unsealed=0, checked_from=1
            )
            result.verified = 4
            if tenant_id == t2:
                result.first_break = audit_chain.Break(seq=3, row_id="r3", reason="link_hash")
            return result

        with (
            patch.object(audit_chain_tasks, "_tenant_ids", AsyncMock(return_value=[t1, t2, t3])),
            patch.object(audit_chain, "verify", _verify),
        ):
            result = asyncio.run(audit_chain_tasks._verify_audit_chains_async())
        assert result["tenants"] == 3 and result["verified"] == 8 and result["errors"] == 1
        assert result["broken"] == [{"tenant_id": str(t2), "seq": 3, "row_id": "r3", "reason": "link_hash"}]

    def test_the_tasks_are_scheduled(self):
        from core.tasks.celery_app import app

        assert "core.tasks.audit_chain_tasks" in app.conf.include
        schedule = app.conf.beat_schedule
        assert schedule["seal-audit-chains"]["task"] == "core.tasks.audit_chain_tasks.seal_audit_chains"
        assert schedule["verify-audit-chains"]["task"] == "core.tasks.audit_chain_tasks.verify_audit_chains"


class TestEvidence:
    def test_the_section_carries_the_head_the_backlog_and_a_recent_verification(self, store):
        store.rows.extend(_row(i) for i in range(1, 6))
        _seal_all(store)
        store.rows.append(_row(6))
        section = asyncio.run(audit_chain.evidence(TENANT, recent=3))
        assert section["enabled"] is False and section["head"]["seq"] == 5 and section["unsealed"] == 1
        checked = section["recent_verification"]
        assert checked["status"] == "verified" and (checked["checked_from"], checked["checked_to"]) == (3, 5)

    def test_an_empty_chain_has_no_verification(self, store):
        section = asyncio.run(audit_chain.evidence(TENANT))
        assert section["head"]["seq"] == 0 and section["recent_verification"] is None

    def test_unreadable_parts_are_reported_not_raised(self, monkeypatch):
        monkeypatch.setattr(audit_chain, "status", AsyncMock(side_effect=RuntimeError("db")))
        section = asyncio.run(audit_chain.evidence(TENANT))
        assert section["head"] is None and section["head_error"] == "RuntimeError"
        monkeypatch.setattr(
            audit_chain,
            "status",
            AsyncMock(
                return_value={"enabled": False, "head": {"seq": 9, "hash": "h", "sealed_at": None}, "unsealed": 0}
            ),
        )
        monkeypatch.setattr(audit_chain, "verify", AsyncMock(side_effect=RuntimeError("db")))
        section = asyncio.run(audit_chain.evidence(TENANT))
        assert section["head"]["seq"] == 9 and section["recent_verification"] is None
        assert section["verification_error"] == "RuntimeError"
