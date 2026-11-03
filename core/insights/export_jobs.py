# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights background exports: a job per request, built by the worker, kept for seven days.

``create`` checks what is asked for against the caller's views and explorers,
records it with the caller's role, domains and admin flag as the server read
them, and queues the build. ``build`` (worker) makes the file with exactly that
scope, masks PII by the tenant's setting unless raw values were allowed,
stores it in the export bucket, records its size, row count and SHA-256, and
tells the requester (in-app on the Downloads page, an email with a link and a
web push where subscribed; never the file itself). ``expire`` deletes files
past ``expires_at``. Downloads go through the API, are limited to the
requester (administrators may fetch any of the tenant's), and are audited.

Kinds here: ``metric`` and ``explorer`` (CSV, XLSX or JSON, up to
``MAX_BACKGROUND_ROWS`` rows) and ``run_bundle`` (JSON). The view PDF and the
finance and evidence packs are built by ``core.insights.packs``.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog

from core.insights import export as exports

logger = structlog.get_logger()

RETENTION = timedelta(days=7)
MAX_BACKGROUND_ROWS = 500_000
PAGE_ROWS = 50_000
ROW_FORMATS = ("csv", "xlsx", "json")
FORMATS_BY_KIND: dict[str, tuple[str, ...]] = {
    "metric": ROW_FORMATS,
    "explorer": ROW_FORMATS,
    "run_bundle": ("json",),
    "view_pdf": ("pdf",),
    "finance_pack": ("zip",),
    "evidence_pack": ("zip",),
}
MEDIA_TYPES: dict[str, str] = {
    **exports.MEDIA_TYPES,
    "pdf": "application/pdf",
    "zip": "application/zip",
}
# A job still queued or running this long after it was made is reported failed (the worker lost it).
STALE_AFTER = timedelta(hours=2)


class ExportJobError(Exception):
    """A request or build the job refuses: ``error`` is the machine code, ``status`` the HTTP status for the API."""

    def __init__(self, error: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Built:
    content: bytes
    filename: str
    row_count: int | None
    masked: bool
    # Facts recorded on the job (a pack's manifest SHA-256 and verification status).
    meta: dict[str, Any] = field(default_factory=dict)


Builder = Callable[[Any, Any, dict[str, Any]], Awaitable[Built]]
BUILDERS: dict[str, Builder] = {}


def builder(kind: str) -> Callable[[Builder], Builder]:
    def register(fn: Builder) -> Builder:
        BUILDERS[kind] = fn
        return fn

    return register


def caller_snapshot(caller: Any) -> dict[str, Any]:
    return {
        "user_id": str(caller.user_id) if caller.user_id else None,
        "role": caller.role,
        "domains": list(caller.domains) if caller.domains is not None else None,
        "is_admin": bool(caller.is_admin),
    }


def caller_from_snapshot(snapshot: dict[str, Any]) -> Any:
    from core.ownership import Caller

    raw_user = snapshot.get("user_id")
    return Caller(
        user_id=uuid.UUID(raw_user) if raw_user else None,
        role=str(snapshot.get("role") or ""),
        domains=list(snapshot["domains"]) if snapshot.get("domains") is not None else None,
        is_admin=bool(snapshot.get("is_admin")),
        is_machine=False,
    )


def _day(raw: Any) -> date | None:
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise ExportJobError("invalid_period", f"not a date: {raw!r}") from None


def validate(kind: str, fmt: str, params: dict[str, Any], caller: Any) -> dict[str, Any]:
    """The job's stored params for a request, or ExportJobError (unknown key, a key the caller may not read, a bad
    format or filter). The caller snapshot is added by the server, never taken from the request."""
    from core.insights import explore_registry, registry
    from core.insights import explorers as ex

    if kind not in FORMATS_BY_KIND:
        raise ExportJobError("unknown_kind", f"no export kind {kind!r}")
    if fmt not in FORMATS_BY_KIND[kind]:
        raise ExportJobError("invalid_format", f"{kind} exports as {', '.join(FORMATS_BY_KIND[kind])}")
    clean: dict[str, Any] = {}
    for name in ("from", "to"):
        day = _day(params.get(name))
        if day is not None:
            clean[name] = day.isoformat()
    company = params.get("company_id")
    if company:
        try:
            clean["company_id"] = str(uuid.UUID(str(company)))
        except ValueError:
            raise ExportJobError("invalid_company", "company_id is not a company id") from None
    if kind == "metric":
        key = str(params.get("key") or "")
        metric = registry.METRICS.get(key)
        if metric is None:
            raise ExportJobError("unknown_metric", f"no metric {key!r}", 404)
        allowed = set(registry.allowed_views(caller.role, is_admin=caller.is_admin))
        if not allowed.intersection(metric.views):
            raise ExportJobError("insights_metric_forbidden", "you may not read this metric", 403)
        clean["key"] = key
        clean["compare"] = "previous" if params.get("compare") == "previous" else "none"
        if params.get("granularity") in ("day", "week", "month"):
            clean["granularity"] = params["granularity"]
        group_by = params.get("group_by")
        if group_by:
            if group_by not in metric.group_by:
                raise ExportJobError("group_by_not_allowed", f"{key} may not be grouped by {group_by!r}")
            clean["group_by"] = group_by
    elif kind == "explorer":
        key = str(params.get("key") or "")
        explorer = explore_registry.EXPLORERS.get(key)
        if explorer is None:
            raise ExportJobError("unknown_explorer", f"no explorer {key!r}", 404)
        if not explorer.readable_by(caller.role, is_admin=caller.is_admin):
            raise ExportJobError("insights_explorer_forbidden", "you may not open this explorer", 403)
        query = {str(k): str(v) for k, v in dict(params.get("query") or {}).items()}
        try:
            ex.parse_query(explorer, query)
        except ex.ExploreQueryError as exc:
            raise ExportJobError(exc.error, exc.message) from None
        clean["key"] = key
        clean["query"] = query
    elif kind == "run_bundle":
        from core.insights.evidence import parse_at

        explorer = explore_registry.EXPLORERS["runs"]
        if not explorer.readable_by(caller.role, is_admin=caller.is_admin):
            raise ExportJobError("insights_explorer_forbidden", "you may not open runs", 403)
        try:
            clean["fact_id"] = str(uuid.UUID(str(params.get("fact_id"))))
        except ValueError:
            raise ExportJobError("invalid_run", "fact_id is not a run id") from None
        instant = parse_at(str(params.get("at") or ""))
        if instant is None:
            raise ExportJobError("invalid_at", "at is the run's started_at as an ISO timestamp with offset")
        clean["at"] = instant.isoformat()
    else:
        from core.insights import packs

        clean.update(packs.validate(kind, params, caller))
    return clean


async def create(
    session: Any,
    tenant_id: uuid.UUID,
    caller: Any,
    *,
    kind: str,
    fmt: str,
    params: dict[str, Any],
    include_pii: bool,
    now: datetime,
) -> Any:
    """Record a queued job (the caller queues the build after the commit)."""
    from core.insights import pii
    from core.models.insights import InsightsExport

    if include_pii and not pii.may_include_pii(caller.role, is_admin=caller.is_admin):
        raise ExportJobError("include_pii_forbidden", "only an administrator or auditor may ask for raw values", 403)
    clean = validate(kind, fmt, params, caller)
    clean["caller"] = caller_snapshot(caller)
    job = InsightsExport(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=caller.user_id,
        kind=kind,
        format=fmt,
        params=clean,
        include_pii=include_pii,
        status="queued",
        created_at=now,
        expires_at=now + RETENTION,
    )
    session.add(job)
    return job


def serialize(job: Any) -> dict[str, Any]:
    params = dict(job.params or {})
    return {
        "id": str(job.id),
        "kind": job.kind,
        "format": job.format,
        "key": params.get("key") or params.get("view"),
        "period": {"from": params.get("from"), "to": params.get("to")},
        "include_pii": bool(job.include_pii),
        "status": job.status,
        "error": job.error,
        "row_count": job.row_count,
        "size_bytes": job.size_bytes,
        "sha256": job.sha256,
        "filename": job.filename,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
        "signed_at": job.signed_at.isoformat() if job.signed_at else None,
        "signature": job.signature,
        "verification_status": params.get("verification_status"),
        "download": f"/insights/exports/{job.id}/download" if job.status == "ready" else None,
    }


def storage_key(tenant_id: uuid.UUID, job_id: uuid.UUID, filename: str) -> str:
    return f"insights-exports/{tenant_id}/{job_id}/{filename}"


# ------------------------------------------------------------------ builders


async def _metric_context(session: Any, tenant_id: uuid.UUID, caller: Any, params: dict[str, Any], **extra: Any):
    from api.v1.insights import _context

    return await _context(
        session,
        tenant_id,
        caller,
        start=_day(params.get("from")),
        end=_day(params.get("to")),
        compare=extra.pop("compare", params.get("compare") or "none"),
        company=uuid.UUID(params["company_id"]) if params.get("company_id") else None,
        **extra,
    )


async def _masked(session: Any, tenant_id: uuid.UUID, rows: list[Any], job: Any, caller: Any) -> tuple[list[Any], bool]:
    from core.insights import pii

    return await pii.apply(
        session, tenant_id, rows, include_pii=bool(job.include_pii), role=caller.role, is_admin=caller.is_admin
    )


@builder("metric")
async def _build_metric(session: Any, job: Any, params: dict[str, Any]) -> Built:
    from core.insights import registry

    caller = caller_from_snapshot(params["caller"])
    metric = registry.METRICS[params["key"]]
    ctx = await _metric_context(
        session,
        job.tenant_id,
        caller,
        params,
        granularity=params.get("granularity"),
        group_by=params.get("group_by"),
    )
    response = await registry.resolve(metric, ctx)
    rows = exports.export_rows(response)
    if len(rows) > MAX_BACKGROUND_ROWS:
        raise ExportJobError("export_too_large", f"{len(rows)} rows; at most {MAX_BACKGROUND_ROWS:,} in one export")
    rows, masked = await _masked(session, job.tenant_id, rows, job, caller)
    filters = {
        "from": ctx.period.start.isoformat(),
        "to": ctx.period.end.isoformat(),
        "granularity": ctx.period.granularity,
        "compare": params.get("compare") or "none",
        "company_id": params.get("company_id"),
        "group_by": params.get("group_by"),
        "pii": "masked" if masked else "raw",
    }
    content = await asyncio.to_thread(exports.render, job.format, rows, filters, response)
    return Built(content, exports.filename(metric.key, response["period"], job.format), len(rows), masked)


@builder("explorer")
async def _build_explorer(session: Any, job: Any, params: dict[str, Any]) -> Built:
    from api.v1.insights_explore import explorer_domains
    from core.insights import explore_registry
    from core.insights import explorers as ex

    snapshot = caller_from_snapshot(params["caller"])
    caller = replace(snapshot, domains=explorer_domains(snapshot.domains))
    explorer = explore_registry.EXPLORERS[params["key"]]
    query = ex.parse_query(explorer, dict(params.get("query") or {}))
    metric_ctx = await _metric_context(session, job.tenant_id, caller, params, compare="previous")
    rows: list[dict[str, Any]] = []
    response: dict[str, Any] = {}
    page = 1
    while True:
        ctx = ex.ExploreContext(
            metric=metric_ctx,
            query=replace(query, page=page, page_size=PAGE_ROWS),
            role=caller.role,
            is_admin=caller.is_admin,
        )
        response = await ex.resolve(explorer, ctx)
        if response["total"] > MAX_BACKGROUND_ROWS:
            raise ExportJobError(
                "export_too_large",
                f"{response['total']:,} rows; at most {MAX_BACKGROUND_ROWS:,} in one export. "
                "Narrow the period or filters.",
            )
        batch = ex.export_rows(explorer, response)
        rows.extend(batch)
        if not batch or len(rows) >= response["total"]:
            break
        page += 1
    rows, masked = await _masked(session, job.tenant_id, rows, job, caller)
    period = response.get("period") or {"from": params.get("from"), "to": params.get("to")}
    filters: dict[str, Any] = {
        "from": period.get("from"),
        "to": period.get("to"),
        "company_id": params.get("company_id"),
        "sort": query.sort,
        "order": query.order,
        "pii": "masked" if masked else "raw",
        **{f"filter[{name}]": value for name, value in (response.get("filters") or {}).items()},
    }
    definition = {
        "key": f"explore.{explorer.key}",
        "title": explorer.title,
        "definition": explorer.definition,
        "source": response.get("source"),
        "data_quality": response.get("data_quality"),
        "computed_at": response.get("computed_at"),
        "period": period,
        "hint": response.get("hint"),
        "columns": [(c.key, c.title, c.description) for c in explorer.columns],
    }
    content = await asyncio.to_thread(exports.render, job.format, rows, filters, definition)
    return Built(content, exports.filename(f"explore-{explorer.key}", period, job.format), len(rows), masked)


@builder("run_bundle")
async def _build_run_bundle(session: Any, job: Any, params: dict[str, Any]) -> Built:
    from api.v1.insights_explore import explorer_domains
    from core.insights.evidence import EvidenceNotFoundError, canonical_json, run_bundle

    snapshot = caller_from_snapshot(params["caller"])
    caller = replace(snapshot, domains=explorer_domains(snapshot.domains))
    ctx = await _metric_context(session, job.tenant_id, caller, {**params, "from": None, "to": None}, compare="none")
    try:
        built = await run_bundle(
            ctx,
            uuid.UUID(params["fact_id"]),
            datetime.fromisoformat(params["at"]),
            include_audit_payloads=caller.is_admin or caller.role == "auditor",
            generated_at=datetime.now(UTC),
        )
    except EvidenceNotFoundError:
        raise ExportJobError("run_not_found", "no such run for you", 404) from None
    return Built(canonical_json(built["bundle"]), f"run-{params['fact_id']}.json", None, False)


# ------------------------------------------------------------------ worker


async def build(tenant_id: uuid.UUID, job_id: uuid.UUID) -> str:
    """Build one queued job; returns its final status. Never raises for a refused build (the job says why)."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.insights import storage
    from core.insights.metrics import apply_statement_timeout
    from core.models.insights import InsightsExport

    async with get_tenant_session(tenant_id) as session:
        job = (
            await session.execute(
                select(InsightsExport)
                .where(InsightsExport.tenant_id == tenant_id, InsightsExport.id == job_id)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if job is None or job.status != "queued":
            return job.status if job is not None else "missing"
        job.status = "running"
        job.started_at = datetime.now(UTC)
    status = "failed"
    error: str | None = None
    built: Built | None = None
    try:
        async with get_tenant_session(tenant_id) as session:
            await apply_statement_timeout(session)
            job = await session.get(InsightsExport, job_id)
            make = BUILDERS.get(job.kind)
            if make is None:
                from core.insights import packs  # noqa: F401 - registers the pack builders

                make = BUILDERS.get(job.kind)
            if make is None:
                raise ExportJobError("unknown_kind", f"no builder for {job.kind}")
            built = await make(session, job, dict(job.params or {}))
        key = storage_key(tenant_id, job_id, built.filename)
        await asyncio.to_thread(
            storage.put, key, built.content, MEDIA_TYPES.get(job.format, "application/octet-stream")
        )
        status = "ready"
    except ExportJobError as exc:
        error = f"{exc.error}: {exc.message}"[:500]
    except storage.ExportStorageError as exc:
        error = str(exc)[:500]
    # enterprise-gate: broad-except-ok reason=a-failed-build-is-recorded-on-the-job-as-failed-and-logged
    except Exception as exc:
        logger.warning("insights_export_build_failed", export_id=str(job_id), error_type=type(exc).__name__)
        error = f"build_failed: {type(exc).__name__}"
    async with get_tenant_session(tenant_id) as session:
        job = await session.get(InsightsExport, job_id)
        job.status = status
        job.error = error
        job.finished_at = datetime.now(UTC)
        if built is not None and status == "ready":
            job.filename = built.filename
            job.storage_key = storage_key(tenant_id, job_id, built.filename)
            job.size_bytes = len(built.content)
            job.sha256 = hashlib.sha256(built.content).hexdigest()
            job.row_count = built.row_count
            params = dict(job.params or {})
            params["masked"] = built.masked
            params.update(built.meta)
            job.params = params
        user_id = job.user_id
        name = job.filename or job.kind
    logger.info("insights_export_built", export_id=str(job_id), status=status)
    await notify(tenant_id, user_id, status, name)
    return status


async def notify(tenant_id: uuid.UUID, user_id: uuid.UUID | None, status: str, name: str) -> None:
    """Tell the requester the file is ready or failed: an email with a link and a web push. Best effort."""
    if user_id is None:
        return
    from sqlalchemy import select

    from core.config import settings
    from core.database import get_tenant_session
    from core.models.user import User

    base = str(getattr(settings, "app_base_url", "") or "https://app.agenticorg.ai").rstrip("/")
    link = f"{base}/dashboard/insights/downloads"
    ready = status == "ready"
    title = "Your Insights export is ready" if ready else "Your Insights export could not be made"
    body = f"{name} is ready to download for seven days." if ready else f"{name} could not be made; the page says why."
    try:
        async with get_tenant_session(tenant_id) as session:
            email = (
                await session.execute(select(User.email).where(User.tenant_id == tenant_id, User.id == user_id))
            ).scalar_one_or_none()
        if email:
            from html import escape

            from core.email import send_email

            html = f'<p>{escape(body)}</p><p><a href="{escape(link)}">Open Downloads</a></p>'
            await asyncio.to_thread(send_email, email, title, html)
    # enterprise-gate: broad-except-ok reason=the-export-email-is-best-effort-the-downloads-page-shows-it-logged
    except Exception as exc:
        logger.warning("insights_export_email_failed", error_type=type(exc).__name__)
    try:
        from core.push.sender import send_push_notification_for_user

        await send_push_notification_for_user(
            str(tenant_id), str(user_id), title, body, data={"url": "/dashboard/insights/downloads"}
        )
    # enterprise-gate: broad-except-ok reason=web-push-is-best-effort-the-downloads-page-shows-the-file-logged
    except Exception as exc:
        logger.debug("insights_export_push_failed", error_type=type(exc).__name__)


async def expire(tenant_id: uuid.UUID, now: datetime) -> int:
    """Delete this tenant's export files past ``expires_at`` and mark them expired; report lost jobs as failed."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.insights import storage
    from core.models.insights import InsightsExport as X

    async with get_tenant_session(tenant_id) as session:
        due = list(
            (
                await session.execute(
                    select(X).where(X.tenant_id == tenant_id, X.status.in_(("ready", "failed")), X.expires_at <= now)
                )
            ).scalars()
        )
        stale = list(
            (
                await session.execute(
                    select(X).where(
                        X.tenant_id == tenant_id,
                        X.status.in_(("queued", "running")),
                        X.created_at <= now - STALE_AFTER,
                    )
                )
            ).scalars()
        )
        for job in stale:
            job.status = "failed"
            job.error = "build_lost: the export did not finish; ask for it again"
            job.finished_at = now
    removed = 0
    for job in due:
        if job.storage_key:
            await asyncio.to_thread(storage.delete, job.storage_key)
        removed += 1
    if due:
        async with get_tenant_session(tenant_id) as session:
            for job in due:
                row = await session.get(X, job.id)
                if row is not None:
                    row.status = "expired"
                    row.storage_key = None
    return removed
