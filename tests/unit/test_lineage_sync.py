# SPDX-License-Identifier: Apache-2.0
"""Incremental synchronisation: items and sources, the feed, runs that skip the unchanged, the sweep, the routes."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, False_, Grouping, Null, True_

from api.v1 import lineage as api
from core.config import settings
from core.lineage import provenance, sync
from core.lineage.sync import SyncError
from core.models.lineage import LineageNode

TENANT = uuid.uuid4()
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
FEED = "https://feed.example.com/changes"


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


def _matches(clause, row) -> bool:
    if clause is None:
        return True
    if isinstance(clause, BooleanClauseList):
        parts = [_matches(part, row) for part in clause.clauses]
        return any(parts) if clause.operator is operators.or_ else all(parts)
    if isinstance(clause, Grouping):
        return _matches(clause.element, row)
    if isinstance(clause, BinaryExpression):
        attr = getattr(row, clause.left.name)
        right = clause.right
        if clause.operator is operators.is_:
            if isinstance(right, Null):
                return attr is None
            if isinstance(right, True_):
                return attr is True
            if isinstance(right, False_):
                return attr is False
        if clause.operator is operators.eq:
            return attr == right.value
        if clause.operator is operators.in_op:
            return attr in list(right.value)
        if clause.operator is operators.lt:
            return attr is not None and attr < right.value
        if clause.operator is operators.le:
            return attr is not None and attr <= right.value
    raise NotImplementedError(str(clause))


class _Session:
    def __init__(self):
        self.rows = []

    async def execute(self, statement):
        table = statement.get_final_froms()[0].name
        return _Result([r for r in self.rows if r.__tablename__ == table and _matches(statement.whereclause, r)])

    def add(self, row):
        row.id = row.id or uuid.uuid4()
        self.rows.append(row)

    async def delete(self, row):
        self.rows.remove(row)

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def session(monkeypatch):
    import core.database
    import core.security.egress

    store = _Session()
    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: store)
    monkeypatch.setattr(settings, "lineage_enabled", True)
    monkeypatch.setattr(sync, "encrypt_for_tenant", AsyncMock(side_effect=lambda text, tid: f"enc:{text}"))
    monkeypatch.setattr(sync, "decrypt_for_tenant", lambda text: text[4:])
    monkeypatch.setattr(core.security.egress, "egress_dns_validation_required", lambda: False)
    return store


def _feed_response(items, cursor="c2"):
    return {"items": items, "cursor": cursor}


DOC = {
    "ref": "https://docs.example.com/a",
    "kind": "document",
    "title": "A",
    "text": "The quick brown fox. " * 20,
    "version": "v1",
}
REC = {
    "ref": "r-1",
    "kind": "record",
    "record": {"account": "A1", "direction": "credit", "amount": 10, "booked_at": "2026-10-01T09:00:00Z"},
}


class TestChecks:
    def test_an_item_is_checked_and_versioned(self):
        doc = sync.check_item({"ref": " https://x/a ", "text": "hello"})
        assert doc["kind"] == "document" and doc["stream"] == b"hello" and doc["mime_type"] == "text/plain"
        assert doc["version"] == provenance.version_of(b"hello") and doc["title"].endswith("x/a")
        raw = sync.check_item(
            {"ref": "b", "content_base64": base64.b64encode(b"%PDF").decode(), "mime_type": "application/pdf"}
        )
        assert raw["stream"] == b"%PDF"
        rec = sync.check_item({"ref": "r-9", "kind": "record", "record": {"account": "A1"}})
        assert rec["record"]["record_ref"] == "r-9" and rec["version"].startswith("sha256:")
        assert sync.check_item({"ref": "r", "record": {}}, "record")["kind"] == "record"
        for bad in (
            {"text": "x"},
            {"ref": "a", "kind": "thing"},
            {"ref": "a"},
            {"ref": "a", "content_base64": "!!"},
            {"ref": "a", "kind": "record"},
            "x",
        ):
            with pytest.raises(SyncError):
                sync.check_item(bad)

    def test_an_item_too_large_is_refused(self, monkeypatch):
        monkeypatch.setattr(sync, "MAX_ITEM_BYTES", 4)
        with pytest.raises(SyncError) as refused:
            sync.check_item({"ref": "a", "text": "hello"})
        assert refused.value.code == "item_too_large"

    def test_a_source_is_checked_and_bounded(self, monkeypatch):
        import core.security.egress

        monkeypatch.setattr(core.security.egress, "egress_dns_validation_required", lambda: False)
        out = sync.check_source(
            {"name": " Core ", "url": FEED, "interval_minutes": "15", "config": {"basis": "contract"}}
        )
        assert out == {
            "name": "Core",
            "kind": "feed",
            "url": FEED,
            "item_kind": "document",
            "interval_minutes": 15,
            "config": {"basis": "contract"},
        }
        assert sync.check_source({"enabled": False, "interval_minutes": 5}, partial=True) == {
            "enabled": False,
            "interval_minutes": 5,
        }
        for bad, code in (
            ({"url": FEED}, "source_invalid"),
            ({"name": "x", "url": "http://feed.example.com/x"}, "url_refused"),
            ({"name": "x", "url": "https://user:pw@feed.example.com/x"}, "url_refused"),
            ({"name": "x", "url": FEED, "interval_minutes": 1}, "source_invalid"),
            ({"name": "x", "url": FEED, "kind": "carrier-pigeon"}, "source_invalid"),
            ({"name": "x", "url": FEED, "item_kind": "finding"}, "source_invalid"),
            ({"name": "x", "url": FEED, "config": {"blob": "x" * sync.MAX_CONFIG}}, "config_too_large"),
        ):
            with pytest.raises(SyncError) as refused:
                sync.check_source(bad)
            assert refused.value.code == code


class TestFeed:
    @pytest.mark.asyncio
    async def test_the_feed_is_asked_since_the_cursor_with_the_token(self, monkeypatch, session):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_feed_response([DOC], "c2"))

        monkeypatch.setattr(sync, "_transport", lambda: httpx.MockTransport(handler))
        out = await sync.fetch_feed({"url": FEED}, "c1", token="t0k")
        assert out == {"items": [DOC], "cursor": "c2", "more": False}
        assert seen["url"].startswith(f"{FEED}?") and "since=c1" in seen["url"] and seen["auth"] == "Bearer t0k"
        assert f"limit={sync.MAX_ITEMS}" in seen["url"]  # the feed is asked for at most one run of items
        out = await sync.fetch_feed({"url": FEED}, None)
        assert "since" not in seen["url"] and seen["auth"] is None

    @pytest.mark.asyncio
    async def test_a_feed_that_misbehaves_is_a_failed_fetch(self, monkeypatch, session):
        answers = {"status": 500, "body": b"{}"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                answers["status"], content=answers["body"], headers={"content-type": "application/json"}
            )

        monkeypatch.setattr(sync, "_transport", lambda: httpx.MockTransport(handler))
        for status, body, code in (
            (500, b"{}", "feed_unavailable"),
            (200, b"not json", "feed_invalid"),
            (200, b'{"items": "x"}', "feed_invalid"),
        ):
            answers.update(status=status, body=body)
            with pytest.raises(SyncError) as refused:
                await sync.fetch_feed({"url": FEED}, None)
            assert refused.value.code == code
        with pytest.raises(SyncError) as refused:
            await sync.fetch_feed({"url": "http://feed.example.com/x"}, None)
        assert refused.value.code == "url_refused"
        monkeypatch.setattr(
            sync, "_transport", lambda: httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
        )
        with pytest.raises(SyncError) as refused:
            await sync.fetch_feed({"url": FEED}, None)
        assert refused.value.code == "feed_unreachable"
        monkeypatch.setattr(sync, "MAX_ITEMS", 1)
        monkeypatch.setattr(
            sync,
            "_transport",
            lambda: httpx.MockTransport(lambda r: httpx.Response(200, json=_feed_response([DOC, REC]))),
        )
        out = await sync.fetch_feed({"url": FEED}, None)
        assert len(out["items"]) == 1 and out["more"] is True


class TestSources:
    @pytest.mark.asyncio
    async def test_a_source_is_kept_with_its_token_encrypted_and_never_returned(self, session):
        made = await sync.create_source(TENANT, {"name": "core", "url": FEED, "token": "secret"}, user_id="u1")
        assert made["has_token"] is True and "token" not in made and made["enabled"] is True and made["next_run_at"]
        assert session.rows[0].token == "enc:secret" and session.rows[0].created_by == "u1"
        with pytest.raises(SyncError) as refused:
            await sync.create_source(TENANT, {"name": "core", "url": FEED})
        assert refused.value.code == "source_exists"
        await sync.create_source(TENANT, {"name": "archive", "url": FEED, "enabled": False})
        listed = await sync.list_sources(TENANT)
        assert [s["name"] for s in listed] == ["archive", "core"] and listed[0]["enabled"] is False

    @pytest.mark.asyncio
    async def test_a_source_is_changed_or_removed(self, session):
        made = await sync.create_source(TENANT, {"name": "core", "url": FEED, "token": "secret"})
        source_id = uuid.UUID(made["id"])
        session.rows[0].cursor = "c9"
        session.rows[0].next_run_at = T0 + timedelta(days=30)
        changed = await sync.update_source(TENANT, source_id, {"interval_minutes": 10, "name": "renamed", "token": ""})
        assert changed["interval_minutes"] == 10 and changed["name"] == "core" and changed["has_token"] is False
        assert changed["next_run_at"] != (T0 + timedelta(days=30)).isoformat() and changed["cursor"] == "c9"
        changed = await sync.update_source(TENANT, source_id, {"reset_cursor": True, "config": {"basis": "consent"}})
        assert changed["cursor"] is None and changed["config"] == {"basis": "consent"}
        with pytest.raises(SyncError):
            await sync.update_source(TENANT, uuid.uuid4(), {"enabled": True})
        with pytest.raises(SyncError):
            await sync.update_source(TENANT, source_id, {"url": "http://feed.example.com/x"})
        await sync.delete_source(TENANT, source_id)
        assert session.rows == []
        with pytest.raises(SyncError) as refused:
            await sync.list_runs(TENANT, source_id)
        assert refused.value.code == "source_unknown"


class TestRuns:
    @pytest.mark.asyncio
    async def test_a_run_skips_the_unchanged_ingests_the_rest_and_moves_the_cursor_only_when_clean(
        self, monkeypatch, session
    ):
        import core.rag.ingest as rag_ingest
        from core.txn import records as txn_records

        made = await sync.create_source(
            TENANT, {"name": "core", "url": FEED, "token": "secret", "config": {"basis": "contract"}}
        )
        source_id = uuid.UUID(made["id"])
        session.rows[0].cursor = "c1"
        # the document with version v1 is already kept: unchanged
        session.add(
            LineageNode(
                tenant_id=TENANT,
                kind="document",
                ref=DOC["ref"],
                source=DOC["ref"],
                version="v1",
                observed_at=T0,
                attributes={},
            )
        )
        changed = {**DOC, "ref": "https://docs.example.com/b", "version": "v2", "modified_at": "2026-10-02T00:00:00Z"}
        broken = {**DOC, "ref": "https://docs.example.com/c", "version": "v3"}
        fetched = AsyncMock(
            return_value={"items": [DOC, changed, broken, REC, {"kind": "document"}], "cursor": "c2", "more": False}
        )
        monkeypatch.setattr(sync, "fetch_feed", fetched)

        async def ingest(**kwargs):
            if kwargs["source"].endswith("/c"):
                return SimpleNamespace(errors=["extraction produced zero spans"], chunks_indexed=0)
            return SimpleNamespace(errors=[], chunks_indexed=1)

        monkeypatch.setattr(rag_ingest, "ingest_document", ingest)
        chained = AsyncMock(return_value={"nodes": [], "steps": 1})
        monkeypatch.setattr(provenance, "record_chain", chained)
        monkeypatch.setattr(settings, "transaction_intelligence_enabled", True)
        ingested_records = AsyncMock(return_value={"kept": 1})
        monkeypatch.setattr(txn_records, "ingest", ingested_records)

        run = await sync.run_source(TENANT, source_id)
        assert fetched.call_args.args[1] == "c1" and fetched.call_args.kwargs["token"] == "secret"
        assert (run["received"], run["processed"], run["skipped"], run["failed"]) == (5, 2, 1, 2)
        assert run["status"] == "partial" and run["cursor_before"] == "c1" and run["cursor_after"] == "c1"
        assert any("ingest_failed" in e for e in run["errors"]) and any("item_invalid" in e for e in run["errors"])
        # the changed document was linked to the feed it was acquired from, with the lawful basis
        doc_call = next(c for c in chained.call_args_list if c.args[1][1]["kind"] == "document")
        nodes, steps = doc_call.args[1], doc_call.args[2]
        assert nodes[1]["version"] == "v2"  # the version the skip check reads
        rec_call = next(c for c in chained.call_args_list if c.args[1][1]["kind"] == "record")
        assert rec_call.args[1][1]["ref"] == "r-1" and rec_call.args[1][1]["version"].startswith("sha256:")
        assert (
            nodes[0]["ref"] == FEED
            and nodes[0]["attributes"]["basis"] == "contract"
            and nodes[1]["ref"] == changed["ref"]
        )
        assert steps == [
            {"from": 0, "to": 1, "step": "acquire", "tool": "core.lineage.sync", "details": {"run": run["id"]}}
        ]
        assert (
            ingested_records.call_args.kwargs["source"] == "core"
            and ingested_records.call_args.args[1][0]["record_ref"] == "r-1"
        )
        source = (await sync.list_sources(TENANT))[0]
        assert source["cursor"] == "c1" and source["last_status"] == "partial" and source["last_run_at"]
        # a clean run moves the cursor
        fetched.return_value = {"items": [changed], "cursor": "c3", "more": False}
        session.add(
            LineageNode(
                tenant_id=TENANT,
                kind="document",
                ref=changed["ref"],
                source="",
                version="v2",
                observed_at=T0,
                attributes={},
            )
        )
        run = await sync.run_source(TENANT, source_id)
        assert run["status"] == "completed" and run["skipped"] == 1 and run["cursor_after"] == "c3"
        runs = await sync.list_runs(TENANT, source_id)
        assert [r["status"] for r in runs] == ["completed", "partial"]
        # a feed that cannot be read is a failed run that keeps the cursor
        fetched.side_effect = SyncError(502, "feed_unavailable", "the feed answered 503")
        run = await sync.run_source(TENANT, source_id)
        assert (
            run["status"] == "failed"
            and run["cursor_after"] == "c3"
            and run["errors"] == ["feed_unavailable: the feed answered 503"]
        )
        fetched.side_effect = RuntimeError("boom")
        run = await sync.run_source(TENANT, source_id)
        assert run["status"] == "failed" and run["errors"] == ["RuntimeError"]

    @pytest.mark.asyncio
    async def test_a_run_holds_its_source_until_it_finishes(self, monkeypatch, session):
        made = await sync.create_source(TENANT, {"name": "core", "url": FEED})
        source_id = uuid.UUID(made["id"])
        row = session.rows[0]
        # a run in progress: a manual run is refused, the source is not claimed
        row.lease_owner, row.lease_until = "other", datetime.now(UTC) + timedelta(minutes=5)
        with pytest.raises(SyncError) as refused:
            await sync.run_source(TENANT, source_id)
        assert refused.value.status == 409 and refused.value.code == "run_in_progress"
        assert await sync.claim_due(TENANT) == []
        assert (await sync.list_sources(TENANT))[0]["running"] is True
        # a scheduled run whose lease is gone is skipped
        skipped = await sync.run_source(TENANT, source_id, trigger="schedule", lease="mine")
        assert skipped["status"] == "skipped" and skipped["reason"] == "lease_lost"
        # once the lease runs out the source is claimed, the run carries the lease and frees it at the end
        row.lease_until = datetime.now(UTC) - timedelta(minutes=1)
        monkeypatch.setattr(sync, "fetch_feed", AsyncMock(return_value={"items": [], "cursor": "c1", "more": False}))
        (claimed_id, lease) = (await sync.claim_due(TENANT))[0]
        assert row.lease_owner == lease
        run = await sync.run_source(TENANT, claimed_id, trigger="schedule", lease=lease)
        assert run["status"] == "completed" and row.lease_owner == "" and row.lease_until is None
        manual = await sync.run_source(TENANT, source_id)
        assert manual["status"] == "completed" and row.lease_owner == ""

    @pytest.mark.asyncio
    async def test_a_feed_that_answers_more_than_asked_keeps_the_cursor(self, monkeypatch, session):
        made = await sync.create_source(TENANT, {"name": "core", "url": FEED})
        session.rows[0].cursor = "c1"
        monkeypatch.setattr(sync, "fetch_feed", AsyncMock(return_value={"items": [], "cursor": "c9", "more": True}))
        run = await sync.run_source(TENANT, uuid.UUID(made["id"]))
        assert run["status"] == "partial" and run["cursor_after"] == "c1"
        assert run["errors"][0].startswith("feed_truncated")
        assert (await sync.list_sources(TENANT))[0]["cursor"] == "c1"

    @pytest.mark.asyncio
    async def test_records_need_transaction_intelligence_and_a_registered_fetcher_is_used(self, monkeypatch, session):
        monkeypatch.setattr(settings, "transaction_intelligence_enabled", False)
        made = await sync.create_source(TENANT, {"name": "bank", "url": FEED, "item_kind": "record"})
        monkeypatch.setattr(sync, "fetch_feed", AsyncMock(return_value={"items": [REC], "cursor": None}))
        run = await sync.run_source(TENANT, uuid.UUID(made["id"]))
        assert run["status"] == "failed" and run["errors"][0].endswith("txn_disabled: transaction intelligence is off")
        listed = AsyncMock(return_value={"items": [], "cursor": "k1"})
        sync.register_fetcher("connector", listed)
        try:
            made = await sync.create_source(TENANT, {"name": "crm", "url": FEED, "kind": "connector"})
            run = await sync.run_source(TENANT, uuid.UUID(made["id"]))
            assert (
                run["status"] == "completed"
                and run["cursor_after"] == "k1"
                and listed.call_args.args[0]["name"] == "crm"
            )
        finally:
            sync.FETCHERS.pop("connector", None)

    @pytest.mark.asyncio
    async def test_due_sources_are_claimed_once_and_run_in_turn(self, monkeypatch, session):
        due = await sync.create_source(TENANT, {"name": "due", "url": FEED})
        later = await sync.create_source(TENANT, {"name": "later", "url": FEED})
        off = await sync.create_source(TENANT, {"name": "off", "url": FEED, "enabled": False})
        for row in session.rows:
            if str(row.id) == later["id"]:
                row.next_run_at = datetime.now(UTC) + timedelta(hours=1)
            if str(row.id) == off["id"]:
                row.next_run_at = None
        claimed = await sync.claim_due(TENANT)
        assert [str(c[0]) for c in claimed] == [due["id"]] and len(claimed[0][1]) == 32
        assert await sync.claim_due(TENANT) == []  # moved to its next slot, and leased
        ran = AsyncMock(return_value={"status": "completed"})
        monkeypatch.setattr(sync, "run_source", ran)
        for row in session.rows:
            if str(row.id) == due["id"]:
                row.next_run_at = None
                row.lease_until = None  # the earlier lease ran out
        assert await sync.run_due(TENANT) == [{"status": "completed"}]
        assert ran.call_args.args[1] == uuid.UUID(due["id"]) and ran.call_args.kwargs["trigger"] == "schedule"
        assert len(ran.call_args.kwargs["lease"]) == 32

    @pytest.mark.asyncio
    async def test_the_sweep_runs_every_tenant_and_is_off_by_default(self, monkeypatch, session):
        import core.database

        assert await sync.sweep() == {"skipped": "lineage_sync_sweep_disabled"}
        monkeypatch.setattr(settings, "lineage_sync_sweep_enabled", True)
        other = uuid.uuid4()

        class _Admin:
            async def execute(self, statement):
                return None

            async def scalars(self, statement):
                return _Result([TENANT, other])

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(core.database, "async_session_factory", lambda: _Admin())

        async def run_due(tenant_id, *, limit):
            if tenant_id == other:
                raise RuntimeError("tenant down")
            return [{"status": "completed"}, {"status": "failed"}]

        monkeypatch.setattr(sync, "run_due", run_due)
        assert await sync.sweep() == {"tenants": 1, "runs": 2, "failed": 2}
        from core.tasks import lineage_tasks

        monkeypatch.setattr(settings, "lineage_sync_sweep_enabled", False)
        assert lineage_tasks.sweep_sync_sources() == {"skipped": "lineage_sync_sweep_disabled"}


class TestRoutes:
    @pytest.mark.asyncio
    async def test_off_the_sync_routes_are_not_found(self, monkeypatch):
        monkeypatch.setattr(settings, "lineage_enabled", False)
        tid = str(TENANT)
        for call in (
            api.list_sync_sources(tenant_id=tid),
            api.create_sync_source(api.SourceIn(name="x", url=FEED), tenant_id=tid, user={}),
            api.run_sync_source(uuid.uuid4(), tenant_id=tid),
            api.list_sync_runs(uuid.uuid4(), limit=5, tenant_id=tid),
        ):
            with pytest.raises(HTTPException) as refused:
                await call
            assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_on_sources_are_managed_and_run_through_the_routes(self, monkeypatch, session):
        tid = str(TENANT)
        made = await api.create_sync_source(api.SourceIn(name="core", url=FEED, token="t"), tenant_id=tid, user={})
        assert made["has_token"] is True and "token" not in made
        with pytest.raises(HTTPException) as refused:
            await api.create_sync_source(api.SourceIn(name="core", url=FEED), tenant_id=tid, user={})
        assert refused.value.status_code == 409 and refused.value.detail["error"] == "source_exists"
        listed = await api.list_sync_sources(tenant_id=tid)
        assert listed["total"] == 1 and "feed" in listed["kinds"]
        source_id = uuid.UUID(made["id"])
        changed = await api.update_sync_source(
            source_id, api.SourcePatch(enabled=False, reset_cursor=True), tenant_id=tid
        )
        assert changed["enabled"] is False
        monkeypatch.setattr(sync, "run_source", AsyncMock(return_value={"id": "r1", "status": "completed"}))
        assert (await api.run_sync_source(source_id, tenant_id=tid))["status"] == "completed"
        assert (await api.list_sync_runs(source_id, limit=5, tenant_id=tid))["total"] == 0
        gone = await api.delete_sync_source(source_id, tenant_id=tid)
        assert gone.status_code == 204
        with pytest.raises(HTTPException) as refused:
            await api.delete_sync_source(source_id, tenant_id=tid)
        assert refused.value.status_code == 404

    def test_the_routes_and_the_beat_entry_are_registered(self):
        from api.main import app
        from core.tasks.celery_app import app as celery_app

        paths = {route.path for route in app.routes}
        assert {
            "/api/v1/lineage/sync/sources",
            "/api/v1/lineage/sync/sources/{source_id}",
            "/api/v1/lineage/sync/sources/{source_id}/run",
            "/api/v1/lineage/sync/sources/{source_id}/runs",
        } <= paths
        entry = celery_app.conf.beat_schedule["sweep-lineage-sync-sources"]
        celery_app.loader.import_default_modules()  # what a worker does at start
        assert "core.tasks.lineage_tasks.sweep_sync_sources" in celery_app.tasks
        assert "core.tasks.lineage_tasks" in celery_app.conf.include
        assert entry["task"] == "core.tasks.lineage_tasks.sweep_sync_sources" and entry["schedule"] == 300.0


def test_the_migration_and_the_models_are_shaped():
    from core.models.lineage_sync import LineageSyncRun, LineageSyncSource

    text = Path("migrations/versions/v6_z76_lineage_sync.py").read_text(encoding="utf-8")
    assert 'revision = "v6z76_lineage_sync"' in text and 'down_revision = "v6z75_lineage"' in text
    for table in ("lineage_sync_sources", "lineage_sync_runs"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in text
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in text
        assert f"CREATE POLICY {table}_tenant_isolation" in text
    assert "source_id UUID NOT NULL REFERENCES lineage_sync_sources(id) ON DELETE CASCADE" in text
    assert "ix_lineage_sync_runs_source ON lineage_sync_runs(source_id)" in text
    assert "ux_lineage_sync_sources_tenant_name" in text
    assert "lease_owner VARCHAR(64) NOT NULL DEFAULT ''" in text and "lease_until TIMESTAMPTZ NULL" in text
    assert (
        LineageSyncSource.__tablename__ == "lineage_sync_sources"
        and LineageSyncRun.__tablename__ == "lineage_sync_runs"
    )
    assert {fk.column.table.name for fk in LineageSyncRun.__table__.foreign_keys} == {"lineage_sync_sources"}
    assert "ix_lineage_sync_runs_source" in {index.name for index in LineageSyncRun.__table__.indexes}
    assert json.dumps(sync.ITEM_KINDS) == '["document", "record"]'
