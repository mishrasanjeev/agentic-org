#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Sample governed cases for the local development stack (``make seed-cases``).

Needs ``make dev`` and ``make seed``. For the seeded development tenant it:

* turns the ``governed_cases.enabled`` flag on (idempotent);
* submits one new case per mock provider fixture (default: a clean case, a missing-owner case, a
  probable false-positive screening hit, a true match and a thin file with no registry match);
* runs the Business Onboarding Underwriter on each against the mock provider service, then the
  Screening Disposition agent on every hit, exactly as the workflows do;
* writes a JSON summary with the new case references (``--output``, default standard output), which
  the browser suite reads.

Every run submits new cases, so a run never depends on what an earlier run or test did to its cases.
It refuses to run outside a development or test runtime. Nothing here approves, declines or reviews
anything: the cases stop at ``awaiting_decision`` with proposed dispositions for a human.

    make seed-cases    # python -m scripts.seed_governed_cases --output ui/test-results/governed-cases-seed.json

The agents' prose comes from the model stub (``--llm-model``, default the stub's scripted model), so no
model credentials are needed; the deterministic documents are complete without prose.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.seed_dev import SAMPLE_AGENT_MODEL, SeedError, assert_development_runtime, seed_id

FLAG_KEY = "governed_cases.enabled"
SEED_ACTOR = "seed:governed_cases"
DEFAULT_FIXTURES: tuple[str, ...] = (
    "gb-clean-brightwater",
    "gb-missing-owner-marlpit",
    "us-false-positive-oakhollow",
    "gb-true-match-corvane",
    "us-thin-file-brambleway",
)


def parse_fixtures(value: str) -> tuple[str, ...]:
    keys = tuple(key.strip() for key in value.split(",") if key.strip())
    if not keys:
        raise SeedError("--fixtures names no fixture")
    return keys


def load_applications(keys: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The fixture applications, failing before any write when a key is unknown."""
    from connectors.providers.mock.data import default_dataset  # noqa: PLC0415

    dataset = default_dataset()
    applications: dict[str, dict[str, Any]] = {}
    for key in keys:
        try:
            applications[key] = dict(dataset.business(key).application)
        except KeyError as exc:
            raise SeedError(f"unknown mock fixture {key!r}") from exc
    return applications


async def enable_flag(session: Any, tenant_id: uuid.UUID) -> None:
    from sqlalchemy import select  # noqa: PLC0415

    from core.models.feature_flag import FeatureFlag  # noqa: PLC0415

    row = (
        await session.execute(
            select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id, FeatureFlag.flag_key == FLAG_KEY)
        )
    ).scalar_one_or_none()
    if row is None:
        row = FeatureFlag(id=seed_id(f"flag:{FLAG_KEY}"), tenant_id=tenant_id, flag_key=FLAG_KEY)
        session.add(row)
    row.enabled, row.rollout_percentage = True, 100
    row.description = "Enabled by scripts/seed_governed_cases.py for the development tenant."
    await session.flush()


async def seed_cases(keys: Sequence[str], *, runtime: Any = None, llm_model: str = SAMPLE_AGENT_MODEL) -> dict[str, Any]:
    from core.cases.runtime import CaseRuntime, default_policy_id, dispose_screening_hits, investigate_case  # noqa: PLC0415
    from core.cases.states import CaseError  # noqa: PLC0415
    from core.cases.store import create_case  # noqa: PLC0415
    from core.config import settings  # noqa: PLC0415

    applications = load_applications(keys)
    runtime = runtime or CaseRuntime(llm_model=llm_model)
    tenant_id = seed_id("tenant")
    async with runtime.session_factory(tenant_id) as session:
        await enable_flag(session, tenant_id)

    summary: dict[str, Any] = {"tenant_id": str(tenant_id), "cases": {}}
    for key, application in applications.items():
        async with runtime.session_factory(tenant_id) as session:
            case = await create_case(
                session,
                tenant_id=tenant_id,
                application=application,
                purpose="aml.cdd.onboarding",
                provider=settings.case_provider,
                policy_id=default_policy_id(str(application.get("jurisdiction") or "")),
                created_by=SEED_ACTOR,
                now=runtime.clock(),
            )
            case_ref = case.case_ref
        investigation = await investigate_case(tenant_id, case_ref, runtime=runtime, actor=SEED_ACTOR)
        entry: dict[str, Any] = {"case_ref": case_ref, "legal_name": application.get("legal_name"), **investigation}
        if investigation.get("state") == "awaiting_decision" and investigation.get("screening_hits"):
            try:
                disposed = await dispose_screening_hits(tenant_id, case_ref, runtime=runtime, actor=SEED_ACTOR)
            except CaseError as exc:
                entry["dispositions_refused"] = exc.reason
            else:
                entry["dispositions_proposed"] = disposed["proposed"]
                entry["disposition_outcomes"] = disposed["outcomes"]
        summary["cases"][key] = entry
    return summary


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description="Submit and investigate sample governed cases (development only).")
    parser.add_argument("--fixtures", default=",".join(DEFAULT_FIXTURES), help="comma-separated mock fixture keys")
    parser.add_argument("--llm-model", default=SAMPLE_AGENT_MODEL, help="model for the agents' prose")
    parser.add_argument("--output", default="", help="write the JSON summary here instead of standard output")
    args = parser.parse_args(argv)
    try:
        assert_development_runtime(environ)
        summary = asyncio.run(seed_cases(parse_fixtures(args.fixtures), llm_model=args.llm_model))
    except SeedError as exc:
        print(f"seed_governed_cases: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(summary, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{text}\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
