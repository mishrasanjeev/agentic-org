"""Account Aggregator consent callback and management endpoints."""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from auth.aa_callback_signing import (
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    verify_aa_callback,
)
from connectors.finance.aa_consent_types import AACallbackPayload, ConsentRequest

logger = structlog.get_logger()

router = APIRouter(prefix="/aa", tags=["Account Aggregator"])


async def _consent_redis():
    """Shared async Redis pool; consent handles must survive restarts and replicas."""
    from core.async_redis import get_async_redis

    redis = await get_async_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="consent store unavailable")
    return redis


async def _get_consent_manager(tenant_id: str):
    """Build a consent manager whose state lives in tenant-scoped Redis keys.

    Audit 2026-09-13: pre-fix managers were cached in a process-local dict,
    so a provider callback landing on another replica (or after a restart)
    could never find the handle and was silently acknowledged.
    """
    from connectors.finance.aa_consent import AAConsentManager, RedisConsentStore

    redis = await _consent_redis()
    return AAConsentManager(store=RedisConsentStore(redis, tenant_id))


def _get_redis():
    """Lazy Redis client for nonce replay store. Mirrors api/v1/webhooks._get_redis."""
    import os

    import redis.asyncio as aioredis

    url = os.getenv("AGENTICORG_REDIS_URL", "redis://localhost:6379/1")
    return aioredis.from_url(url, decode_responses=True)


@router.post("/consent/callback")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:aa_callback.signed_external_input.sensitive.write",
    rate_limit="aa-callback-signed",
    idempotency="nonce-replay-protected",
    audit_event="aa.consent.callback",
    public_reason="provider-callback-hmac-timestamp-nonce-protected",
)
async def consent_callback(request: Request) -> dict[str, str]:
    """Webhook endpoint called by AA providers (Finvu / Setu) when
    consent status changes.

    SEC-2026-05-P1-004 (PR-C): every inbound callback MUST carry a
    valid HMAC-SHA256 signature, a fresh timestamp, and a previously-
    unseen nonce. The endpoint is still public (the auth middleware
    exempts it because the AA provider has no JWT to send), but it
    is no longer unauthenticated — the HMAC IS the authentication.

    Returns:
      - 200 with ``{"status": "ok", ...}`` on a valid, non-replayed callback.
      - 403 if the signature/secret/timestamp/nonce is missing or invalid.
      - 409 if the nonce has been seen before (replay).
    """
    raw_body = await request.body()

    # Verify signature + timestamp + nonce BEFORE parsing the body
    # into a Pydantic model. Don't even let an attacker probe Pydantic
    # error shapes via unsigned requests.
    redis_client = _get_redis()
    try:
        verdict = await verify_aa_callback(
            timestamp_header=request.headers.get(TIMESTAMP_HEADER),
            nonce_header=request.headers.get(NONCE_HEADER),
            signature_header=request.headers.get(SIGNATURE_HEADER),
            body=raw_body,
            redis_client=redis_client,
        )
    finally:
        # Best-effort close — don't raise if Redis was unreachable
        # during the verify call.
        try:
            await redis_client.close()
        # enterprise-gate: broad-except-ok reason=redis-close-failure-after-callback-verification-is-nonfatal
        except Exception as _close_exc:  # noqa: BLE001, S110 — close failures are non-fatal
            logger.debug("aa_callback_redis_close_failed", error=str(_close_exc))

    if verdict == "replay":
        logger.warning(
            "aa_consent_callback_replay_blocked",
            nonce=request.headers.get(NONCE_HEADER),
        )
        raise HTTPException(status_code=409, detail="callback replayed")
    if verdict != "ok":
        logger.warning(
            "aa_consent_callback_signature_invalid",
            verdict=verdict,
            timestamp=request.headers.get(TIMESTAMP_HEADER),
        )
        raise HTTPException(status_code=403, detail=f"callback rejected: {verdict}")

    # Now safe to parse the body.
    try:
        payload = AACallbackPayload(**json.loads(raw_body))
    except (ValidationError, json.JSONDecodeError) as exc:
        logger.warning("aa_consent_callback_bad_payload", error=str(exc))
        raise HTTPException(status_code=400, detail="invalid payload") from exc

    logger.info(
        "aa_consent_callback_received",
        consent_handle=payload.consent_handle,
        status=payload.consent_status.value,
        consent_id=payload.consent_id,
    )

    # Resolve the tenant that created this handle from the durable store.
    from connectors.finance.aa_consent import RedisConsentStore

    redis = await _consent_redis()
    tenant_id = await RedisConsentStore.tenant_for_handle(redis, payload.consent_handle)
    if tenant_id is None:
        # Non-2xx on purpose: the provider retries, and an operator sees the
        # miss instead of a silent "ok" for a handle nobody owns.
        logger.warning("aa_consent_callback_unmatched", handle=payload.consent_handle)
        raise HTTPException(status_code=404, detail="unknown consent handle")

    manager = await _get_consent_manager(tenant_id)
    result = await manager.handle_consent_callback(
        consent_handle=payload.consent_handle,
        consent_status=payload.consent_status,
        consent_id=payload.consent_id,
    )
    if "error" in result:
        logger.warning("aa_consent_callback_unmatched", handle=payload.consent_handle)
        raise HTTPException(status_code=404, detail="unknown consent handle")
    return {"status": "ok", "consent_handle": payload.consent_handle}


@router.get("/consent/{consent_handle}/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="aa_consent.sensitive.read",
    rate_limit="aa-consent-read",
    idempotency="read-only",
    audit_event="aa.consent.status",
)
async def consent_status(
    consent_handle: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Check the status of a consent request."""
    # SEC-2026-05-P3-014: ``tenant_id`` is a string (UUID) returned
    # directly by ``get_current_tenant``. Earlier code annotated it as
    # ``dict`` and called ``.get("tenant_id")`` — that raised
    # AttributeError on every consent request/status call.
    manager = await _get_consent_manager(tenant_id)
    result = await manager.get_consent_status(consent_handle)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/consent/request")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="aa_consent.external_financial_data.sensitive.write",
    rate_limit="aa-consent-request",
    idempotency="not_idempotent-creates-provider-consent-request",
    audit_event="aa.consent.request",
)
async def create_consent_request(
    params: ConsentRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, str]:
    """Create a new AA consent request.

    Returns consent_handle and redirect_url for user approval. The body is
    validated by ``ConsentRequest`` at the boundary (pre-fix it was an
    untyped ``dict`` re-parsed inside the handler).
    """
    # SEC-2026-05-P3-014: ``tenant_id`` is a string (UUID) returned
    # directly by ``get_current_tenant``. Earlier code annotated it as
    # ``dict`` and called ``.get("tenant_id")`` — that raised
    # AttributeError on every consent request/status call.
    manager = await _get_consent_manager(tenant_id)
    return await manager.create_consent_request(params)
