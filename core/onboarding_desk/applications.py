# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Applications: one per lead, shared by every channel, saved as the applicant goes.

An applicant opens their application from the onboarding link, answers on the
web page or by text message, uploads documents and confirms the values they
suggest, asks for a person when they want one, and submits once every required
item is in. Answers, the transcript and documents are encrypted under the
tenant key; the row keeps only which items are answered. Writes take a lock on
the application row, so answers given on two channels at once are never lost.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from core.onboarding_desk import documents, questions, store
from core.onboarding_desk.store import DeskError

logger = structlog.get_logger()

CLOSED_LEAD_STATUSES = ("opted_out", "closed")
MAX_ANSWERS_PER_CALL = 20
MAX_TRANSCRIPT = 200
MAX_TRANSCRIPT_TEXT = 1000
MAX_MESSAGE = 1000


# ── Encryption ───────────────────────────────────────────────────────────────


async def _kek(tenant_id: uuid.UUID) -> str:
    """Resolve the tenant key before taking a row lock: resolving opens its own database session."""
    from core.crypto.tenant_secrets import resolve_tenant_kek

    return await resolve_tenant_kek(tenant_id)


async def _seal(kek: str, plaintext: str) -> str:
    from core.crypto.tenant_secrets import encrypt_with_kek

    return await asyncio.to_thread(encrypt_with_kek, plaintext, kek)


async def _open(ciphertext: str | None) -> str | None:
    return await store._decrypt(ciphertext) if ciphertext else None


async def _answers(row: Any) -> dict[str, Any]:
    raw = await _open(row.answers_ciphertext)
    data = json.loads(raw) if raw else {}
    return {"values": dict(data.get("values") or {}), "sources": dict(data.get("sources") or {})}


async def _transcript(row: Any) -> list[dict[str, Any]]:
    raw = await _open(row.transcript_ciphertext)
    return list(json.loads(raw)) if raw else []


def _channel_of(source: str) -> str:
    return "web" if source.startswith("document:") else source


# ── Policy ───────────────────────────────────────────────────────────────────


async def get_application_policy(tenant_id: uuid.UUID) -> questions.ApplicationPolicy:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    async with store._session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
    return questions.application_policy_from((row.settings or {}).get("application") if row else None)


async def put_application_policy(
    tenant_id: uuid.UUID, raw: dict[str, Any], *, actor: str
) -> questions.ApplicationPolicy:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    try:
        policy = questions.application_policy_from(raw)
    except questions.PolicyError as exc:
        raise DeskError(422, "application_policy_invalid", f"{exc.field}: {exc.message}") from exc
    async with store._session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if row is None:
            session.add(
                OnboardingPolicy(
                    tenant_id=tenant_id, settings={"application": policy.to_dict()}, updated_by=actor[:128]
                )
            )
        else:
            row.settings = {**(row.settings or {}), "application": policy.to_dict()}
            row.updated_by = actor[:128]
            row.updated_at = datetime.now(UTC)
    return policy


# ── Opening and reading ──────────────────────────────────────────────────────


async def open_for_lead(tenant_id: uuid.UUID, lead_id: uuid.UUID, *, now: datetime | None = None) -> Any:
    """The lead's application, created on first use; refused for a lead that opted out or was closed."""
    from sqlalchemy import select, update
    from sqlalchemy.dialects.postgresql import insert

    from core.models.onboarding_desk import OnboardingApplication, OnboardingLead

    moment = now or datetime.now(UTC)
    lead, _ = await store.get_lead(tenant_id, lead_id)
    if lead.status in CLOSED_LEAD_STATUSES:
        raise DeskError(410, "lead_closed", "this application is no longer open")
    query = select(OnboardingApplication).where(
        OnboardingApplication.tenant_id == tenant_id, OnboardingApplication.lead_id == lead_id
    )
    async with store._session(tenant_id) as session:
        row = (await session.execute(query)).scalar_one_or_none()
        if row is None:
            await session.execute(
                insert(OnboardingApplication)
                .values(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    lead_id=lead_id,
                    status="in_progress",
                    merchant_type=lead.merchant_type,
                    country=lead.country,
                    region=lead.region,
                    locale=lead.locale,
                    answered_items=[],
                    created_at=moment,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "lead_id"])
            )
            row = (await session.execute(query)).scalar_one_or_none()
            await session.execute(
                update(OnboardingLead)
                .where(
                    OnboardingLead.tenant_id == tenant_id,
                    OnboardingLead.id == lead_id,
                    OnboardingLead.status.in_(("new", "contacted", "engaged")),
                )
                .values(status="applying", updated_at=moment)
            )
    if row is None:
        raise DeskError(409, "application_unavailable", "the application could not be opened; try again")
    return row


async def get_application(tenant_id: uuid.UUID, application_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingApplication

    async with store._session(tenant_id) as session:
        row = (
            await session.execute(
                select(OnboardingApplication).where(
                    OnboardingApplication.tenant_id == tenant_id, OnboardingApplication.id == application_id
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise DeskError(404, "application_not_found", "no such application")
    return row


async def _locked(session: Any, tenant_id: uuid.UUID, application_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingApplication

    row = (
        await session.execute(
            select(OnboardingApplication)
            .where(OnboardingApplication.tenant_id == tenant_id, OnboardingApplication.id == application_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise DeskError(404, "application_not_found", "no such application")
    return row


def _require_open(row: Any) -> None:
    if row.status != "in_progress":
        raise DeskError(409, "application_closed", f"this application is {row.status}")


async def _documents(tenant_id: uuid.UUID, application_id: uuid.UUID) -> list[Any]:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingDocument

    async with store._session(tenant_id) as session:
        return list(
            (
                await session.execute(
                    select(OnboardingDocument)
                    .where(
                        OnboardingDocument.tenant_id == tenant_id, OnboardingDocument.application_id == application_id
                    )
                    .order_by(OnboardingDocument.created_at)
                )
            ).scalars()
        )


async def _open_handoff(tenant_id: uuid.UUID, application_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingHandoff

    async with store._session(tenant_id) as session:
        return (
            await session.execute(
                select(OnboardingHandoff).where(
                    OnboardingHandoff.tenant_id == tenant_id,
                    OnboardingHandoff.application_id == application_id,
                    OnboardingHandoff.status == "open",
                )
            )
        ).scalar_one_or_none()


def document_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "item": row.item,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "size_bytes": int(row.size_bytes),
        "extraction_status": row.extraction_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def state(tenant_id: uuid.UUID, row: Any, *, operator: bool = False) -> dict[str, Any]:
    """The application as the applicant sees it; ``operator`` adds the transcript and the case application."""
    policy = await get_application_policy(tenant_id)
    question_set = policy.for_type(row.merchant_type)
    answers = await _answers(row)
    docs = await _documents(tenant_id, row.id)
    handoff = await _open_handoff(tenant_id, row.id)
    lang = "fr" if row.locale == "fr" else "en"
    items = questions.view(question_set, lang)
    for item in items:
        if item["kind"] == "document":
            item["documents"] = [document_view(d) for d in docs if d.item == item["id"]]
        else:
            item["answer"] = answers["values"].get(item["id"])
            item["source"] = (answers["sources"].get(item["id"]) or {}).get("source")
    missing = questions.missing(question_set, answers["values"], {d.item for d in docs})
    out: dict[str, Any] = {
        "id": str(row.id),
        "status": row.status,
        "locale": lang,
        "country": row.country,
        "region": row.region,
        "merchant_type": row.merchant_type,
        "items": items,
        "missing": missing,
        "ready": not missing and row.status == "in_progress",
        "person_requested": handoff is not None,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
    }
    if operator:
        out["lead_id"] = str(row.lead_id)
        out["transcript"] = await _transcript(row)
        out["case_application"] = (
            questions.business_case_application(answers["values"], country=row.country, region=row.region)
            if not missing
            else None
        )
    return out


# ── Writing ──────────────────────────────────────────────────────────────────


async def save_answers(
    tenant_id: uuid.UUID,
    application_id: uuid.UUID,
    raw: Any,
    *,
    source: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Validate and save answers; returns the error code for each answer that was refused.

    ``None`` clears an answer. An item the partner's policy does not list for this
    application is refused, never stored.
    """
    if not isinstance(raw, dict) or not raw or len(raw) > MAX_ANSWERS_PER_CALL:
        raise DeskError(422, "answers_invalid", f"give between 1 and {MAX_ANSWERS_PER_CALL} answers")
    moment = now or datetime.now(UTC)
    current = await get_application(tenant_id, application_id)
    question_set = (await get_application_policy(tenant_id)).for_type(current.merchant_type)
    kek = await _kek(tenant_id)
    errors: dict[str, str] = {}
    async with store._session(tenant_id) as session:
        row = await _locked(session, tenant_id, application_id)
        _require_open(row)
        answers = await _answers(row)
        for item_id, value in raw.items():
            if item_id not in question_set.items():
                errors[str(item_id)[:32]] = "item_not_requested"
                continue
            if value is None:
                answers["values"].pop(item_id, None)
                answers["sources"].pop(item_id, None)
                continue
            try:
                normalised = questions.validate_answer(item_id, value, country=row.country, today=moment.date())
            except questions.AnswerError as exc:
                errors[item_id] = exc.code
                continue
            answers["values"][item_id] = normalised
            answers["sources"][item_id] = {"source": source, "at": moment.isoformat()}
        row.answers_ciphertext = await _seal(kek, json.dumps(answers, separators=(",", ":")))
        documents_answered = {i for i in (row.answered_items or []) if questions.ITEMS[i].kind == "document"}
        row.answered_items = sorted(set(answers["values"]) | documents_answered)
        if row.pending_item in answers["values"]:
            row.pending_item = None
        row.last_channel = _channel_of(source)
        row.updated_at = moment
    return errors


async def add_document(
    tenant_id: uuid.UUID,
    application_id: uuid.UUID,
    *,
    item: str,
    filename: str | None,
    content: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Store an uploaded document for a document item and return it with the values it suggests."""
    from sqlalchemy import func, select

    from core.models.onboarding_desk import OnboardingDocument

    moment = now or datetime.now(UTC)
    try:
        mime = documents.sniff(content)
    except documents.DocumentRefusedError as exc:
        raise DeskError(exc.status, exc.code, exc.message) from exc
    current = await get_application(tenant_id, application_id)
    _require_open(current)
    question_set = (await get_application_policy(tenant_id)).for_type(current.merchant_type)
    if item not in question_set.items() or questions.ITEMS[item].kind != "document":
        raise DeskError(422, "item_not_requested", "this application does not ask for that document")
    async with store._session(tenant_id) as session:
        count = (
            await session.execute(
                select(func.count()).where(
                    OnboardingDocument.tenant_id == tenant_id, OnboardingDocument.application_id == application_id
                )
            )
        ).scalar() or 0
    if count >= documents.MAX_PER_APPLICATION:
        raise DeskError(409, "too_many_documents", f"at most {documents.MAX_PER_APPLICATION} documents per application")
    answerable = tuple(i for i in question_set.items() if questions.ITEMS[i].kind != "document")
    extraction = await documents.extract(content, mime, items=answerable, country=current.country)
    kek = await _kek(tenant_id)
    content_ciphertext = await _seal(kek, base64.b64encode(content).decode("ascii"))
    suggestions_ciphertext = (
        await _seal(kek, json.dumps(extraction.suggestions, separators=(",", ":"))) if extraction.suggestions else None
    )
    doc = OnboardingDocument(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        application_id=application_id,
        item=item,
        filename=documents.safe_filename(filename, mime),
        mime_type=mime,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content_ciphertext=content_ciphertext,
        extraction_status=extraction.status,
        suggestions_ciphertext=suggestions_ciphertext,
        created_at=moment,
    )
    async with store._session(tenant_id) as session:
        row = await _locked(session, tenant_id, application_id)
        _require_open(row)
        session.add(doc)
        row.answered_items = sorted(set(row.answered_items or []) | {item})
        row.last_channel = "web"
        row.updated_at = moment
    return {**document_view(doc), "suggestions": extraction.suggestions}


async def _document(tenant_id: uuid.UUID, application_id: uuid.UUID, document_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingDocument

    async with store._session(tenant_id) as session:
        doc = (
            await session.execute(
                select(OnboardingDocument).where(
                    OnboardingDocument.tenant_id == tenant_id,
                    OnboardingDocument.application_id == application_id,
                    OnboardingDocument.id == document_id,
                )
            )
        ).scalar_one_or_none()
    if doc is None:
        raise DeskError(404, "document_not_found", "no such document")
    return doc


async def confirm_suggestions(
    tenant_id: uuid.UUID,
    application_id: uuid.UUID,
    document_id: uuid.UUID,
    items: list[str],
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """Save the suggested values the applicant confirmed, recording the document as their source."""
    doc = await _document(tenant_id, application_id, document_id)
    raw = await _open(doc.suggestions_ciphertext)
    suggested = {s["item"]: s["value"] for s in (json.loads(raw) if raw else [])}
    unknown = [i for i in items if i not in suggested]
    if not items or unknown:
        raise DeskError(422, "suggestion_not_found", "confirm only values this document suggested")
    return await save_answers(
        tenant_id, application_id, {i: suggested[i] for i in items}, source=f"document:{doc.id}", now=now
    )


async def remove_document(
    tenant_id: uuid.UUID, application_id: uuid.UUID, document_id: uuid.UUID, *, now: datetime | None = None
) -> None:
    """Remove an applicant's own upload before the application is submitted."""
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingDocument

    moment = now or datetime.now(UTC)
    async with store._session(tenant_id) as session:
        row = await _locked(session, tenant_id, application_id)
        _require_open(row)
        docs = list(
            (
                await session.execute(
                    select(OnboardingDocument).where(
                        OnboardingDocument.tenant_id == tenant_id, OnboardingDocument.application_id == application_id
                    )
                )
            ).scalars()
        )
        target = next((d for d in docs if d.id == document_id), None)
        if target is None:
            raise DeskError(404, "document_not_found", "no such document")
        await session.delete(target)
        kept = {d.item for d in docs if d.id != document_id}
        answered = {i for i in (row.answered_items or []) if questions.ITEMS[i].kind != "document"}
        row.answered_items = sorted(answered | kept)
        row.updated_at = moment


async def append_transcript(
    tenant_id: uuid.UUID, application_id: uuid.UUID, entries: list[dict[str, Any]], *, now: datetime | None = None
) -> None:
    """Add messages to the encrypted transcript; the oldest are dropped past the limit."""
    moment = now or datetime.now(UTC)
    kek = await _kek(tenant_id)
    async with store._session(tenant_id) as session:
        row = await _locked(session, tenant_id, application_id)
        transcript = await _transcript(row)
        for entry in entries:
            transcript.append(
                {
                    "at": moment.isoformat(),
                    "channel": str(entry.get("channel") or "web")[:16],
                    "from": "applicant" if entry.get("from") == "applicant" else "desk",
                    "text": str(entry.get("text") or "")[:MAX_TRANSCRIPT_TEXT],
                }
            )
        row.transcript_ciphertext = await _seal(kek, json.dumps(transcript[-MAX_TRANSCRIPT:], separators=(",", ":")))


async def request_person(
    tenant_id: uuid.UUID,
    application_id: uuid.UUID,
    *,
    channel: str,
    message: str = "",
    now: datetime | None = None,
) -> Any:
    """Ask for a person: one open hand-off per application, carrying the applicant's message."""
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert

    from core.models.onboarding_desk import OnboardingHandoff

    moment = now or datetime.now(UTC)
    row = await get_application(tenant_id, application_id)
    note = " ".join(str(message or "").split())[:MAX_MESSAGE]
    kek = await _kek(tenant_id)
    sealed = await _seal(kek, note) if note else None
    async with store._session(tenant_id) as session:
        await session.execute(
            insert(OnboardingHandoff)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                application_id=application_id,
                lead_id=row.lead_id,
                channel=channel[:16],
                status="open",
                message_ciphertext=sealed,
                created_at=moment,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "application_id"], index_where=text("status = 'open'"))
        )
    await append_transcript(
        tenant_id,
        application_id,
        [
            {
                "channel": channel,
                "from": "applicant",
                "text": "[asked for a person] " + note if note else "[asked for a person]",
            }
        ],
        now=moment,
    )
    handoff = await _open_handoff(tenant_id, application_id)
    logger.info("onboarding_handoff_requested", channel=channel)
    return handoff


async def submit(tenant_id: uuid.UUID, application_id: uuid.UUID, *, now: datetime | None = None) -> Any:
    """Submit a complete application; the lead stops getting reminders."""
    from sqlalchemy import update

    from core.models.onboarding_desk import OnboardingLead

    moment = now or datetime.now(UTC)
    current = await get_application(tenant_id, application_id)
    question_set = (await get_application_policy(tenant_id)).for_type(current.merchant_type)
    docs = {d.item for d in await _documents(tenant_id, application_id)}
    async with store._session(tenant_id) as session:
        row = await _locked(session, tenant_id, application_id)
        _require_open(row)
        missing = questions.missing(question_set, (await _answers(row))["values"], docs)
        if missing:
            raise DeskError(422, "application_incomplete", "required items are missing: " + ", ".join(missing))
        row.status = "submitted"
        row.submitted_at = moment
        row.pending_item = None
        row.updated_at = moment
        await session.execute(
            update(OnboardingLead)
            .where(OnboardingLead.tenant_id == tenant_id, OnboardingLead.id == row.lead_id)
            .values(status="submitted", next_contact_at=None, updated_at=moment)
        )
    logger.info("onboarding_application_submitted")
    return row


async def set_pending_item(tenant_id: uuid.UUID, application_id: uuid.UUID, item: str | None) -> None:
    """The item the text conversation last asked for, so the next reply answers it."""
    async with store._session(tenant_id) as session:
        row = await _locked(session, tenant_id, application_id)
        row.pending_item = item


# ── Operators ────────────────────────────────────────────────────────────────


def summary_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "lead_id": str(row.lead_id),
        "status": row.status,
        "merchant_type": row.merchant_type,
        "country": row.country,
        "region": row.region,
        "answered_items": list(row.answered_items or []),
        "last_channel": row.last_channel,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "case_ref": row.case_ref,
        "case_error": row.case_error,
    }


async def list_applications(tenant_id: uuid.UUID, *, status: str | None = None, limit: int = 50) -> list[Any]:
    from sqlalchemy import select

    from core.models.onboarding_desk import APPLICATION_STATUSES, OnboardingApplication

    query = select(OnboardingApplication).where(OnboardingApplication.tenant_id == tenant_id)
    if status:
        if status not in APPLICATION_STATUSES:
            raise DeskError(422, "status_invalid", f"status must be one of {list(APPLICATION_STATUSES)}")
        query = query.where(OnboardingApplication.status == status)
    query = query.order_by(OnboardingApplication.created_at.desc()).limit(max(1, min(limit, 200)))
    async with store._session(tenant_id) as session:
        return list((await session.execute(query)).scalars())


async def document_content(
    tenant_id: uuid.UUID, application_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[str, str, bytes]:
    doc = await _document(tenant_id, application_id, document_id)
    raw = await _open(doc.content_ciphertext)
    return doc.filename, doc.mime_type, base64.b64decode(raw or "")


async def handoff_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "application_id": str(row.application_id),
        "lead_id": str(row.lead_id),
        "channel": row.channel,
        "status": row.status,
        "message": await _open(row.message_ciphertext),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolved_by": row.resolved_by,
        "resolution_note": row.resolution_note,
    }


async def list_handoffs(tenant_id: uuid.UUID, *, status: str = "open", limit: int = 50) -> list[Any]:
    from sqlalchemy import select

    from core.models.onboarding_desk import HANDOFF_STATUSES, OnboardingHandoff

    if status not in HANDOFF_STATUSES:
        raise DeskError(422, "status_invalid", f"status must be one of {list(HANDOFF_STATUSES)}")
    async with store._session(tenant_id) as session:
        return list(
            (
                await session.execute(
                    select(OnboardingHandoff)
                    .where(OnboardingHandoff.tenant_id == tenant_id, OnboardingHandoff.status == status)
                    .order_by(OnboardingHandoff.created_at.desc())
                    .limit(max(1, min(limit, 200)))
                )
            ).scalars()
        )


async def resolve_handoff(
    tenant_id: uuid.UUID, handoff_id: uuid.UUID, *, actor: str, note: str = "", now: datetime | None = None
) -> Any:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingHandoff

    async with store._session(tenant_id) as session:
        row = (
            await session.execute(
                select(OnboardingHandoff)
                .where(OnboardingHandoff.tenant_id == tenant_id, OnboardingHandoff.id == handoff_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise DeskError(404, "handoff_not_found", "no such hand-off")
        if row.status == "open":
            row.status = "resolved"
            row.resolved_at = now or datetime.now(UTC)
            row.resolved_by = actor[:128]
            row.resolution_note = " ".join(note.split())[:500] or None
    return row
