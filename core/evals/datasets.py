# SPDX-License-Identifier: Apache-2.0
"""Evaluation datasets: named, versioned sets of reference cases a tenant keeps.

A dataset is a name and a sequence of versions. A version is a list of cases
in the form prompt evaluation already scores (``core/prompts/compare.py``): an
input and at least one expectation (``contains``, ``not_contains``, ``equals``,
``matches``). A version is never changed: a change to the cases is a new
version, so a result can always name exactly the cases it was measured on. A
version carries the SHA-256 of its canonical cases; saving cases identical to
the latest version is refused rather than stored as a new number.

A dataset is archived, not deleted: its versions stay readable by id and its
name becomes free for a new dataset.

Cases are the tenant's own reference data and may hold business content. They
are stored under row-level security, returned only to the tenant's
administrators, and never logged or put in metrics.

Behind ``AGENTICORG_EVALS_V2_ENABLED`` (off by default): off, the endpoints
answer 409 and nothing is stored.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.config import settings
from core.models.eval_dataset import EvalDataset, EvalDatasetVersion
from core.prompts import compare as prompt_compare

logger = structlog.get_logger()

MAX_DATASETS = 200
MAX_CASES = 200
MAX_VERSION_BYTES = 1_000_000
MAX_NAME = 120
MAX_DESCRIPTION = 500
MAX_NOTE = 500


class DatasetError(Exception):
    """A dataset request that cannot be carried out; ``status`` is the HTTP status it maps to."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(settings.evals_v2_enabled)


def _text(value: Any, label: str, limit: int, *, required: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise DatasetError(422, "invalid", f"{label} is required")
        return None
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise DatasetError(422, "invalid", f"{label} is text of at most {limit} characters")
    return value.strip()


def _case_dict(case: prompt_compare.Case) -> dict[str, Any]:
    out: dict[str, Any] = {"id": case.id, "input": case.input}
    if case.contains:
        out["contains"] = list(case.contains)
    if case.not_contains:
        out["not_contains"] = list(case.not_contains)
    if case.equals is not None:
        out["equals"] = case.equals
    if case.matches:
        out["matches"] = case.matches
    return out


def normalise_cases(raw: Any) -> tuple[list[dict[str, Any]], str]:
    """The cases as they are stored, and their content hash.

    The rules are the ones evaluation applies when it scores (an input, at
    least one expectation, bounded text, a bounded pattern, distinct ids), so
    a stored version can always be run.
    """
    try:
        cases = [_case_dict(case) for case in prompt_compare.parse_cases(raw, limit=MAX_CASES)]
    except ValueError as exc:
        raise DatasetError(422, "invalid_cases", str(exc)) from None
    canonical = json.dumps(cases, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(canonical.encode("utf-8")) > MAX_VERSION_BYTES:
        raise DatasetError(422, "invalid_cases", f"a version holds at most {MAX_VERSION_BYTES} bytes of cases")
    return cases, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def dataset_dict(dataset: EvalDataset) -> dict[str, Any]:
    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "description": dataset.description,
        "latest_version": dataset.latest_version,
        "case_count": dataset.case_count,
        "created_by_user": str(dataset.created_by_user) if dataset.created_by_user else None,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "updated_at": dataset.updated_at.isoformat() if dataset.updated_at else None,
        "archived_at": dataset.archived_at.isoformat() if dataset.archived_at else None,
    }


def version_dict(version: EvalDatasetVersion, *, with_cases: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(version.id),
        "dataset_id": str(version.dataset_id),
        "version": version.version,
        "case_count": version.case_count,
        "content_hash": version.content_hash,
        "note": version.note,
        "created_by_user": str(version.created_by_user) if version.created_by_user else None,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }
    if with_cases:
        out["cases"] = list(version.cases or [])
    return out


async def _dataset(session: Any, tenant_id: uuid.UUID, dataset_id: uuid.UUID, *, lock: bool = False) -> EvalDataset:
    statement = select(EvalDataset).where(EvalDataset.id == dataset_id, EvalDataset.tenant_id == tenant_id)
    if lock:
        statement = statement.with_for_update()
    dataset = (await session.execute(statement)).scalar_one_or_none()
    if dataset is None:
        raise DatasetError(404, "not_found", "Evaluation dataset not found")
    return dataset


async def list_datasets(session: Any, tenant_id: uuid.UUID, *, include_archived: bool = False) -> list[EvalDataset]:
    statement = select(EvalDataset).where(EvalDataset.tenant_id == tenant_id)
    if not include_archived:
        statement = statement.where(EvalDataset.archived_at.is_(None))
    statement = statement.order_by(func.lower(EvalDataset.name), EvalDataset.created_at)
    return list((await session.execute(statement)).scalars().all())


async def get_dataset(session: Any, tenant_id: uuid.UUID, dataset_id: uuid.UUID) -> EvalDataset:
    return await _dataset(session, tenant_id, dataset_id)


async def list_versions(session: Any, tenant_id: uuid.UUID, dataset_id: uuid.UUID) -> list[EvalDatasetVersion]:
    await _dataset(session, tenant_id, dataset_id)
    statement = (
        select(EvalDatasetVersion)
        .where(EvalDatasetVersion.dataset_id == dataset_id, EvalDatasetVersion.tenant_id == tenant_id)
        .order_by(EvalDatasetVersion.version.desc())
    )
    return list((await session.execute(statement)).scalars().all())


async def get_version(
    session: Any, tenant_id: uuid.UUID, dataset_id: uuid.UUID, version: int | None = None
) -> EvalDatasetVersion:
    """One version of a dataset; the latest when ``version`` is not given."""
    dataset = await _dataset(session, tenant_id, dataset_id)
    number = dataset.latest_version if version is None else version
    found = (
        await session.execute(
            select(EvalDatasetVersion).where(
                EvalDatasetVersion.dataset_id == dataset_id,
                EvalDatasetVersion.tenant_id == tenant_id,
                EvalDatasetVersion.version == number,
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise DatasetError(404, "version_not_found", "Evaluation dataset version not found")
    return found


def _new_version(
    dataset: EvalDataset, cases: list[dict[str, Any]], content_hash: str, note: str | None, actor: uuid.UUID | None
) -> EvalDatasetVersion:
    dataset.latest_version = int(dataset.latest_version or 0) + 1
    dataset.case_count = len(cases)
    dataset.updated_at = datetime.now(UTC)
    return EvalDatasetVersion(
        id=uuid.uuid4(),
        tenant_id=dataset.tenant_id,
        dataset_id=dataset.id,
        version=dataset.latest_version,
        cases=cases,
        case_count=len(cases),
        content_hash=content_hash,
        note=note,
        created_by_user=actor,
    )


async def create(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    name: Any,
    description: Any = None,
    cases: Any,
    note: Any = None,
    actor: uuid.UUID | None = None,
) -> tuple[EvalDataset, EvalDatasetVersion]:
    """A new dataset with its first version."""
    clean_name = _text(name, "name", MAX_NAME, required=True)
    clean_description = _text(description, "description", MAX_DESCRIPTION)
    clean_note = _text(note, "note", MAX_NOTE)
    stored, content_hash = normalise_cases(cases)
    count = await session.scalar(
        select(func.count()).select_from(EvalDataset).where(EvalDataset.tenant_id == tenant_id)
    )
    if int(count or 0) >= MAX_DATASETS:
        raise DatasetError(409, "too_many", f"a tenant keeps at most {MAX_DATASETS} evaluation datasets")
    taken = await session.scalar(
        select(EvalDataset.id).where(
            EvalDataset.tenant_id == tenant_id,
            func.lower(EvalDataset.name) == str(clean_name).lower(),
            EvalDataset.archived_at.is_(None),
        )
    )
    if taken is not None:
        raise DatasetError(409, "name_taken", "An evaluation dataset with this name already exists")
    dataset = EvalDataset(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=clean_name,
        description=clean_description,
        latest_version=0,
        case_count=0,
        created_by_user=actor,
    )
    version = _new_version(dataset, stored, content_hash, clean_note, actor)
    session.add(dataset)
    try:
        # The dataset row first: the version refers to it.
        await session.flush()
        session.add(version)
        await session.flush()
    except IntegrityError:
        # Two creations of the same name at once: the unique index decided.
        raise DatasetError(409, "name_taken", "An evaluation dataset with this name already exists") from None
    logger.info("eval_dataset_created", dataset_id=str(dataset.id), cases=len(stored))
    return dataset, version


async def add_version(
    session: Any,
    tenant_id: uuid.UUID,
    dataset_id: uuid.UUID,
    *,
    cases: Any,
    note: Any = None,
    actor: uuid.UUID | None = None,
    expected_latest: int | None = None,
) -> tuple[EvalDataset, EvalDatasetVersion]:
    """The next version of a dataset.

    The dataset row is locked, so two writers get consecutive numbers.
    ``expected_latest`` lets a writer who edited version N refuse to save over
    a version someone else added meanwhile.
    """
    clean_note = _text(note, "note", MAX_NOTE)
    stored, content_hash = normalise_cases(cases)
    dataset = await _dataset(session, tenant_id, dataset_id, lock=True)
    if dataset.archived_at is not None:
        raise DatasetError(409, "archived", "This evaluation dataset is archived")
    if expected_latest is not None and expected_latest != dataset.latest_version:
        raise DatasetError(
            409, "stale", f"The dataset is at version {dataset.latest_version}, not {expected_latest}; reload it"
        )
    latest_hash = await session.scalar(
        select(EvalDatasetVersion.content_hash).where(
            EvalDatasetVersion.dataset_id == dataset_id,
            EvalDatasetVersion.tenant_id == tenant_id,
            EvalDatasetVersion.version == dataset.latest_version,
        )
    )
    if latest_hash == content_hash:
        raise DatasetError(409, "unchanged", "These cases are identical to the latest version")
    version = _new_version(dataset, stored, content_hash, clean_note, actor)
    session.add(version)
    await session.flush()
    logger.info("eval_dataset_version_added", dataset_id=str(dataset.id), version=version.version, cases=len(stored))
    return dataset, version


async def archive(session: Any, tenant_id: uuid.UUID, dataset_id: uuid.UUID) -> EvalDataset:
    """Archive a dataset: it leaves the list and frees its name; its versions stay readable."""
    dataset = await _dataset(session, tenant_id, dataset_id, lock=True)
    if dataset.archived_at is None:
        dataset.archived_at = datetime.now(UTC)
        logger.info("eval_dataset_archived", dataset_id=str(dataset.id))
    return dataset
