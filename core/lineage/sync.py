# SPDX-License-Identifier: Apache-2.0
"""Incremental synchronisation: a source polled on a schedule, only what changed since the last run processed.

A sync source names a feed the tenant administrator set up: an HTTPS
endpoint that answers a JSON list of items changed since a cursor, with
the cursor to ask from next time. Each item is a document (a title and
its text or bytes) or a transaction record, under a stable reference and
a version. A run fetches the items, skips every item whose version is
already kept in provenance, ingests the rest (documents through the
knowledge ingestion, records through the transaction store, both of
which note their lineage), links each document to the feed it was
acquired from, and records what it received, processed, skipped and
failed. The cursor advances only when nothing failed, so a failed item
is offered again. Sources are claimed for a run one at a time under a
row lock, so two sweepers never run the same source. Behind
``lineage_enabled``; the periodic sweep (Celery beat) also needs
``lineage_sync_sweep_enabled``. Tenant scoped under row-level security.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select

from core.config import settings
from core.crypto.tenant_secrets import decrypt_for_tenant, encrypt_for_tenant
from core.lineage import provenance
from core.security.egress import EgressValidationError, build_pinned_async_transport, validate_public_url

logger = structlog.get_logger()

SOURCE_KINDS = ("feed",)
ITEM_KINDS = ("document", "record")
MIN_INTERVAL = 5
MAX_INTERVAL = 10080  # a week, in minutes
MAX_ITEMS = 500  # items one run processes
MAX_ITEM_BYTES = 5_000_000
MAX_FEED_BYTES = 50_000_000
MAX_ERRORS = 20
MAX_RUNS = 100
FETCH_TIMEOUT_S = 30.0
MAX_CONFIG = 4000
LEASE_MINUTES = 60  # how long a claimed run holds its source if it never finishes


class SyncError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return provenance.enabled()


def sweep_enabled() -> bool:
    return enabled() and bool(getattr(settings, "lineage_sync_sweep_enabled", False))


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- the feed


def _transport() -> Any:
    from core.security.egress import egress_dns_validation_required

    return build_pinned_async_transport(require_dns=egress_dns_validation_required())


async def fetch_feed(source: dict[str, Any], cursor: str | None, *, token: str | None = None) -> dict[str, Any]:
    """The items the feed holds since the cursor, and the cursor to ask from next time."""
    try:
        checked = validate_public_url(source["url"])
    except EgressValidationError as exc:
        raise SyncError(422, "url_refused", f"the feed URL is not allowed: {exc.reason}") from None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: dict[str, Any] = {"limit": MAX_ITEMS}
    if cursor:
        params["since"] = cursor
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, transport=_transport(), follow_redirects=False) as client:
            response = await client.get(checked.url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise SyncError(502, "feed_unreachable", f"the feed could not be read: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise SyncError(502, "feed_unavailable", f"the feed answered {response.status_code}")
    if len(response.content) > MAX_FEED_BYTES:
        raise SyncError(502, "feed_too_large", f"the feed answered more than {MAX_FEED_BYTES} bytes")
    try:
        body = response.json()
    except ValueError:
        raise SyncError(502, "feed_invalid", "the feed did not answer JSON") from None
    items = body.get("items") if isinstance(body, dict) else body
    if not isinstance(items, list):
        raise SyncError(502, "feed_invalid", "the feed answers an object with an items list")
    more = len(items) > MAX_ITEMS
    next_cursor = body.get("cursor") if isinstance(body, dict) else None
    return {"items": items[:MAX_ITEMS], "cursor": _text(next_cursor, 500) or None, "more": more}


Fetcher = Callable[[dict[str, Any], str | None], Awaitable[dict[str, Any]]]

# enterprise-gate: process-local-ok reason=fetcher-registry-holds-code-not-state
FETCHERS: dict[str, Fetcher] = {}


def register_fetcher(kind: str, fetcher: Fetcher) -> None:
    """Another way of listing changed items (a connector) under its own source kind."""
    FETCHERS[kind] = fetcher


def fetcher_for(kind: str) -> Fetcher | None:
    return FETCHERS.get(kind)


# ---------------------------------------------------------------- items


def check_item(raw: Any, default_kind: str = "document") -> dict[str, Any]:
    """One item as a run processes it, or why it cannot be."""
    if not isinstance(raw, dict):
        raise SyncError(422, "item_invalid", "each item is an object")
    ref = _text(raw.get("ref"), provenance.MAX_REF)
    if not ref:
        raise SyncError(422, "item_invalid", "ref is required")
    kind = _text(raw.get("kind"), 16).lower() or default_kind
    if kind not in ITEM_KINDS:
        raise SyncError(422, "item_invalid", f"kind is one of {', '.join(ITEM_KINDS)}")
    modified_at = raw.get("modified_at")
    item: dict[str, Any] = {"ref": ref, "kind": kind, "modified_at": _text(modified_at, 40) or None}
    if kind == "document":
        if raw.get("content_base64") is not None:
            try:
                stream = base64.b64decode(str(raw["content_base64"]), validate=True)
            except (binascii.Error, ValueError):
                raise SyncError(422, "item_invalid", f"{ref}: content_base64 is not base64") from None
        else:
            stream = str(raw.get("text") or "").encode("utf-8")
        if not stream:
            raise SyncError(422, "item_invalid", f"{ref}: a document carries text or content_base64")
        if len(stream) > MAX_ITEM_BYTES:
            raise SyncError(422, "item_too_large", f"{ref}: a document is at most {MAX_ITEM_BYTES} bytes")
        item["stream"] = stream
        item["title"] = _text(raw.get("title"), 480) or ref[-120:]
        item["mime_type"] = _text(raw.get("mime_type"), 128) or "text/plain"
        item["version"] = _text(raw.get("version"), provenance.MAX_VERSION) or provenance.version_of(stream)
    else:
        record = raw.get("record")
        if not isinstance(record, dict):
            raise SyncError(422, "item_invalid", f"{ref}: a record item carries a record object")
        record = dict(record)
        record.setdefault("record_ref", ref[:128])
        item["record"] = record
        item["version"] = _text(raw.get("version"), provenance.MAX_VERSION) or provenance.version_of(
            json.dumps(record, sort_keys=True, default=str)
        )
    return item


async def known_versions(tenant_id: uuid.UUID, items: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    """The (kind, ref, version) keys provenance already keeps for these items: those are unchanged."""
    from core.database import get_tenant_session
    from core.models.lineage import LineageNode

    refs = sorted({item["ref"] for item in items})
    if not refs:
        return set()
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(LineageNode).where(
                        LineageNode.tenant_id == tenant_id,
                        LineageNode.kind.in_(sorted(ITEM_KINDS)),
                        LineageNode.ref.in_(refs),
                    )
                )
            )
            .scalars()
            .all()
        )
    return {(row.kind, row.ref, row.version or "") for row in rows}


async def process_item(tenant_id: uuid.UUID, source: dict[str, Any], item: dict[str, Any], run_id: str) -> None:
    """Ingest one changed item through the store that owns its kind, and link it to the feed it came from."""
    if item["kind"] == "document":
        from core.rag.ingest import ingest_document

        result = await ingest_document(
            tenant_id=tenant_id,
            title=item["title"],
            stream=item["stream"],
            mime_type=item["mime_type"],
            filename=item["ref"][-120:],
            source=item["ref"],
            metadata={"sync_source": source["name"]},
        )
        if result.errors:
            raise SyncError(422, "ingest_failed", "; ".join(result.errors)[:200])
        if provenance.enabled():
            attributes = {"sync_source": source["name"], "run": run_id}
            attributes.update({k: v for k, v in (source.get("config") or {}).items() if k in ("basis", "licence")})
            await provenance.record_chain(
                tenant_id,
                [
                    {"kind": "source", "ref": source["url"], "source": source["url"], "attributes": attributes},
                    {
                        "kind": "document",
                        "ref": item["ref"],
                        "source": item["ref"],
                        # The version the skip check reads (the feed's own, or the content hash).
                        "version": item["version"],
                        "observed_at": item.get("modified_at"),
                    },
                ],
                [{"from": 0, "to": 1, "step": "acquire", "tool": "core.lineage.sync", "details": {"run": run_id}}],
            )
    else:
        from core.txn import records
        from core.txn.records import TxnError

        if not records.enabled():
            raise SyncError(422, "txn_disabled", "transaction intelligence is off")
        try:
            await records.ingest(tenant_id, [item["record"]], source=source["name"][:64])
        except TxnError as exc:
            raise SyncError(exc.status, exc.code, exc.message) from None
        if provenance.enabled():
            # The record under the version the skip check reads, acquired from this feed.
            await provenance.record_chain(
                tenant_id,
                [
                    {"kind": "source", "ref": source["url"], "source": source["url"]},
                    {
                        "kind": "record",
                        "ref": item["ref"],
                        "source": source["name"][:64],
                        "version": item["version"],
                        "observed_at": item.get("modified_at"),
                    },
                ],
                [{"from": 0, "to": 1, "step": "acquire", "tool": "core.lineage.sync", "details": {"run": run_id}}],
            )


# ---------------------------------------------------------------- sources


def _source_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "kind": row.kind,
        "url": row.url,
        "item_kind": row.item_kind,
        "interval_minutes": row.interval_minutes,
        "enabled": bool(row.enabled),
        "has_token": bool(row.token),
        "config": dict(row.config or {}),
        "cursor": row.cursor or None,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "last_status": row.last_status or None,
        "running": bool(row.lease_owner) and row.lease_until is not None and row.lease_until > _now(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _run_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "source_id": str(row.source_id),
        "trigger": row.trigger,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "cursor_before": row.cursor_before or None,
        "cursor_after": row.cursor_after or None,
        "received": row.received,
        "processed": row.processed,
        "skipped": row.skipped,
        "failed": row.failed,
        "errors": list(row.errors or []),
    }


def check_source(raw: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """A source as the store keeps it (the token aside), or why it cannot be."""
    out: dict[str, Any] = {}
    if "name" in raw or not partial:
        name = _text(raw.get("name"), 100)
        if not name:
            raise SyncError(422, "source_invalid", "name is required")
        out["name"] = name
    if "kind" in raw or not partial:
        kind = _text(raw.get("kind"), 16).lower() or "feed"
        if kind not in SOURCE_KINDS and kind not in FETCHERS:
            raise SyncError(
                422, "source_invalid", f"kind is one of {', '.join(sorted(set(SOURCE_KINDS) | set(FETCHERS)))}"
            )
        out["kind"] = kind
    if "url" in raw or not partial:
        url = _text(raw.get("url"), 500)
        if not url:
            raise SyncError(422, "source_invalid", "url is required")
        try:
            validate_public_url(url, require_dns=False)
        except EgressValidationError as exc:
            raise SyncError(422, "url_refused", f"the feed URL is not allowed: {exc.reason}") from None
        out["url"] = url
    if "item_kind" in raw or not partial:
        item_kind = _text(raw.get("item_kind"), 16).lower() or "document"
        if item_kind not in ITEM_KINDS:
            raise SyncError(422, "source_invalid", f"item_kind is one of {', '.join(ITEM_KINDS)}")
        out["item_kind"] = item_kind
    if "interval_minutes" in raw or not partial:
        try:
            interval = int(raw.get("interval_minutes") or 60)
        except (TypeError, ValueError):
            raise SyncError(422, "source_invalid", "interval_minutes is a number") from None
        if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            raise SyncError(422, "source_invalid", f"interval_minutes is between {MIN_INTERVAL} and {MAX_INTERVAL}")
        out["interval_minutes"] = interval
    if "enabled" in raw:
        out["enabled"] = bool(raw.get("enabled"))
    if "config" in raw or not partial:
        config = raw.get("config") or {}
        if not isinstance(config, dict):
            raise SyncError(422, "source_invalid", "config is an object")
        if len(json.dumps(config, default=str)) > MAX_CONFIG:
            raise SyncError(422, "config_too_large", f"config is at most {MAX_CONFIG} characters of JSON")
        out["config"] = dict(config)
    return out


async def create_source(tenant_id: uuid.UUID, raw: dict[str, Any], *, user_id: str = "") -> dict[str, Any]:
    """Keep a new source; its name is unique for the tenant; a token is kept encrypted and never returned."""
    from core.database import get_tenant_session
    from core.models.lineage_sync import LineageSyncSource

    fields = check_source(raw)
    token = _text(raw.get("token"), 2000)
    async with get_tenant_session(tenant_id) as session:
        existing = (
            (
                await session.execute(
                    select(LineageSyncSource).where(
                        LineageSyncSource.tenant_id == tenant_id, LineageSyncSource.name == fields["name"]
                    )
                )
            )
            .scalars()
            .all()
        )
        if existing:
            raise SyncError(409, "source_exists", "a source of this name exists")
        row = LineageSyncSource(
            tenant_id=tenant_id,
            enabled=fields.get("enabled", True),
            token=(await encrypt_for_tenant(token, tenant_id)) if token else "",
            created_by=user_id[:128] or None,
            next_run_at=_now(),
            **{k: v for k, v in fields.items() if k != "enabled"},
        )
        session.add(row)
        await session.flush()
        out = _source_dict(row)
    logger.info("lineage_sync_source_created", kind=out["kind"])
    return out


async def list_sources(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.lineage_sync import LineageSyncSource

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (await session.execute(select(LineageSyncSource).where(LineageSyncSource.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
    return sorted((_source_dict(row) for row in rows), key=lambda s: s["name"])


async def _source_row(
    session: Any, tenant_id: uuid.UUID, source_id: uuid.UUID, *, lock: bool = False, wait: bool = False
) -> Any:
    from core.models.lineage_sync import LineageSyncSource

    statement = select(LineageSyncSource).where(
        LineageSyncSource.tenant_id == tenant_id, LineageSyncSource.id == source_id
    )
    if lock:
        statement = statement.with_for_update() if wait else statement.with_for_update(skip_locked=True)
    rows = (await session.execute(statement)).scalars().all()
    if not rows:
        raise SyncError(404, "source_unknown", "no such source")
    return rows[0]


async def update_source(tenant_id: uuid.UUID, source_id: uuid.UUID, raw: dict[str, Any]) -> dict[str, Any]:
    """Change a source: the URL, the interval, whether it is enabled, the token, the config; the cursor can be reset."""
    from core.database import get_tenant_session

    fields = check_source(raw, partial=True)
    token = raw.get("token")
    async with get_tenant_session(tenant_id) as session:
        row = await _source_row(session, tenant_id, source_id)
        for key, value in fields.items():
            if key == "name":
                continue  # the name is the source's identity
            setattr(row, key, value)
        if token is not None:
            row.token = (await encrypt_for_tenant(_text(token, 2000), tenant_id)) if _text(token, 2000) else ""
        if raw.get("reset_cursor"):
            row.cursor = ""
        if "interval_minutes" in fields or fields.get("enabled"):
            row.next_run_at = _now()
        row.updated_at = _now()
        await session.flush()
        out = _source_dict(row)
    return out


async def delete_source(tenant_id: uuid.UUID, source_id: uuid.UUID) -> None:
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _source_row(session, tenant_id, source_id)
        await session.delete(row)
        await session.flush()


async def list_runs(tenant_id: uuid.UUID, source_id: uuid.UUID, *, limit: int = 20) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.lineage_sync import LineageSyncRun

    async with get_tenant_session(tenant_id) as session:
        await _source_row(session, tenant_id, source_id)
        rows = (
            (
                await session.execute(
                    select(LineageSyncRun).where(
                        LineageSyncRun.tenant_id == tenant_id, LineageSyncRun.source_id == source_id
                    )
                )
            )
            .scalars()
            .all()
        )
    found = [_run_dict(row) for row in rows]
    found.sort(key=lambda r: r["started_at"] or "", reverse=True)
    return found[: max(1, min(limit, MAX_RUNS))]


# ---------------------------------------------------------------- runs


def _leased(row: Any, now: datetime) -> bool:
    return bool(row.lease_owner) and row.lease_until is not None and row.lease_until > now


async def claim_due(tenant_id: uuid.UUID, *, limit: int = 50) -> list[tuple[uuid.UUID, str]]:
    """The due sources of a tenant no run holds, each leased to this claim and moved to its next slot.

    The lease is held from the claim until the run finishes (or runs out), so another sweeper never
    claims a source while a run of it is still going.
    """
    from core.database import get_tenant_session
    from core.models.lineage_sync import LineageSyncSource

    now = _now()
    claimed: list[tuple[uuid.UUID, str]] = []
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(LineageSyncSource)
                    .where(
                        LineageSyncSource.tenant_id == tenant_id,
                        LineageSyncSource.enabled.is_(True),
                        (LineageSyncSource.next_run_at.is_(None)) | (LineageSyncSource.next_run_at <= now),
                        (LineageSyncSource.lease_until.is_(None)) | (LineageSyncSource.lease_until < now),
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            lease = uuid.uuid4().hex
            row.next_run_at = now + timedelta(minutes=int(row.interval_minutes or 60))
            row.lease_owner = lease
            row.lease_until = now + timedelta(minutes=LEASE_MINUTES)
            claimed.append((row.id, lease))
        await session.flush()
    return claimed


async def run_source(
    tenant_id: uuid.UUID, source_id: uuid.UUID, *, trigger: str = "manual", lease: str | None = None
) -> dict[str, Any]:
    """One run: fetch since the cursor, skip the unchanged, ingest the rest, record what happened.

    A scheduled run carries the lease its claim took and is skipped if that lease is gone; a manual run
    takes its own lease and is refused while another run holds the source.
    """
    from core.database import get_tenant_session
    from core.models.lineage_sync import LineageSyncRun

    if not enabled():
        raise SyncError(404, "lineage_disabled", "lineage is off")
    async with get_tenant_session(tenant_id) as session:
        row = await _source_row(session, tenant_id, source_id, lock=True, wait=True)
        now = _now()
        if lease is not None:
            if row.lease_owner != lease or not _leased(row, now):
                return {"source_id": str(source_id), "status": "skipped", "reason": "lease_lost"}
        else:
            if _leased(row, now):
                raise SyncError(409, "run_in_progress", "a run of this source is in progress")
            lease = uuid.uuid4().hex
            row.lease_owner = lease
        row.lease_until = now + timedelta(minutes=LEASE_MINUTES)
        source = _source_dict(row)
        token = decrypt_for_tenant(row.token) if row.token else None
        run = LineageSyncRun(
            tenant_id=tenant_id,
            source_id=row.id,
            trigger=trigger[:16],
            status="running",
            started_at=_now(),
            cursor_before=source["cursor"] or "",
            received=0,
            processed=0,
            skipped=0,
            failed=0,
            errors=[],
        )
        session.add(run)
        await session.flush()
        run_id = str(run.id)
    errors: list[str] = []
    received = processed = skipped = failed = 0
    cursor_after = source["cursor"]
    status = "completed"
    try:
        fetcher = fetcher_for(source["kind"])
        if fetcher is None:
            fetched = await fetch_feed(source, source["cursor"], token=token)
        else:
            fetched = await fetcher(source, source["cursor"])
        raw_items = list(fetched.get("items") or [])[:MAX_ITEMS]
        received = len(raw_items)
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            try:
                items.append(check_item(raw, source["item_kind"]))
            except SyncError as exc:
                failed += 1
                if len(errors) < MAX_ERRORS:
                    errors.append(f"{exc.code}: {exc.message}"[:300])
        known = await known_versions(tenant_id, items)
        latest = ""
        for item in items:
            if (item["kind"], item["ref"], item["version"]) in known:
                skipped += 1
                continue
            try:
                await process_item(tenant_id, source, item, run_id)
                processed += 1
                latest = max(latest, item.get("modified_at") or "")
            except SyncError as exc:
                failed += 1
                if len(errors) < MAX_ERRORS:
                    errors.append(f"{item['ref'][:120]}: {exc.code}: {exc.message}"[:300])
        if fetched.get("more"):
            # The feed answered more than it was asked for: the cursor stays, so nothing past the cut is lost.
            status = "partial"
            if len(errors) < MAX_ERRORS:
                errors.append(f"feed_truncated: the feed answered more than {MAX_ITEMS} items; the cursor was kept")
        elif failed == 0:
            # The cursor moves only when every item went through, so a failed item is offered again.
            cursor_after = fetched.get("cursor") or latest or source["cursor"]
            status = "completed"
        else:
            status = "partial" if processed or skipped else "failed"
    except SyncError as exc:
        status = "failed"
        errors.append(f"{exc.code}: {exc.message}"[:300])
    # enterprise-gate: broad-except-ok reason=sync-run-records-failure-logged-cursor-kept-primary-operation-isolated
    except Exception as exc:
        status = "failed"
        errors.append(f"{type(exc).__name__}"[:300])
        logger.warning("lineage_sync_run_failed", exc_info=True)
    async with get_tenant_session(tenant_id) as session:
        row = await _source_row(session, tenant_id, source_id)
        runs = (
            (
                await session.execute(
                    select(LineageSyncRun).where(
                        LineageSyncRun.tenant_id == tenant_id, LineageSyncRun.id == uuid.UUID(run_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        run = runs[0]
        run.status = status
        run.finished_at = _now()
        run.cursor_after = cursor_after or ""
        run.received, run.processed, run.skipped, run.failed = received, processed, skipped, failed
        run.errors = errors[:MAX_ERRORS]
        row.cursor = cursor_after or ""
        row.last_run_at = run.finished_at
        row.last_status = status
        if row.lease_owner == lease:
            row.lease_owner = ""
            row.lease_until = None
        if trigger == "manual" or row.next_run_at is None:
            row.next_run_at = run.finished_at + timedelta(minutes=int(row.interval_minutes or 60))
        await session.flush()
        out = _run_dict(run)
    logger.info(
        "lineage_sync_run",
        status=status,
        received=received,
        processed=processed,
        skipped=skipped,
        failed=failed,
        trigger=trigger,
    )
    return out


async def run_due(tenant_id: uuid.UUID, *, limit: int = 50) -> list[dict[str, Any]]:
    """Every due source of the tenant, run in turn."""
    out = []
    for source_id, lease in await claim_due(tenant_id, limit=limit):
        out.append(await run_source(tenant_id, source_id, trigger="schedule", lease=lease))
    return out


async def sweep(*, limit_per_tenant: int = 50) -> dict[str, Any]:
    """Every tenant's due sources; a no-op unless the sweep is on. Counts only."""
    if not sweep_enabled():
        return {"skipped": "lineage_sync_sweep_disabled"}
    from sqlalchemy import text

    from core.database import async_session_factory
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        # A maintenance role that cannot bypass tenant RLS must fail loudly, not enumerate nothing.
        await session.execute(text("SET LOCAL row_security = off"))
        tenant_ids = list((await session.scalars(select(Tenant.id))).all())
    runs = 0
    failed = 0
    tenants = 0
    for tenant_id in tenant_ids:
        try:
            outcomes = await run_due(tenant_id, limit=limit_per_tenant)
        # enterprise-gate: broad-except-ok reason=sync-sweep-isolates-per-tenant-failures-logged-continues-to-next
        except Exception:
            failed += 1
            logger.warning("lineage_sync_sweep_tenant_failed", exc_info=True)
            continue
        if outcomes:
            tenants += 1
            runs += len(outcomes)
            failed += sum(1 for o in outcomes if o.get("status") == "failed")
    logger.info("lineage_sync_sweep", tenants=tenants, runs=runs, failed=failed)
    return {"tenants": tenants, "runs": runs, "failed": failed}
