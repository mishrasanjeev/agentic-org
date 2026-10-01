#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Demo 3: a governed enterprise case, end to end, against the dev stack (``make demo-case``).

An agent runs a business-onboarding case that calls the mock verification provider through the
tool gateway under a run grant delegated from a development root grant, with
``grants.enforce_closed=deny`` and the evidence sink on. The script prints, one line per step,
labelled ``live`` (the stack's Grantex service and this API's own runtime) or ``fixture`` (the
mock provider and the model stub):

* the root grant issued with its purpose and tools, and the run grant delegated from it;
* the case submitted and the underwriter run;
* every provider tool call, authorised one by one under the run grant;
* an out-of-scope tool call denied with the reason;
* the evidence records the run posted, read back from the service's export;
* the evidence package exported, its root and anchor read from the tenant audit log (not from
  the exporter), and verified with the ``grantex-evidence`` CLI against the service's JWK Set.

It needs ``make dev`` and ``make seed`` and refuses to run outside a development runtime or
against any Grantex service but the stack's own, as the governed-case seed does. Every run
submits a new case. Exit status 0 only when every step had its expected outcome.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from scripts.seed_dev import assert_development_runtime, seed_id
from scripts.seed_governed_cases import (
    CASE_PURPOSE,
    SAMPLE_AGENT_MODEL,
    SeedError,
    assert_local_grantex,
    enable_flag,
    load_applications,
    prepare_case_agents,
)

DEFAULT_FIXTURE = "gb-clean-brightwater"
DEMO_ACTOR = "demo:governed_case"
OUT_OF_SCOPE_ROLE = "screening_disposition"
OUT_OF_SCOPE_TOOL = "ownership"  # a read tool outside the screening role's grant


class DemoError(RuntimeError):
    pass


class Steps:
    def __init__(self) -> None:
        self.n = 0
        self.lines: list[dict[str, Any]] = []

    def __call__(self, title: str, source: str, detail: str = "") -> None:
        self.n += 1
        line = f"[{self.n:02d}] {title:<52} {source:<8} {detail}".rstrip()
        print(line, flush=True)
        self.lines.append({"step": self.n, "title": title, "source": source, "detail": detail})


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise DemoError(message)


async def run_demo(fixture: str, *, llm_model: str, environ: Mapping[str, str]) -> dict[str, Any]:
    # Imports after the runtime checks, as the seed does: they load settings and the ORM.
    from core.cases.evidence import GrantexEvidenceService, evidence_service, evidence_time  # noqa: PLC0415
    from core.cases.grant_authorizer import case_authorizer  # noqa: PLC0415
    from core.cases.runtime import CaseRuntime, default_policy_id, investigate_case  # noqa: PLC0415
    from core.cases.store import create_case, get_case  # noqa: PLC0415
    from core.config import external_keys, settings  # noqa: PLC0415

    step = Steps()
    expect(settings.grants_enforce_closed == "deny", "start with AGENTICORG_GRANTS_ENFORCE_CLOSED=deny")
    expect(settings.case_evidence_service == "grantex", "start with AGENTICORG_CASE_EVIDENCE_SERVICE=grantex")
    base_url = environ["GRANTEX_BASE_URL"].rstrip("/")
    api_key = environ["GRANTEX_API_KEY"]

    # ── the authority: a root grant, and the agents that will be delegated from it ─────────
    tenant_id = seed_id("tenant")
    agents = await prepare_case_agents(tenant_id)
    root_token = external_keys.grantex_root_grant_token
    expect(bool(root_token), "no development root grant was issued")
    root_claims = _jwt_claims(root_token)
    step(
        "root grant issued (purpose, tools, caps)",
        "live",
        f"grant={root_claims.get('grnt', '?')} purpose={root_claims.get('purpose', CASE_PURPOSE)} "
        f"scopes={len(str(root_claims.get('scope', '')).split())} exp={root_claims.get('exp')}",
    )
    for role, agent in sorted(agents.items()):
        detail = agent if isinstance(agent, str) else json.dumps(agent, sort_keys=True, default=str)
        step(f"case agent ready: {role}", "live", detail)

    # ── the case ──────────────────────────────────────────────────────────────────────────
    runtime = CaseRuntime(llm_model=llm_model, authorizer_factory=case_authorizer, evidence_service=evidence_service)
    async with runtime.session_factory(tenant_id) as session:
        await enable_flag(session, tenant_id)
    application = load_applications([fixture])[fixture]
    async with runtime.session_factory(tenant_id) as session:
        case = await create_case(
            session,
            tenant_id=tenant_id,
            application=application,
            purpose=CASE_PURPOSE,
            provider=settings.case_provider,
            policy_id=default_policy_id(str(application.get("jurisdiction") or "")),
            created_by=DEMO_ACTOR,
            now=runtime.clock(),
        )
        case_ref = case.case_ref
    step(
        "case submitted",
        "live",
        f"{case_ref} {application.get('legal_name')} provider={settings.case_provider} enforce_closed=deny",
    )

    investigation = await investigate_case(tenant_id, case_ref, runtime=runtime, actor=DEMO_ACTOR)
    expect(investigation.get("state") == "awaiting_decision", f"the investigation did not complete: {investigation}")
    step(
        "underwriter run completed",
        "fixture",
        f"state={investigation['state']} recommendation={investigation.get('recommendation')} tier={investigation.get('tier')}",
    )

    # ── every provider call, authorised one by one under the run grant ─────────────────────
    async with runtime.session_factory(tenant_id) as session:
        case = await get_case(session, tenant_id, case_ref)
        records = list(case.agent_records or [])
    run = records[-1]
    calls = list(run.get("tool_calls") or [])
    expect(bool(calls), "the run made no provider calls")
    run_grants = {c.get("grant_id") for c in calls if c.get("grant_id")}
    expect(len(run_grants) == 1, f"calls were not all under one run grant: {run_grants}")
    run_grant = next(iter(run_grants))
    step("run grant delegated from the root grant", "live", f"grant={run_grant} run={run['run_id']}")
    for call in calls:
        step(
            f"provider call authorised: {call['tool']}",
            "live",
            f"grant={call.get('grant_id', '')} outcome={call['outcome']} records={len(call.get('record_ids') or [])}",
        )
    expect(all(c.get("outcome") in ("ok", "pending", "not_available") for c in calls), "a provider call was refused")

    # ── an out-of-scope call, denied with its reason ──────────────────────────────────────
    authorizer = runtime.authorizer_for(str(tenant_id), case_ref, OUT_OF_SCOPE_ROLE, CASE_PURPOSE)
    decision = await authorizer.authorize(connector=settings.case_provider, tool=OUT_OF_SCOPE_TOOL)
    expect(not decision.allowed, f"{OUT_OF_SCOPE_ROLE} was allowed {OUT_OF_SCOPE_TOOL}, which its grant does not cover")
    step(
        f"out-of-scope call denied: {OUT_OF_SCOPE_ROLE} -> {OUT_OF_SCOPE_TOOL}",
        "live",
        f"reason={decision.reason} sub_reason={decision.sub_reason or '-'} grant={decision.grant_id or '-'}",
    )

    # ── the evidence package ──────────────────────────────────────────────────────────────
    service = evidence_service()
    expect(isinstance(service, GrantexEvidenceService), "the evidence sink is not configured")
    assert service is not None
    exported = await service.export(case_ref)
    package = json.loads(exported.data)
    entries = package.get("entries") or []
    kinds = sorted({e.get("type") for e in entries})
    step(
        "evidence package exported",
        "live",
        f"entries={len(entries)} types={','.join(kinds)} state={package.get('case', {}).get('state')} root={exported.root[:23]}…",
    )
    tool_entries = [e for e in entries if e.get("type") == "tool_call"]
    expect(
        len(tool_entries) == len(calls), f"the package holds {len(tool_entries)} tool calls, the run made {len(calls)}"
    )
    expect(any(e.get("type") == "grant" for e in entries), "the package carries no grant entry")
    expect(
        evidence_time(calls[0]["started_at"]) == tool_entries[0]["at"],
        "the first tool call's time differs in the package",
    )

    # Root and anchor from the tenant audit log, not from the exporter.
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0, follow_redirects=False) as http:
        audit = await http.get(
            "/v1/audit/entries",
            params={"action": "evidence.package_exported"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        expect(audit.status_code == 200, f"audit entries answered {audit.status_code}: {audit.text[:200]}")
        anchors = [e for e in audit.json().get("entries", []) if (e.get("metadata") or {}).get("case_id") == case_ref]
        expect(bool(anchors), "the audit log has no export entry for this case")
        anchor = anchors[-1]
        expect(
            anchor["metadata"].get("package_root") == exported.root,
            "the audit log's package root differs from the export's",
        )
        expect(anchor["hash"] == exported.anchor_hash, "the audit entry hash differs from the export's anchor")
        jwks = await http.get("/.well-known/jwks.json")
        expect(jwks.status_code == 200, f"JWK Set answered {jwks.status_code}")
    step(
        "root and anchor read from the tenant audit log",
        "live",
        f"entry={anchor['entryId']} root matches, anchor matches",
    )

    return {
        "case_ref": case_ref,
        "run_id": run["run_id"],
        "run_grant": run_grant,
        "package": package,
        "root": exported.root,
        "anchor": exported.anchor_hash,
        "jwks": jwks.json(),
        "steps": step,
        "denied": {"reason": decision.reason, "sub_reason": decision.sub_reason},
    }


def verify_with_cli(out_dir: Path, result: dict[str, Any], step: Steps) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    package_path = out_dir / f"{result['case_ref']}.evidence.json"
    jwks_path = out_dir / "grantex-jwks.json"
    package_path.write_bytes(json.dumps(result["package"], separators=(",", ":"), sort_keys=True).encode())
    jwks_path.write_text(json.dumps(result["jwks"]), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "grantex.cli",
        "evidence",
        "verify",
        str(package_path),
        "--root",
        result["root"],
        "--anchor",
        result["anchor"],
        "--require-anchor",
        "--jwks",
        str(jwks_path),
        "--require-signature",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    first = (completed.stdout.strip().splitlines() or [completed.stderr.strip()])[0]
    expect(
        completed.returncode == 0,
        f"grantex-evidence verify exited {completed.returncode}: {completed.stdout}{completed.stderr}",
    )
    step("package verified with grantex-evidence", "live", f"{first} ({package_path.name})")


def _jwt_claims(token: str) -> dict[str, Any]:
    import base64  # noqa: PLC0415

    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
        return claims if isinstance(claims, dict) else {}
    except (IndexError, ValueError):
        return {}


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description="Run one governed case end to end with evidence (development only).")
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE, help="mock provider fixture key")
    parser.add_argument("--llm-model", default=SAMPLE_AGENT_MODEL, help="model for the agents' prose")
    parser.add_argument(
        "--output", default="ui/test-results/demo-case", help="directory for the package, the JWK Set and the summary"
    )
    args = parser.parse_args(argv)
    try:
        assert_development_runtime(environ)
        assert_local_grantex(environ)
        result = asyncio.run(run_demo(args.fixture, llm_model=args.llm_model, environ=environ))
        steps: Steps = result["steps"]
        verify_with_cli(Path(args.output), result, steps)
    except (SeedError, DemoError) as exc:
        print(f"\ndemo-case: FAIL: {exc}", file=sys.stderr)
        return 1
    summary = {k: v for k, v in result.items() if k not in ("package", "jwks", "steps")}
    summary["steps"] = steps.lines
    (Path(args.output) / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("\ndemo-case: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
