# SPDX-License-Identifier: Apache-2.0
"""A self-contained, offline-verifiable export of a tenant's commerce ledger.

The commerce ledger lives in the tenant's hash-chained ``audit_log``
(``core.commerce.ledger``, ``core.governance.audit_chain``). The chain links
every audit row of the tenant, so an export carries every sealed row of the
sequence range it covers, commerce or not (``commerce_ledger`` marks the
commerce ones): leaving a row out would leave a gap a verifier must report.

The bundle (``EXPORT_FORMAT``) holds:

- ``chain``: the link version, the genesis value, the sequence range, the link
  before the range (``prev_link``; genesis when the range starts at 1) and the
  tenant's head when the export was taken;
- ``rows``: each sealed row as signed and sealed: the signed columns in their
  canonical form, ``signature``, ``chain_seq``, ``chain_prev``, ``chain_hash``;
- ``anchors``: the head the last sealing stored (``audit_chain_anchors``) and
  every head Grantex witnessed, with its countersignature, or refused as a
  fork (``audit_chain_witness_checkpoints``);
- ``signatures``: row signatures are HMAC-SHA256 under the platform audit
  secret (``core.tool_gateway.audit_logger``). A symmetric secret is never
  exported, so an offline verifier recomputes and checks every chain link but
  cannot check row signatures; ``key_id`` identifies the key (a keyed digest,
  it reveals nothing about the secret) for whoever holds it;
- ``grantex``: the pinned Grantex issuer and, when it could be read from the
  cache or one fetch, its JWKS, so witness countersignatures verify offline.

``scripts/verify_ledger.py`` verifies a bundle with no database and no network.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from typing import Any

from core.governance.audit_chain_link import CHAIN_VERSION, GENESIS, SIGNED_COLUMNS, canonical_value

EXPORT_FORMAT = "agenticorg-commerce-ledger-export-v1"
MAX_EXPORT_ROWS = 5000
LEDGER_RESOURCE_TYPE = "commerce_ledger"
SIGNATURE_ALGORITHM = "HMAC-SHA256"
_KEY_ID_CONTEXT = b"agenticorg-audit-key-id-v1"


def audit_key_id(secret: bytes) -> str:
    """A stable identifier of the audit signing secret that discloses nothing about it."""
    return "audit-hmac-" + hmac.new(secret, _KEY_ID_CONTEXT, hashlib.sha256).hexdigest()[:16]


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    return None if value is None else str(value)


def export_row(row: Any) -> dict[str, Any]:
    """One sealed ``audit_log`` row with its signed columns in canonical form and its chain link."""
    out: dict[str, Any] = {"id": str(row.id)}
    for key in SIGNED_COLUMNS:
        out[key] = canonical_value(key, getattr(row, key, None))
    out["signature"] = getattr(row, "signature", None)
    out["chain_seq"] = int(row.chain_seq)
    out["chain_prev"] = row.chain_prev
    out["chain_hash"] = row.chain_hash
    out["sealed_at"] = _iso(getattr(row, "sealed_at", None))
    out["commerce_ledger"] = getattr(row, "resource_type", None) == LEDGER_RESOURCE_TYPE
    return out


def witness_view(row: Any) -> dict[str, Any]:
    return {
        "head_seq": int(row.head_seq),
        "head_hash": row.head_hash,
        "anchored_at": _iso(row.anchored_at),
        "status": row.status,
        "witness_id": row.witness_id,
        "countersignature": row.countersignature,
        "witnessed_at": _iso(row.witnessed_at),
        "fork_evidence": row.fork_evidence,
    }


async def build_export_bundle(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    from_seq: int = 1,
    to_seq: int | None = None,
    grantex_issuer: str | None = None,
    grantex_jwks: Any = None,
    secret: bytes | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The export bundle of sealed rows ``from_seq``..``to_seq`` (at most ``MAX_EXPORT_ROWS``) in the tenant session."""
    from sqlalchemy import func, select

    from core.config import settings
    from core.governance import audit_chain
    from core.governance.audit_witness import list_checkpoints
    from core.models.audit import AuditLog

    start = max(1, int(from_seq))
    head = audit_chain._head_of(await audit_chain._head_row(session, tenant_id))
    end = head.seq if to_seq is None else min(int(to_seq), head.seq)
    end = min(end, start + MAX_EXPORT_ROWS - 1)
    rows: list[Any] = []
    if end >= start:
        rows = await audit_chain._sealed_rows(session, tenant_id, start, end - start + 1)
        rows = [row for row in rows if int(row.chain_seq) <= end]
    prev_link = GENESIS
    if start > 1:
        before = await audit_chain._sealed_rows(session, tenant_id, start - 1, 1)
        prev_link = str(before[0].chain_hash) if before and int(before[0].chain_seq) == start - 1 else None
    anchor = await audit_chain._anchor(session, tenant_id)
    witnesses = await list_checkpoints(session, tenant_id, limit=1000)
    unsealed_commerce = int(
        (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.tenant_id == tenant_id,
                    AuditLog.chain_seq.is_(None),
                    AuditLog.resource_type == LEDGER_RESOURCE_TYPE,
                )
            )
        ).scalar()
        or 0
    )
    key = secret if secret is not None else settings.secret_key.encode()
    exported = [export_row(row) for row in rows]
    return {
        "format": EXPORT_FORMAT,
        "generated_at": _iso(now or datetime.now(UTC)),
        "tenant_id": str(tenant_id),
        "chain": {
            "version": CHAIN_VERSION,
            "genesis": GENESIS,
            "link_hash": "sha256(version|prev_link|canonical_signed_columns|signature), lowercase hex",
            "from_seq": start,
            "to_seq": exported[-1]["chain_seq"] if exported else start - 1,
            "prev_link": prev_link,
            "head": head.to_dict(),
            "row_count": len(exported),
        },
        "rows": exported,
        "anchors": {
            "audit_chain_anchor": anchor.to_dict() if anchor else None,
            "witness_checkpoints": [witness_view(row) for row in witnesses],
        },
        "signatures": {
            "algorithm": SIGNATURE_ALGORITHM,
            "key_id": audit_key_id(key),
            "signed_columns": list(SIGNED_COLUMNS),
            "offline_verifiable": False,
            "note": (
                "Row signatures are HMAC-SHA256 under the platform audit secret, which is never exported. "
                "Offline verification recomputes every chain link and checks anchors and witness "
                "countersignatures; row signatures can be checked only by a holder of the secret."
            ),
        },
        "grantex": {"issuer": grantex_issuer or None, "jwks": grantex_jwks if isinstance(grantex_jwks, dict) else None},
        "unsealed_commerce_rows": unsealed_commerce,
    }
