# SPDX-License-Identifier: Apache-2.0
"""Evaluation datasets: versioned reference cases a tenant's administrators keep and run."""

from __future__ import annotations

import uuid as _uuid
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.evals import datasets
from core.pii.pseudonymiser import PseudonymisationError
from core.prompts import compare as prompt_compare
from core.schemas.api import EvalDatasetCreate, EvalDatasetRunIn, EvalDatasetVersionCreate

logger = structlog.get_logger()

router = APIRouter()


def _require_enabled() -> None:
    if not datasets.enabled():
        raise HTTPException(409, "Evaluation datasets are off in this deployment")


def _refused(exc: datasets.DatasetError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _actor(user: dict | None) -> _uuid.UUID | None:
    """The caller's local user id; an API key or a token without one records no author."""
    if not isinstance(user, dict):
        return None
    for key in ("agenticorg:user_id", "user_id"):
        raw = user.get(key)
        if raw:
            try:
                return _uuid.UUID(str(raw))
            except (TypeError, ValueError):
                continue
    return None


@router.get("/eval-datasets", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="evals.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="evals.datasets.list",
)
async def list_eval_datasets(include_archived: bool = False, tenant_id: str = Depends(get_current_tenant)) -> dict:
    """The tenant's evaluation datasets, without their cases."""
    limits = {
        "datasets": datasets.MAX_DATASETS,
        "cases": datasets.MAX_CASES,
        "cases_per_run": prompt_compare.MAX_CASES,
    }
    if not datasets.enabled():
        return {"enabled": False, "datasets": [], "limits": limits}
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        rows = await datasets.list_datasets(session, tid, include_archived=include_archived)
        return {"enabled": True, "datasets": [datasets.dataset_dict(row) for row in rows], "limits": limits}


@router.post("/eval-datasets", status_code=201, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="evals.write",
    rate_limit="standard",
    idempotency="not-idempotent-name-is-unique",
    audit_event="evals.datasets.create",
)
async def create_eval_dataset(
    body: EvalDatasetCreate,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> dict:
    """Create a dataset with its first version."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            dataset, version = await datasets.create(
                session,
                tid,
                name=body.name,
                description=body.description,
                cases=body.cases,
                note=body.note,
                actor=_actor(user),
            )
            return {**datasets.dataset_dict(dataset), "version": datasets.version_dict(version)}
    except datasets.DatasetError as exc:
        raise _refused(exc) from None


@router.get("/eval-datasets/{dataset_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="evals.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="evals.datasets.read",
)
async def get_eval_dataset(dataset_id: UUID, tenant_id: str = Depends(get_current_tenant)) -> dict:
    """A dataset and the list of its versions, newest first, without their cases."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            dataset = await datasets.get_dataset(session, tid, dataset_id)
            versions = await datasets.list_versions(session, tid, dataset_id)
            return {
                **datasets.dataset_dict(dataset),
                "versions": [datasets.version_dict(version) for version in versions],
            }
    except datasets.DatasetError as exc:
        raise _refused(exc) from None


@router.get("/eval-datasets/{dataset_id}/versions/{version}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="evals.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="evals.datasets.version.read",
)
async def get_eval_dataset_version(
    dataset_id: UUID, version: int, tenant_id: str = Depends(get_current_tenant)
) -> dict:
    """One version of a dataset, with its cases."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            found = await datasets.get_version(session, tid, dataset_id, version)
            return datasets.version_dict(found, with_cases=True)
    except datasets.DatasetError as exc:
        raise _refused(exc) from None


@router.post("/eval-datasets/{dataset_id}/versions", status_code=201, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="evals.write",
    rate_limit="standard",
    idempotency="not-idempotent-identical-cases-are-refused",
    audit_event="evals.datasets.version.create",
)
async def add_eval_dataset_version(
    dataset_id: UUID,
    body: EvalDatasetVersionCreate,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> dict:
    """Save the cases as the dataset's next version. Earlier versions are not changed."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            dataset, version = await datasets.add_version(
                session,
                tid,
                dataset_id,
                cases=body.cases,
                note=body.note,
                actor=_actor(user),
                expected_latest=body.expected_latest,
            )
            return {**datasets.dataset_dict(dataset), "version": datasets.version_dict(version)}
    except datasets.DatasetError as exc:
        raise _refused(exc) from None


@router.delete("/eval-datasets/{dataset_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="evals.write",
    rate_limit="standard",
    idempotency="idempotent-archive",
    audit_event="evals.datasets.archive",
)
async def archive_eval_dataset(dataset_id: UUID, tenant_id: str = Depends(get_current_tenant)) -> dict:
    """Archive a dataset. Its versions stay readable; its name becomes free."""
    _require_enabled()
    tid = _uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            return datasets.dataset_dict(await datasets.archive(session, tid, dataset_id))
    except datasets.DatasetError as exc:
        raise _refused(exc) from None


@router.post("/eval-datasets/{dataset_id}/run", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="evals.write",
    rate_limit="prompt-compare",
    idempotency="not-idempotent-makes-billed-model-calls",
    audit_event="evals.datasets.run",
)
async def run_eval_dataset(
    dataset_id: UUID, body: EvalDatasetRunIn, tenant_id: str = Depends(get_current_tenant)
) -> dict:
    """Score a prompt with one model against a version of a dataset.

    Makes one billed model call per case, at most 25 cases a request; a larger
    version is run in slices with ``offset``. The response names the version,
    its content hash and the slice, and per case whether it passed, which
    expectations it failed or the error type; never an answer. Nothing is
    stored.
    """
    _require_enabled()
    if not prompt_compare.enabled():
        raise HTTPException(409, "Prompt evaluation is off in this deployment")
    tid = _uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            version = await datasets.get_version(session, tid, dataset_id, body.version)
            described = datasets.version_dict(version, with_cases=True)
    except datasets.DatasetError as exc:
        raise _refused(exc) from None
    selected = described["cases"][body.offset : body.offset + body.limit]
    if not selected:
        raise HTTPException(422, f"offset {body.offset} is past the version's {described['case_count']} cases")
    try:
        report = await prompt_compare.evaluate(
            tid,
            variants=[("prompt", body.system)],
            cases=prompt_compare.parse_cases(selected),
            model=body.model,
            max_tokens=body.max_tokens,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except PseudonymisationError as exc:
        logger.error("eval_dataset_run_pseudonymisation_unavailable", reason=str(exc))
        raise HTTPException(503, "Pseudonymisation could not be applied; no model was called") from None
    [result] = report["variants"]
    result.pop("name", None)
    return {
        "dataset_id": described["dataset_id"],
        "version": described["version"],
        "content_hash": described["content_hash"],
        "cases_total": described["case_count"],
        "offset": body.offset,
        "cases_run": len(selected),
        "complete": body.offset == 0 and len(selected) == described["case_count"],
        "model": report["model"],
        "max_tokens": report["max_tokens"],
        **result,
    }
