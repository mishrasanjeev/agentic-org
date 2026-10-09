# SPDX-License-Identifier: Apache-2.0
"""Findings: what the detectors raised, kept once each, and the disposition a person gives them.

A finding is kept under its fingerprint (kind, entity, the rows behind
it), so running the detectors again never raises the same one twice. It
stays open until a person dispositions it: dismissed with a reason,
confirmed, or escalated to a governed case; nothing is filed or closed
by the detectors themselves. The thresholds the detectors use come from
the business console, so a bank tunes them without a release.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.txn import aggregate, detectors, narrative, records
from core.txn.records import TxnError

logger = structlog.get_logger()

STATUSES = ("open", "dismissed", "confirmed", "escalated")
OUTCOMES = ("dismiss", "confirm", "escalate")
MAX_LIST = 500


async def thresholds_for(tenant_id: uuid.UUID) -> detectors.Thresholds:
    """The detectors' thresholds from the business console; the catalogue's defaults where unset or off."""
    from core.workbench import console

    found = await console.effective(
        tenant_id,
        [
            "txn.structuring_threshold",
            "txn.structuring_window_days",
            "txn.structuring_min_count",
            "txn.passthrough_window_hours",
            "txn.passthrough_ratio",
            "txn.passthrough_min_amount",
        ],
    )
    base = detectors.Thresholds()
    return detectors.Thresholds(
        structuring_threshold=float(found.get("txn.structuring_threshold", base.structuring_threshold)),
        structuring_window_days=int(found.get("txn.structuring_window_days", base.structuring_window_days)),
        structuring_min_count=int(found.get("txn.structuring_min_count", base.structuring_min_count)),
        passthrough_window_hours=int(found.get("txn.passthrough_window_hours", base.passthrough_window_hours)),
        passthrough_ratio=float(found.get("txn.passthrough_ratio", base.passthrough_ratio)),
        passthrough_min_amount=float(found.get("txn.passthrough_min_amount", base.passthrough_min_amount)),
    )


def finding_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "entity_kind": row.entity_kind,
        "entity_ref": row.entity_ref,
        "severity": row.severity,
        "status": row.status,
        "summary": row.summary,
        "facts": dict(row.facts or {}),
        "record_refs": list(row.record_refs or []),
        "fingerprint": row.fingerprint,
        "detected_at": row.detected_at.isoformat() if row.detected_at else None,
        "disposition": dict(row.disposition or {}),
        "case_ref": row.case_ref,
        "narrative": dict(getattr(row, "narrative", None) or {}),
        "narrative_at": row.narrative_at.isoformat() if getattr(row, "narrative_at", None) else None,
        "evidence_digest": getattr(row, "evidence_digest", None),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def detect(
    tenant_id: uuid.UUID, *, account: str | None = None, since_days: int = 90, kinds: list[str] | None = None
) -> dict[str, Any]:
    """Run the detectors over the recent records (one account or all) and keep every new finding."""
    from core.database import get_tenant_session
    from core.models.txn_finding import TxnFinding

    wanted = tuple(k for k in (kinds or detectors.KINDS) if k in detectors.KINDS)
    if not wanted:
        raise TxnError(422, "kind_unknown", f"kinds are among {', '.join(detectors.KINDS)}")
    thresholds = await thresholds_for(tenant_id)
    rows = await records.list_records(
        tenant_id, account=account, since=records.window_start(since_days), limit=records.MAX_LIST
    )
    found = detectors.run_all(rows, thresholds, kinds=wanted)
    if not found:
        return {"records": len(rows), "findings": [], "new": 0, "known": 0, "thresholds": thresholds.to_dict()}
    by_fingerprint: dict[str, dict[str, Any]] = {}
    for item in found:
        by_fingerprint.setdefault(item["fingerprint"], item)
    ordered = sorted(by_fingerprint.items())
    now = datetime.now(UTC)
    inserted_fingerprints: set[str] = set()
    async with get_tenant_session(tenant_id) as session:
        # Bound SQL parameters while keeping one transaction and a stable conflict-lock order.
        for start in range(0, len(ordered), records.MAX_BATCH):
            result = await session.execute(
                insert(TxnFinding)
                .values(
                    [
                        {
                            "tenant_id": tenant_id,
                            "kind": item["kind"],
                            "entity_kind": item["entity_kind"],
                            "entity_ref": item["entity_ref"][:64],
                            "severity": item["severity"],
                            "status": "open",
                            "summary": item["summary"][:1000],
                            "facts": item["facts"],
                            "record_refs": item["record_refs"],
                            "fingerprint": fingerprint,
                            "detected_at": now,
                            "disposition": {},
                        }
                        for fingerprint, item in ordered[start : start + records.MAX_BATCH]
                    ]
                )
                .on_conflict_do_nothing(index_elements=[TxnFinding.tenant_id, TxnFinding.fingerprint])
                .returning(TxnFinding.fingerprint)
            )
            inserted_fingerprints.update(result.scalars().all())
    created = [item for fingerprint, item in by_fingerprint.items() if fingerprint in inserted_fingerprints]
    logger.info("txn_detectors_ran", records=len(rows), found=len(found), new=len(created))
    return {
        "records": len(rows),
        "findings": created,
        "new": len(created),
        "known": len(found) - len(created),
        "thresholds": thresholds.to_dict(),
    }


async def list_findings(
    tenant_id: uuid.UUID,
    *,
    status: str | None = None,
    kind: str | None = None,
    entity_ref: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.txn_finding import TxnFinding

    statement = select(TxnFinding).where(TxnFinding.tenant_id == tenant_id)
    if status:
        statement = statement.where(TxnFinding.status == status)
    if kind:
        statement = statement.where(TxnFinding.kind == kind)
    if entity_ref:
        statement = statement.where(TxnFinding.entity_ref == entity_ref[:64])
    statement = statement.order_by(TxnFinding.detected_at.desc()).limit(max(1, min(limit, MAX_LIST)))
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(statement)).scalars().all()
    return [finding_dict(row) for row in rows]


async def get_finding(
    tenant_id: uuid.UUID, finding_id: uuid.UUID, *, with_records: bool = True
) -> dict[str, Any] | None:
    from core.database import get_tenant_session
    from core.models.txn_finding import TxnFinding
    from core.models.txn_record import TxnRecord

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(TxnFinding).where(TxnFinding.tenant_id == tenant_id, TxnFinding.id == finding_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        found = finding_dict(row)
        if with_records and row.record_refs:
            rows = (
                (
                    await session.execute(
                        select(TxnRecord)
                        .where(TxnRecord.tenant_id == tenant_id, TxnRecord.record_ref.in_(list(row.record_refs)))
                        .order_by(TxnRecord.booked_at)
                    )
                )
                .scalars()
                .all()
            )
            found["records"] = [records.record_dict(r) for r in rows]
    return found


async def disposition(
    tenant_id: uuid.UUID, finding_id: uuid.UUID, *, outcome: str, notes: str, user_id: str, case_ref: str | None = None
) -> dict[str, Any]:
    """A person's decision on a finding: dismissed with a reason, confirmed, or escalated to a governed case."""
    from core.database import get_tenant_session
    from core.models.txn_finding import TxnFinding

    if outcome not in OUTCOMES:
        raise TxnError(422, "outcome_unknown", f"outcome is one of {', '.join(OUTCOMES)}")
    if outcome == "dismiss" and not notes.strip():
        raise TxnError(422, "reason_required", "A dismissal needs a reason")
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(TxnFinding)
                .where(TxnFinding.tenant_id == tenant_id, TxnFinding.id == finding_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise TxnError(404, "not_found", "No such finding")
        if row.status != "open":
            raise TxnError(409, "decided", f"The finding is already {row.status}")
        row.status = {"dismiss": "dismissed", "confirm": "confirmed", "escalate": "escalated"}[outcome]
        row.disposition = {
            "outcome": row.status,
            "by": str(user_id)[:128],
            "at": datetime.now(UTC).isoformat(),
            "notes": notes.strip()[:2000],
        }
        row.case_ref = (case_ref or "")[:128] or None
        row.updated_at = datetime.now(UTC)
        answer = finding_dict(row)
    logger.info("txn_finding_dispositioned", outcome=answer["status"], kind=answer["kind"])
    return answer


async def entity(tenant_id: uuid.UUID, kind: str, ref: str, *, since_days: int = 365) -> dict[str, Any]:
    """One entity's aggregation with its findings."""
    if kind not in aggregate.ENTITY_KINDS:
        raise TxnError(422, "kind_unknown", f"kind is one of {', '.join(aggregate.ENTITY_KINDS)}")
    since = records.window_start(since_days)
    if kind == "account":
        rows = await records.list_records(tenant_id, account=ref, since=since, limit=records.MAX_LIST)
    elif kind == "customer":
        rows = await records.list_records(tenant_id, customer_ref=ref, since=since, limit=records.MAX_LIST)
    else:
        rows = await records.list_records(tenant_id, counterparty=ref, since=since, limit=records.MAX_LIST)
    found = await list_findings(tenant_id, limit=MAX_LIST)
    return aggregate.entity_view(rows, kind, ref, found)


async def draft_narrative(
    tenant_id: uuid.UUID, finding_id: uuid.UUID, *, method: str = "auto", complete: Any = None
) -> dict[str, Any]:
    """Draft the narrative of a finding from its facts, its rows and its entity view, and keep it on the finding.

    The finding stays open: the draft is for a person to review in the queue, never a filing.
    """
    from core.database import get_tenant_session
    from core.models.txn_finding import TxnFinding

    if method not in narrative.METHODS:
        raise TxnError(422, "method_unknown", f"method is one of {', '.join(narrative.METHODS)}")
    found = await get_finding(tenant_id, finding_id, with_records=True)
    if found is None:
        raise TxnError(404, "not_found", "No such finding")
    view = await entity(tenant_id, found["entity_kind"], found["entity_ref"])
    try:
        text = await narrative.draft(
            tenant_id, found, list(found.get("records") or []), view, method=method, complete=complete
        )
    except ValueError as exc:
        raise TxnError(422, "method_unknown", str(exc)) from None
    # enterprise-gate: broad-except-ok reason=model-boundary-refused-with-an-explicit-error-nothing-kept
    except Exception as exc:  # noqa: BLE001 - the model boundary when the caller insisted on the model
        logger.warning("txn_narrative_failed", error_type=type(exc).__name__)
        raise TxnError(502, "narrative_failed", "The model did not produce a narrative") from None
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(TxnFinding)
                .where(TxnFinding.tenant_id == tenant_id, TxnFinding.id == finding_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise TxnError(404, "not_found", "No such finding")
        row.narrative = text
        row.narrative_at = datetime.now(UTC)
        row.updated_at = row.narrative_at
        answer = finding_dict(row)
    logger.info("txn_narrative_drafted", method=text.get("method"), recommendation=text.get("recommendation"))
    return answer


async def evidence(tenant_id: uuid.UUID, finding_id: uuid.UUID, *, hops: int = 2) -> dict[str, Any]:
    """The evidence package of a finding: finding and narrative, rows, entity view, fund-flow rows, a digest."""
    from core.database import get_tenant_session
    from core.models.txn_finding import TxnFinding
    from core.txn import graph

    found = await get_finding(tenant_id, finding_id, with_records=True)
    if found is None:
        raise TxnError(404, "not_found", "No such finding")
    view = await entity(tenant_id, found["entity_kind"], found["entity_ref"])
    flow = await graph.export(
        tenant_id,
        "account" if found["entity_kind"] == "account" else found["entity_kind"],
        found["entity_ref"],
        hops=hops,
        findings=[found],
    )
    package = {
        "finding": {k: v for k, v in found.items() if k != "records"},
        "records": list(found.get("records") or []),
        "entity": view,
        "fund_flow": {"graph": flow["graph"], "records": flow["records"]},
        "exported_at": datetime.now(UTC).isoformat(),
    }
    package["digest"] = narrative.digest(package)
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(TxnFinding)
                .where(TxnFinding.tenant_id == tenant_id, TxnFinding.id == finding_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is not None:
            row.evidence_digest = package["digest"]
            row.updated_at = datetime.now(UTC)
    return {**package, "csv": flow["csv"]}
