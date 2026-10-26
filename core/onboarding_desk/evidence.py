# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The evidence pack for an application: what was agreed, said, uploaded, checked and decided.

A pack has three parts:

- ``sections``: the application and its answers with their sources, the lead,
  every consent with its exact wording and hash, the contact attempts, the
  conversation transcript, each document's SHA-256, the requests for a person,
  the governed case with its policy result, memo recommendation and decision,
  and, when the Grantex evidence service is on, that service's signed export
  of the case's agent runs;
- ``manifest``: the pack's schema, tenant, application, time, and the SHA-256
  of each section's canonical JSON;
- ``signature``: Ed25519 over the manifest's canonical JSON with the platform's
  evidence key (``AGENTICORG_ONBOARDING_DESK_EVIDENCE_SIGNING_KEY``), or null
  when no key is configured.

``verify`` checks a pack offline with the published public key: every section
digest and the signature. Canonical JSON is UTF-8 with sorted keys and no
whitespace. Document contents are not in the pack; their SHA-256 binds them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from core.onboarding_desk import applications, store
from core.onboarding_desk.store import DeskError

logger = structlog.get_logger()

SCHEMA = "agenticorg.onboarding_evidence/1"
ALGORITHM = "Ed25519"
SECTIONS: tuple[str, ...] = (
    "application",
    "lead",
    "consents",
    "contact_attempts",
    "transcript",
    "documents",
    "handoffs",
    "case",
    "case_evidence",
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


# ── Keys ─────────────────────────────────────────────────────────────────────


def _private_key() -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.config import settings

    raw = str(getattr(settings, "onboarding_desk_evidence_signing_key", "") or "").strip()
    if not raw:
        return None
    try:
        seed = base64.b64decode(raw, validate=True)
        return Ed25519PrivateKey.from_private_bytes(seed)
    except ValueError as exc:
        raise DeskError(503, "evidence_key_invalid", "the evidence signing key is not a base64 Ed25519 seed") from exc


def _public_bytes(private_key: Any) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return bytes(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def key_id_for(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()[:16]


def public_key() -> dict[str, str] | None:
    """The published verification key, or None when packs are not signed."""
    private_key = _private_key()
    if private_key is None:
        return None
    raw = _public_bytes(private_key)
    return {"alg": ALGORITHM, "key_id": key_id_for(raw), "public_key": base64.b64encode(raw).decode("ascii")}


def sign(manifest: dict[str, Any]) -> dict[str, str] | None:
    private_key = _private_key()
    if private_key is None:
        return None
    raw = _public_bytes(private_key)
    return {
        "alg": ALGORITHM,
        "key_id": key_id_for(raw),
        "value": base64.b64encode(private_key.sign(canonical(manifest))).decode("ascii"),
    }


def verify(pack: Any, public_key_b64: str | None = None) -> list[str]:
    """Problems with a pack, or an empty list when every digest and the signature check out."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(pack, dict) or set(pack) != {"manifest", "signature", "sections"}:
        return ["pack must have exactly manifest, signature and sections"]
    manifest, signature, sections = pack["manifest"], pack["signature"], pack["sections"]
    problems: list[str] = []
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        return [f"manifest schema must be {SCHEMA}"]
    listed = manifest.get("sections") or {}
    if not isinstance(sections, dict) or set(sections) != set(listed):
        problems.append("sections differ from the manifest")
    for name, expected in listed.items():
        if name in sections and digest(sections[name]) != expected:
            problems.append(f"section {name} does not match its digest")
    if public_key_b64 is None:
        return problems
    if not isinstance(signature, dict) or signature.get("alg") != ALGORITHM:
        return [*problems, "pack is not signed"]
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
        if signature.get("key_id") != key_id_for(raw):
            return [*problems, "signed with a different key"]
        Ed25519PublicKey.from_public_bytes(raw).verify(base64.b64decode(signature["value"]), canonical(manifest))
    except (ValueError, KeyError, TypeError, InvalidSignature):
        problems.append("signature does not verify")
    return problems


# ── Building a pack ──────────────────────────────────────────────────────────


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


async def _handoffs(tenant_id: uuid.UUID, application_id: uuid.UUID) -> list[Any]:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingHandoff

    async with store._session(tenant_id) as session:
        return list(
            (
                await session.execute(
                    select(OnboardingHandoff)
                    .where(OnboardingHandoff.tenant_id == tenant_id, OnboardingHandoff.application_id == application_id)
                    .order_by(OnboardingHandoff.created_at)
                )
            ).scalars()
        )


async def _case_section(tenant_id: uuid.UUID, case_ref: str | None) -> tuple[Any, dict[str, Any]]:
    from core.onboarding_desk import cases

    if not case_ref:
        return None, {"status": "no_case"}
    case = await cases.get_case(tenant_id, case_ref)
    if case is None:
        return None, {"status": "case_not_found"}
    memo = case.memo or {}
    section = {
        "case_ref": case.case_ref,
        "purpose": case.purpose,
        "state": case.state,
        "provider": case.provider,
        "policy_id": case.policy_id,
        "application": case.application,
        "subject": case.subject,
        "policy_result": case.policy_result,
        "recommendation": memo.get("recommendation"),
        "screening_results": case.screening_results,
        "screening_dispositions": case.screening_dispositions,
        "decision": case.decision,
        "created_at": _iso(case.created_at),
        "completed_at": _iso(case.completed_at),
    }
    return section, await _case_export(case_ref)


async def _case_export(case_ref: str) -> dict[str, Any]:
    from core.cases.evidence import EvidenceServiceError, evidence_service

    try:
        service = evidence_service()
        if service is None:
            return {"status": "off"}
        exported = await service.export(case_ref, sign=True)
    except EvidenceServiceError as exc:
        return {"status": "unavailable", "reason": exc.reason}
    return {
        "status": "included",
        "root": exported.root,
        "anchor_hash": exported.anchor_hash,
        "export": base64.b64encode(exported.data).decode("ascii"),
    }


async def build_pack(tenant_id: uuid.UUID, application_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    row = await applications.get_application(tenant_id, application_id)
    lead, consents = await store.get_lead(tenant_id, row.lead_id)
    answers = await applications._answers(row)
    case, case_evidence = await _case_section(tenant_id, row.case_ref)
    sections: dict[str, Any] = {
        "application": {
            "id": str(row.id),
            "status": row.status,
            "merchant_type": row.merchant_type,
            "country": row.country,
            "region": row.region,
            "locale": row.locale,
            "created_at": _iso(row.created_at),
            "submitted_at": _iso(row.submitted_at),
            "case_ref": row.case_ref,
            "answers": answers["values"],
            "answer_sources": answers["sources"],
        },
        "lead": {
            "id": str(lead.id),
            "source_kind": lead.source_kind,
            "source_id": str(lead.source_id) if lead.source_id else None,
            "external_ref": lead.external_ref,
            "business_name": lead.business_name,
            "email_masked": lead.email_masked,
            "phone_masked": lead.phone_masked,
            "status": lead.status,
            "created_at": _iso(lead.created_at),
        },
        "consents": [
            {
                "id": str(c.id),
                "channel": c.channel,
                "purpose": c.purpose,
                "wording": c.wording,
                "wording_sha256": c.wording_sha256,
                "method": c.method,
                "source_ref": c.source_ref,
                "written": c.written,
                "captured_at": _iso(c.captured_at),
                "expires_at": _iso(c.expires_at),
                "status": c.status,
                "withdrawn_at": _iso(c.withdrawn_at),
                "withdrawn_via": c.withdrawn_via,
            }
            for c in consents
        ],
        "contact_attempts": [store.attempt_view(a) for a in await store.list_attempts(tenant_id, row.lead_id)],
        "transcript": await applications._transcript(row),
        "documents": [
            {**applications.document_view(d), "sha256": d.sha256}
            for d in await applications._documents(tenant_id, row.id)
        ],
        "handoffs": [
            {
                "channel": h.channel,
                "status": h.status,
                "created_at": _iso(h.created_at),
                "resolved_at": _iso(h.resolved_at),
                "resolved_by": h.resolved_by,
            }
            for h in await _handoffs(tenant_id, row.id)
        ],
        "case": case,
        "case_evidence": case_evidence,
    }
    manifest = {
        "schema": SCHEMA,
        "tenant_id": str(tenant_id),
        "application_id": str(row.id),
        "generated_at": moment.isoformat(),
        "sections": {name: digest(sections[name]) for name in SECTIONS},
    }
    logger.info("onboarding_evidence_built", signed=bool(_private_key()), case=bool(row.case_ref))
    return {"manifest": manifest, "signature": sign(manifest), "sections": sections}
