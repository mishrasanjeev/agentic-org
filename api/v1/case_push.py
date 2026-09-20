# SPDX-License-Identifier: Apache-2.0
"""Case hand-off: push endpoint configuration, dead letters, REST retrieval and inbound provider webhooks.

Endpoint configuration and dead-letter routes are tenant-admin only. A signing secret is returned
exactly once, when it is created; it is stored encrypted and never returned again. REST retrieval
returns the same ``case_push`` document a webhook delivery carries, so a system of record can pull
instead of (or as well as) receiving pushes.

``POST /webhooks/providers/{tenant_id}/{provider}/{path_token}`` carries no session - providers sign
their deliveries - but it is not open: the path token binds the inbox to one tenant, and only a
delivery whose signature verifies can trigger anything (``core.cases.provider_webhooks``). The body
is read under a size cap before any database work, and every outcome answers the same 202, so a
caller learns neither which attempts were close nor whether the tenant has governed cases enabled.
Tenant admins read their own inbox path from ``GET /case-push/provider-webhook-inbox``.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.governed_cases import _actor, _error, _session, get_case_runtime
from core.cases.provider_webhooks import (
    MAX_BODY_BYTES,
    RequeryRequest,
    receive_provider_webhook,
    webhook_path,
)
from core.cases.push import (
    build_case_push,
    configure_endpoint,
    decrypt_keys,
    endpoint_view,
    event_id_for,
    list_dead_letters,
    replay_dead_letter,
    retire_previous_keys,
    rotate_signing_key,
    snapshot_event_type,
)
from core.cases.runtime import CaseRuntime, investigate_case
from core.cases.states import CaseError
from core.cases.store import get_case

logger = structlog.get_logger()

router = APIRouter()

PROVIDER_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class EndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2048)
    enabled: bool = True


def _outbox_view(row: Any) -> dict[str, Any]:
    return {
        "outbox_id": str(row.id),
        "event_id": str(row.event_id),
        "event_type": row.event_type,
        "status": row.status,
        "attempts": row.attempts,
        "last_error": row.last_error,
        "last_status_code": row.last_status_code,
        "replay_count": row.replay_count,
        "payload_sha256": row.payload_sha256,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
        "dead_lettered_at": row.dead_lettered_at.isoformat() if row.dead_lettered_at else None,
    }


@router.get("/case-push/endpoint")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.case_push.read", rate_limit="standard")
async def get_push_endpoint(
    tenant_id: str = Depends(get_current_tenant),
    _admin: Any = require_tenant_admin,
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    from core.cases.push import _endpoint

    try:
        tenant = uuid.UUID(tenant_id)
        await runtime.require_enabled(tenant)
        async with _session(tenant_id) as session:
            endpoint = await _endpoint(session, tenant)
            key_ids = [k.key_id for k in await decrypt_keys(endpoint)] if endpoint else []
            return endpoint_view(endpoint, key_ids)
    except CaseError as exc:
        return _error(exc)


@router.put("/case-push/endpoint")
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.case_push.write", rate_limit="credential-write",
    idempotency="put-replaces-endpoint-url", audit_event="case_push.endpoint_configured",
)  # fmt: skip
async def put_push_endpoint(
    body: EndpointRequest,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    _admin: Any = require_tenant_admin,
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        tenant = uuid.UUID(tenant_id)
        await runtime.require_enabled(tenant)
        async with _session(tenant_id) as session:
            endpoint, key = await configure_endpoint(
                session, tenant, url=body.url, enabled=body.enabled, now=runtime.clock()
            )
            view = endpoint_view(endpoint, [key.key_id] if key else [])
        logger.info("case_push_endpoint_configured", tenant_id=tenant_id, actor=_actor(user), created=key is not None)
        if key is not None:
            # Shown once: the only time the secret leaves the server.
            view = {**view, "signing_key": {"key_id": key.key_id, "secret": key.secret}}
        return view
    except CaseError as exc:
        return _error(exc)


@router.post("/case-push/endpoint/rotate-key")
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.case_push.write", rate_limit="credential-write",
    idempotency="not-idempotent-each-call-adds-a-key", audit_event="case_push.signing_key_rotated",
)  # fmt: skip
async def rotate_push_key(
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    _admin: Any = require_tenant_admin,
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        tenant = uuid.UUID(tenant_id)
        await runtime.require_enabled(tenant)
        async with _session(tenant_id) as session:
            key = await rotate_signing_key(session, tenant, now=runtime.clock())
        logger.info("case_push_signing_key_rotated", tenant_id=tenant_id, actor=_actor(user), key_id=key.key_id)
        return {"signing_key": {"key_id": key.key_id, "secret": key.secret}}
    except CaseError as exc:
        return _error(exc)


@router.post("/case-push/endpoint/retire-previous-keys")
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.case_push.write", rate_limit="credential-write",
    idempotency="idempotent-keeps-only-the-active-key", audit_event="case_push.signing_keys_retired",
)  # fmt: skip
async def retire_push_keys(
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    _admin: Any = require_tenant_admin,
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        tenant = uuid.UUID(tenant_id)
        await runtime.require_enabled(tenant)
        async with _session(tenant_id) as session:
            remaining = await retire_previous_keys(session, tenant, now=runtime.clock())
        logger.info("case_push_signing_keys_retired", tenant_id=tenant_id, actor=_actor(user))
        return {"key_ids": remaining}
    except CaseError as exc:
        return _error(exc)


@router.get("/case-push/dead-letters")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.case_push.read", rate_limit="standard")
async def get_dead_letters(
    limit: int = Query(default=100, ge=1, le=500),
    tenant_id: str = Depends(get_current_tenant),
    _admin: Any = require_tenant_admin,
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        tenant = uuid.UUID(tenant_id)
        await runtime.require_enabled(tenant)
        async with _session(tenant_id) as session:
            return {
                "dead_letters": [_outbox_view(row) for row in await list_dead_letters(session, tenant, limit=limit)]
            }
    except CaseError as exc:
        return _error(exc)


@router.post("/case-push/dead-letters/{outbox_id}/replay")
@route_meta(
    auth_required=True, tenant_required=True, scope="approvals.case_push.write", rate_limit="admin-mutating",
    idempotency="same-event-id-receivers-deduplicate", audit_event="case_push.dead_letter_replayed",
)  # fmt: skip
async def replay_push_dead_letter(
    outbox_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
    user: dict[str, Any] = Depends(get_current_user),
    _admin: Any = require_tenant_admin,
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        tenant = uuid.UUID(tenant_id)
        await runtime.require_enabled(tenant)
        async with _session(tenant_id) as session:
            row = await replay_dead_letter(session, tenant, outbox_id, actor=_actor(user), now=runtime.clock())
            view = _outbox_view(row)
        runtime.push_kick(tenant)
        return view
    except CaseError as exc:
        return _error(exc)


@router.get("/governed-cases/{case_ref}/push-payload")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.governed_cases.read", rate_limit="standard")
async def get_case_push_payload(
    case_ref: str, tenant_id: str = Depends(get_current_tenant), runtime: CaseRuntime = Depends(get_case_runtime)
) -> Any:
    """REST retrieval of the ``case_push`` document for the case as it is now (same event id as its push)."""
    from core.cases.push import PushPayloadError

    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            event_type = snapshot_event_type(case)
            try:
                return build_case_push(
                    case, event_type=event_type, event_id=event_id_for(case, event_type), occurred_at=case.updated_at
                )
            except PushPayloadError as exc:
                raise CaseError("payload_invalid", status=409) from exc
    except CaseError as exc:
        return _error(exc)


@router.get("/governed-cases/{case_ref}/push-deliveries")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.governed_cases.read", rate_limit="standard")
async def get_case_push_deliveries(
    case_ref: str, tenant_id: str = Depends(get_current_tenant), runtime: CaseRuntime = Depends(get_case_runtime)
) -> Any:
    from sqlalchemy import select

    from core.models.case_push import CasePushOutbox

    try:
        await runtime.require_enabled(uuid.UUID(tenant_id))
        async with _session(tenant_id) as session:
            case = await get_case(session, tenant_id, case_ref)
            rows = await session.execute(
                select(CasePushOutbox)
                .where(CasePushOutbox.tenant_id == case.tenant_id, CasePushOutbox.case_id == case.id)
                .order_by(CasePushOutbox.created_at)
            )
            return {"case_ref": case_ref, "deliveries": [_outbox_view(row) for row in rows.scalars()]}
    except CaseError as exc:
        return _error(exc)


@router.get("/case-push/provider-webhook-inbox")
@route_meta(auth_required=True, tenant_required=True, scope="approvals.case_push.read", rate_limit="standard")
async def get_provider_webhook_inbox(
    provider: str = Query(pattern=PROVIDER_PATTERN, max_length=64),
    tenant_id: str = Depends(get_current_tenant),
    _admin: Any = require_tenant_admin,
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    """The path this tenant's provider must deliver to. Treat it as a credential: it is what binds
    a delivery to this tenant, so anyone holding it can present events for this tenant (they still
    have to be signed)."""
    try:
        tenant = uuid.UUID(tenant_id)
        await runtime.require_enabled(tenant)
        return {"provider": provider, "path": webhook_path(tenant, provider)}
    except CaseError as exc:
        return _error(exc)


async def _requery_in_background(runtime: CaseRuntime) -> Any:
    async def requery(request: RequeryRequest) -> None:
        try:
            await investigate_case(
                request.tenant_id,
                request.case_ref,
                runtime=runtime,
                actor=request.actor,
                reason=request.reason,
                keep_state_on_failure=True,
            )
        except CaseError as exc:
            logger.warning("provider_event_requery_refused", case_ref=request.case_ref, reason=exc.reason)

    return requery


async def _read_capped_body(request: Request) -> bytes:
    """Read at most ``MAX_BODY_BYTES``, refusing on the declared length before reading anything."""
    declared = request.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise CaseError("webhook_body_too_large", status=413)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise CaseError("webhook_body_too_large", status=413)
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/webhooks/providers/{tenant_id}/{provider}/{path_token}", status_code=202)
@route_meta(
    auth_required=False, tenant_required=True, scope="public:webhooks.providers", rate_limit="provider-webhook",
    idempotency="verified-event-id-is-single-use", audit_event="providers.webhook_received",
    public_reason="per-tenant-path-token-plus-provider-signature-and-body-treated-as-untrusted-trigger",
)  # fmt: skip
async def receive_provider_event(
    tenant_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    provider: str = Path(pattern=PROVIDER_PATTERN, max_length=64),
    path_token: str = Path(pattern=r"^[0-9a-f]{32}$"),
    runtime: CaseRuntime = Depends(get_case_runtime),
) -> Any:
    try:
        body = await _read_capped_body(request)
    except CaseError as exc:
        return _error(exc)
    try:
        scheduled: list[RequeryRequest] = []

        async def schedule(request_to_run: RequeryRequest) -> None:
            scheduled.append(request_to_run)

        receipt = await receive_provider_webhook(
            tenant_id=tenant_id,
            provider_name=provider,
            path_token=path_token,
            headers=dict(request.headers),
            body=body,
            runtime=runtime,
            requery=schedule,
            now=runtime.clock(),
        )
        requery = await _requery_in_background(runtime)
        for scheduled_requery in scheduled:
            background.add_task(requery, scheduled_requery)
        logger.info("provider_event_accepted_for_processing", outcome=receipt.outcome, requeried=len(receipt.requeried))
    except CaseError as exc:
        # One answer for every refusal: an unauthenticated caller learns nothing about the tenant,
        # the feature flag or the provider from the status code.
        logger.info("provider_event_not_processed", reason=exc.reason)
    return JSONResponse(status_code=202, content={"status": "received"})
