# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Grantex witnessing of audit chain heads (cross-repo contract section 7).

After the sealing task seals a tenant's chain (``core.tasks.audit_chain_tasks``)
it calls ``witness_head`` with the new head. The head is posted to Grantex
``POST /v1/audit/witness-checkpoints`` as ``{source: "agenticorg",
tenant_ref, chain: "audit_log", head_seq, head_hash, anchored_at}``. Grantex
answers with a ``witness_id`` and a countersignature, a compact JWS (``typ``
``audit-witness+jwt``) by its platform key over those fields and
``witnessed_at``. The countersignature is verified against the pinned Grantex
JWKS, its claims are held against the head that was posted, and it is stored
in ``audit_chain_witness_checkpoints`` beside the tenant's anchor.

A ``409 WITNESS_FORK`` means Grantex already witnessed another hash at that
sequence: the tenant's chain disagrees with a head it published earlier. That
is an irregularity: it is logged as an error and the fork evidence Grantex
returned (the stored witness and the presented head) is kept in a ``fork``
row. Verification (``core.commerce.ledger_export`` and
``scripts/verify_ledger.py``) holds the chain against every witnessed head.

Behind ``AGENTICORG_AUDIT_WITNESS_ENABLED`` (off by default). A failure to
reach Grantex is logged and the sealing stands; the next sealing posts its
head. Nothing retries or waits.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from core.commerce.grantex_service import (
    GrantexServiceClient,
    GrantexServiceUnavailableError,
    GrantexTokenError,
    grantex_service_client_from_env,
)

logger = structlog.get_logger()

WITNESS_PATH = "/v1/audit/witness-checkpoints"
WITNESS_TYP = "audit-witness+jwt"
WITNESS_SOURCE = "agenticorg"
WITNESS_CHAIN = "audit_log"
_MAX_FORK_EVIDENCE_BYTES = 64 * 1024


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def witness_body(tenant_id: uuid.UUID, head_seq: int, head_hash: str, anchored_at: datetime) -> dict[str, Any]:
    return {
        "source": WITNESS_SOURCE,
        "tenant_ref": str(tenant_id),
        "chain": WITNESS_CHAIN,
        "head_seq": int(head_seq),
        "head_hash": head_hash,
        "anchored_at": _iso(anchored_at),
    }


def check_witness_claims(claims: dict[str, Any], body: dict[str, Any], *, issuer: str, witness_id: Any) -> str | None:
    """Why a verified countersignature does not witness the posted head, or None."""
    # anchored_at is not compared: Grantex answers a repeated head with the witness it stored
    # first, whose anchored_at is the first posting's. The head itself (sequence and hash) must match.
    checks: tuple[tuple[bool, str], ...] = (
        (bool(issuer) and claims.get("iss") == issuer, "issuer_mismatch"),
        (claims.get("source") == body["source"], "source_mismatch"),
        (claims.get("tenant_ref") == body["tenant_ref"], "tenant_ref_mismatch"),
        (claims.get("chain") == body["chain"], "chain_mismatch"),
        (claims.get("head_seq") == body["head_seq"], "head_seq_mismatch"),
        (claims.get("head_hash") == body["head_hash"], "head_hash_mismatch"),
        (_parse(claims.get("anchored_at")) is not None, "anchored_at_missing"),
        (_parse(claims.get("witnessed_at")) is not None, "witnessed_at_missing"),
        (isinstance(witness_id, str) and claims.get("jti") == witness_id, "witness_id_mismatch"),
    )
    for passed, reason in checks:
        if not passed:
            return reason
    return None


async def witness_head(
    tenant_id: uuid.UUID,
    *,
    head_seq: int,
    head_hash: str,
    anchored_at: datetime | None,
    client: GrantexServiceClient | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Post one chain head for witnessing and store the verified answer. Never raises, never waits."""
    from core.config import settings

    if not (settings.audit_witness_enabled if enabled is None else enabled):
        return {"status": "skipped", "reason": "witness_disabled"}
    if head_seq < 1:
        return {"status": "skipped", "reason": "chain_empty"}
    service = client if client is not None else grantex_service_client_from_env()
    if service is None:
        logger.warning("audit_chain_witness_unavailable", tenant_id=str(tenant_id), reason="grantex_not_configured")
        return {"status": "unavailable", "reason": "grantex_not_configured"}
    body = witness_body(tenant_id, head_seq, head_hash, anchored_at or datetime.now(UTC))
    try:
        response = await service.post_json(WITNESS_PATH, body)
    except GrantexServiceUnavailableError as exc:
        logger.warning("audit_chain_witness_unavailable", tenant_id=str(tenant_id), reason=str(exc))
        return {"status": "unavailable", "reason": "grantex_unreachable"}
    if response.status_code == 409 and response.body.get("code") == "WITNESS_FORK":
        evidence = response.body.get("fork") if isinstance(response.body.get("fork"), dict) else response.body
        if len(json.dumps(evidence, default=str)) > _MAX_FORK_EVIDENCE_BYTES:
            evidence = {"truncated": True, "code": "WITNESS_FORK"}
        logger.error(
            "audit_chain_witness_irregularity",
            tenant_id=str(tenant_id),
            head_seq=head_seq,
            head_hash=head_hash,
            reason="WITNESS_FORK",
        )
        await _store(tenant_id, body, status="fork", fork_evidence=evidence)
        return {"status": "fork", "reason": "WITNESS_FORK", "fork_evidence": evidence}
    if response.status_code not in (200, 201):
        logger.warning("audit_chain_witness_refused", tenant_id=str(tenant_id), http_status=response.status_code)
        return {"status": "unavailable", "reason": f"grantex_http_{response.status_code}"}
    countersignature = response.body.get("countersignature")
    witness_id = response.body.get("witness_id")
    try:
        claims = await service.verify_token(countersignature, typ=WITNESS_TYP)
    except GrantexServiceUnavailableError:
        return {"status": "unavailable", "reason": "grantex_jwks_unavailable"}
    except GrantexTokenError as exc:
        logger.error("audit_chain_witness_irregularity", tenant_id=str(tenant_id), reason=str(exc))
        return {"status": "invalid", "reason": "countersignature_invalid"}
    problem = check_witness_claims(claims, body, issuer=service.issuer, witness_id=witness_id)
    if problem is not None:
        logger.error("audit_chain_witness_irregularity", tenant_id=str(tenant_id), reason=problem)
        return {"status": "invalid", "reason": problem}
    await _store(
        tenant_id,
        {**body, "anchored_at": claims["anchored_at"]},
        status="witnessed",
        witness_id=str(witness_id),
        countersignature=str(countersignature),
        witnessed_at=_parse(claims.get("witnessed_at")),
    )
    return {"status": "witnessed", "reason": "countersignature_verified", "witness_id": witness_id}


async def _store(
    tenant_id: uuid.UUID,
    body: dict[str, Any],
    *,
    status: str,
    witness_id: str | None = None,
    countersignature: str | None = None,
    witnessed_at: datetime | None = None,
    fork_evidence: Any = None,
) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from core.database import get_tenant_session
    from core.models.audit import AuditChainWitnessCheckpoint

    values = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "head_seq": int(body["head_seq"]),
        "head_hash": str(body["head_hash"]),
        "anchored_at": _parse(body["anchored_at"]),
        "status": status,
        "witness_id": witness_id,
        "countersignature": countersignature,
        "witnessed_at": witnessed_at,
        "fork_evidence": fork_evidence,
    }
    async with get_tenant_session(tenant_id) as session:
        await session.execute(
            insert(AuditChainWitnessCheckpoint)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "head_seq", "head_hash", "status"])
        )


async def list_checkpoints(session: Any, tenant_id: uuid.UUID, *, limit: int = 100) -> list[Any]:
    """The tenant's stored witness rows, newest head first."""
    from sqlalchemy import select

    from core.models.audit import AuditChainWitnessCheckpoint

    table = AuditChainWitnessCheckpoint
    query = (
        select(table)
        .where(table.tenant_id == tenant_id)
        .order_by(table.head_seq.desc(), table.created_at.desc())
        .limit(max(1, min(int(limit), 1000)))
    )
    return list((await session.scalars(query)).all())
