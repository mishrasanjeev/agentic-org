# SPDX-License-Identifier: Apache-2.0
"""Tamper-evident audit: each tenant's audit rows linked into a hash chain.

Every audit row is signed on its own with the platform's audit key
(``core.tool_gateway.audit_logger``), which proves the platform wrote the
row but not that the trail is whole: a removed, reordered or inserted row
leaves every remaining signature valid. The chain closes that gap. The
sealing task (``core.tasks.audit_chain_tasks``) takes each tenant's unsealed
rows in write order and gives each a sequence number, the previous link and
a link hash over the previous link, the row's signed payload and its
signature. The newest link is the chain head; the task logs it after every
sealing, so a log retention outside the platform anchors the chain.

Verification walks the sealed rows in order and recomputes every link: an
edited row changes its link hash, a removed row leaves a gap in the
sequence, an inserted or reordered row breaks the previous link, and the
first break is reported with its sequence number and reason. A row's own
signature is checked on the way; a row sealed without one (written before
signing existed) is counted, not a break.

Behind ``AGENTICORG_AUDIT_CHAIN_ENABLED`` (off by default): off, nothing is
sealed; verification and the status read whatever is sealed either way.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import settings
from core.tool_gateway.audit_logger import canonical_audit_payload, verify_audit_row

logger = structlog.get_logger()

CHAIN_VERSION = "1"
GENESIS = "0" * 64
PAGE = 1000
BREAK_REASONS: tuple[str, ...] = ("sequence_gap", "previous_link", "link_hash", "signature")


def link_hash(prev_hash: str, row: Any) -> str:
    """The link for ``row`` after ``prev_hash``: SHA-256 over the version, that link, the payload and the signature."""
    signature = str(getattr(row, "signature", None) or "")
    material = f"{CHAIN_VERSION}|{prev_hash}|{canonical_audit_payload(row)}|{signature}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Head:
    """The newest link of a tenant's chain; sequence 0 and the genesis value when nothing is sealed."""

    seq: int = 0
    hash: str = GENESIS
    sealed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "hash": self.hash, "sealed_at": self.sealed_at.isoformat() if self.sealed_at else None}


@dataclass(frozen=True)
class SealOutcome:
    tenant_id: uuid.UUID
    sealed: int
    head: Head
    more: bool

    def to_dict(self) -> dict[str, Any]:
        return {"tenant_id": str(self.tenant_id), "sealed": self.sealed, "head": self.head.to_dict(), "more": self.more}


@dataclass(frozen=True)
class Break:
    seq: int
    row_id: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "row_id": self.row_id, "reason": self.reason}


@dataclass
class Verification:
    tenant_id: uuid.UUID
    head: Head
    unsealed: int
    checked_from: int
    checked_to: int = 0
    verified: int = 0
    unsigned: int = 0
    first_break: Break | None = None
    breaks: list[Break] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.first_break is not None:
            return "broken"
        if self.head.seq == 0:
            return "empty"
        return "verified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "status": self.status,
            "head": self.head.to_dict(),
            "unsealed": self.unsealed,
            "checked_from": self.checked_from,
            "checked_to": self.checked_to,
            "verified": self.verified,
            "unsigned": self.unsigned,
            "first_break": self.first_break.to_dict() if self.first_break else None,
        }


# ---------------------------------------------------------------------------
# Queries (seams the tests replace)
# ---------------------------------------------------------------------------


async def _head_row(session: Any, tenant_id: uuid.UUID, *, lock: bool = False) -> Any:
    from sqlalchemy import select

    from core.models.audit import AuditLog

    query = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.chain_seq.is_not(None))
        .order_by(AuditLog.chain_seq.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


async def _unsealed_rows(session: Any, tenant_id: uuid.UUID, limit: int) -> list[Any]:
    from sqlalchemy import select

    from core.models.audit import AuditLog

    query = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.chain_seq.is_(None))
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .limit(limit)
        .with_for_update()
    )
    return list((await session.execute(query)).scalars().all())


async def _unsealed_count(session: Any, tenant_id: uuid.UUID) -> int:
    from sqlalchemy import func, select

    from core.models.audit import AuditLog

    query = (
        select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.chain_seq.is_(None))
    )
    return int((await session.execute(query)).scalar() or 0)


async def _sealed_rows(session: Any, tenant_id: uuid.UUID, from_seq: int, limit: int) -> list[Any]:
    from sqlalchemy import select

    from core.models.audit import AuditLog

    query = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.chain_seq.is_not(None), AuditLog.chain_seq >= from_seq)
        .order_by(AuditLog.chain_seq.asc())
        .limit(limit)
    )
    return list((await session.execute(query)).scalars().all())


def _head_of(row: Any) -> Head:
    if row is None:
        return Head()
    return Head(seq=int(row.chain_seq), hash=str(row.chain_hash or GENESIS), sealed_at=getattr(row, "sealed_at", None))


# ---------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------


async def seal(tenant_id: uuid.UUID, *, batch: int | None = None) -> SealOutcome:
    """Link the tenant's next batch of unsealed rows onto its chain; ``more`` says a further batch waits."""
    from core.database import get_tenant_session

    size = max(1, int(batch or settings.audit_chain_seal_batch))
    now = datetime.now(UTC)
    async with get_tenant_session(tenant_id) as session:
        head = _head_of(await _head_row(session, tenant_id, lock=True))
        rows = await _unsealed_rows(session, tenant_id, size)
        seq, prev = head.seq, head.hash
        for row in rows:
            seq += 1
            row.chain_seq = seq
            row.chain_prev = prev
            row.chain_hash = link_hash(prev, row)
            row.sealed_at = now
            prev = row.chain_hash
        if rows:
            head = Head(seq=seq, hash=prev, sealed_at=now)
    if rows:
        from observability.metrics import audit_chain_links_total

        audit_chain_links_total.inc(len(rows))
        # The head is the anchor: a log retention outside the platform keeps it.
        logger.info(
            "audit_chain_sealed", tenant_id=str(tenant_id), sealed=len(rows), head_seq=head.seq, head_hash=head.hash
        )
    return SealOutcome(tenant_id=tenant_id, sealed=len(rows), head=head, more=len(rows) >= size)


# ---------------------------------------------------------------------------
# Verification and status
# ---------------------------------------------------------------------------


async def verify(tenant_id: uuid.UUID, *, from_seq: int = 1, limit: int | None = None) -> Verification:
    """Recompute every link from ``from_seq`` on (at most ``limit`` rows) and report the first break."""
    from core.database import get_tenant_session

    start = max(1, int(from_seq))
    async with get_tenant_session(tenant_id) as session:
        head = _head_of(await _head_row(session, tenant_id))
        result = Verification(
            tenant_id=tenant_id, head=head, unsealed=await _unsealed_count(session, tenant_id), checked_from=start
        )
        if head.seq == 0:
            return result
        prev = GENESIS
        if start > 1:
            before = await _sealed_rows(session, tenant_id, start - 1, 1)
            if before and int(before[0].chain_seq) == start - 1:
                prev = str(before[0].chain_hash or "")
            else:
                result.first_break = Break(seq=start - 1, row_id=None, reason="sequence_gap")
                result.breaks.append(result.first_break)
                return result
        expected = start
        remaining = limit
        while remaining is None or remaining > 0:
            page = PAGE if remaining is None else min(PAGE, remaining)
            rows = await _sealed_rows(session, tenant_id, expected, page)
            if not rows:
                break
            for row in rows:
                seq = int(row.chain_seq)
                reason = _check(row, seq, expected, prev)
                if reason is not None:
                    result.first_break = Break(seq=seq, row_id=str(getattr(row, "id", "") or "") or None, reason=reason)
                    result.breaks.append(result.first_break)
                    return result
                if not getattr(row, "signature", None):
                    result.unsigned += 1
                result.verified += 1
                result.checked_to = seq
                prev = str(row.chain_hash)
                expected = seq + 1
            if remaining is not None:
                remaining -= len(rows)
            if len(rows) < page:
                break
    return result


def _check(row: Any, seq: int, expected: int, prev: str) -> str | None:
    if seq != expected:
        return "sequence_gap"
    if str(row.chain_prev or "") != prev:
        return "previous_link"
    if str(row.chain_hash or "") != link_hash(prev, row):
        return "link_hash"
    if getattr(row, "signature", None) and not verify_audit_row(row):
        return "signature"
    return None


async def status(tenant_id: uuid.UUID) -> dict[str, Any]:
    """The chain head, how many rows wait for sealing, and whether sealing is on."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        head = _head_of(await _head_row(session, tenant_id))
        unsealed = await _unsealed_count(session, tenant_id)
    return {"enabled": bool(settings.audit_chain_enabled), "head": head.to_dict(), "unsealed": unsealed}


async def evidence(tenant_id: uuid.UUID, *, recent: int = 1000) -> dict[str, Any]:
    """What the compliance evidence package records: the head, the backlog and a verification of the newest links.

    An unreadable part is reported, never raised: the package collects what it can.
    """
    section: dict[str, Any] = {"enabled": bool(settings.audit_chain_enabled)}
    try:
        current = await status(tenant_id)
        section["head"] = current["head"]
        section["unsealed"] = current["unsealed"]
    # enterprise-gate: broad-except-ok reason=unreadable-chain-head-degrades-to-unknown-in-the-evidence-package-logged
    except Exception as exc:
        logger.warning("audit_chain_evidence_head_unreadable", error_type=type(exc).__name__)
        section["head"] = None
        section["unsealed"] = None
        section["head_error"] = type(exc).__name__
        return section
    head_seq = int((section["head"] or {}).get("seq") or 0)
    if head_seq == 0:
        section["recent_verification"] = None
        return section
    try:
        checked = await verify(tenant_id, from_seq=max(1, head_seq - recent + 1), limit=recent)
        section["recent_verification"] = checked.to_dict()
    # enterprise-gate: broad-except-ok reason=an-unreadable-chain-degrades-to-unknown-in-the-evidence-package-logged
    except Exception as exc:
        logger.warning("audit_chain_evidence_verify_unreadable", error_type=type(exc).__name__)
        section["recent_verification"] = None
        section["verification_error"] = type(exc).__name__
    return section
