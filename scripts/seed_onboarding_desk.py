#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The merchant onboarding desk for the local development stack (``make e2e-onboarding-desk``).

Two steps around the browser suite ``ui/e2e/onboarding-desk.spec.ts``, for the tenant ``make seed``
created:

``configure``
    Turns ``governed_cases.enabled`` on, sets the desk's branding (a fictional partner), opens a case
    when an application is submitted (``auto_open``) but leaves the checks to the next step, creates a
    webhook lead source and points the governed-case push at ``--push-url``. The lead source's signing
    secret and the push signing key are written to ``--output`` for the browser suite, which signs the
    webhook and checks the push with them; they are development values and never leave the stack.

``investigate``
    Runs the checks on the case the suite's application opened (``--run``, written by the suite),
    exactly as ``make seed-cases`` runs them: the case agents registered with the stack's Grantex
    service and a development root grant held in this process only. The worker has no root grant in
    the development stack, which is why the suite hands this step over rather than letting the desk
    investigate on submission.

Both refuse to run outside a development or test runtime or against any Grantex service but the
stack's own, as the governed-case seed does.

    python -m scripts.seed_onboarding_desk configure --output ui/test-results/onboarding-desk-seed.json \\
        --push-url http://grantex:3099/case-push
    python -m scripts.seed_onboarding_desk investigate --run ui/test-results/onboarding-desk-run.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.seed_dev import SAMPLE_AGENT_MODEL, SeedError, assert_development_runtime, seed_id
from scripts.seed_governed_cases import assert_local_grantex, enable_flag, prepare_case_agents

SEED_ACTOR = "seed:onboarding_desk"
SOURCE_NAME = "Development partner form"
BRANDING = {
    "partner_name": "Northwind Payments (development)",
    "onboarding_url": "http://localhost/onboarding-desk/apply",
    "postal_address": "100 Example Street, Toronto ON A0A 0A0",
    "brand_color": "#0f5c8c",
    "privacy_url": "https://northwind.example.com/privacy",
}
#: The suite submits, the desk opens the case, and ``investigate`` runs the checks.
CASE_SETTINGS = {"auto_open": True, "investigate": False}


def _tenant_session(tenant_id: uuid.UUID) -> Any:
    from core.database import get_tenant_session  # noqa: PLC0415

    return get_tenant_session(tenant_id)


async def _lead_source(tenant_id: uuid.UUID) -> tuple[str, str]:
    """A fresh webhook source, so its signing secret can be handed to the suite; earlier ones stay."""
    from core.onboarding_desk import store  # noqa: PLC0415

    row, secret = await store.create_source(tenant_id, SOURCE_NAME, "webhook", actor=SEED_ACTOR)
    if not secret:
        raise SeedError("the desk returned no signing secret for the webhook lead source")
    return str(row.id), secret


async def _push_endpoint(tenant_id: uuid.UUID, url: str) -> dict[str, str]:
    """Point the governed-case push at ``url`` and return a signing key the suite can check it with."""
    from core.cases.push import configure_endpoint, retire_previous_keys, rotate_signing_key  # noqa: PLC0415

    now = datetime.now(UTC)
    async with _tenant_session(tenant_id) as session:
        _, key = await configure_endpoint(session, tenant_id, url=url, enabled=True, now=now)
        if key is None:
            # The endpoint was configured by an earlier run, whose key was never kept: start a new
            # active key and retire the others, so every delivery is signed with one the suite holds.
            key = await rotate_signing_key(session, tenant_id, now=now)
            await retire_previous_keys(session, tenant_id, now=now)
    return {"key_id": key.key_id, "secret": key.secret}


async def configure(push_url: str) -> dict[str, Any]:
    from core.onboarding_desk import cases, store  # noqa: PLC0415

    if not store.enabled():
        raise SeedError("the onboarding desk is off: start the stack with AGENTICORG_DEV_ONBOARDING_DESK=true")
    tenant_id = seed_id("tenant")
    async with _tenant_session(tenant_id) as session:
        await enable_flag(session, tenant_id)
    await store.put_branding(tenant_id, dict(BRANDING), actor=SEED_ACTOR)
    await cases.put_case_settings(tenant_id, dict(CASE_SETTINGS), actor=SEED_ACTOR)
    source_id, secret = await _lead_source(tenant_id)
    return {
        "tenant_id": str(tenant_id),
        "partner_name": BRANDING["partner_name"],
        "lead_source": {
            "id": source_id,
            "webhook_path": f"/api/v1/onboarding-desk/webhooks/leads/{tenant_id}/{source_id}",
            "signing_secret": secret,
        },
        "push": {"url": push_url, **await _push_endpoint(tenant_id, push_url)},
    }


async def investigate(run: Mapping[str, Any]) -> dict[str, Any]:
    from core.cases.grant_authorizer import case_authorizer  # noqa: PLC0415
    from core.cases.runtime import CaseRuntime  # noqa: PLC0415
    from core.onboarding_desk import applications, cases  # noqa: PLC0415

    tenant_id = seed_id("tenant")
    if str(run.get("tenant_id")) != str(tenant_id):
        raise SeedError("the run file names another tenant than the seeded one")
    try:
        application_id = uuid.UUID(str(run.get("application_id")))
    except ValueError as exc:
        raise SeedError("the run file has no application_id") from exc
    row = await applications.get_application(tenant_id, application_id)
    if not row.case_ref:
        raise SeedError(f"the application has no case yet (case_error: {row.case_error or 'none'})")
    await prepare_case_agents(tenant_id)
    # The agents' prose comes from the model stub, as for the seeded cases.
    runtime = CaseRuntime(llm_model=SAMPLE_AGENT_MODEL, authorizer_factory=case_authorizer)
    result = await cases.investigate(tenant_id, row.case_ref, actor=SEED_ACTOR, runtime=runtime)
    case = await cases.get_case(tenant_id, row.case_ref)
    return {**result, "state": str(case.state), "tier": (case.policy_result or {}).get("tier")}


def _write(path: str, summary: Mapping[str, Any]) -> None:
    text = json.dumps(summary, indent=2, sort_keys=True)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{text}\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description="The onboarding desk for the development stack (development only).")
    steps = parser.add_subparsers(dest="step", required=True)
    configure_step = steps.add_parser("configure", help="branding, case settings, a lead source and the push")
    configure_step.add_argument("--output", required=True, help="where the suite reads the seeded values")
    configure_step.add_argument("--push-url", required=True, help="the governed-case push endpoint")
    investigate_step = steps.add_parser("investigate", help="run the checks on the suite's case")
    investigate_step.add_argument("--run", required=True, help="the run file the suite wrote")
    args = parser.parse_args(argv)
    try:
        assert_development_runtime(environ)
        if args.step == "configure":
            _write(args.output, asyncio.run(configure(args.push_url)))
            print(f"seed_onboarding_desk: configured, values in {args.output}")
            return 0
        assert_local_grantex(environ)
        run_path = Path(args.run)
        try:
            run = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SeedError(f"cannot read the run file {run_path}: {exc}") from exc
        result = asyncio.run(investigate(run))
        _write(args.run, {**run, "investigation": result})
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("state") == "awaiting_decision" else 1
    except SeedError as exc:
        print(f"seed_onboarding_desk: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
