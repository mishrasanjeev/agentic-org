#!/usr/bin/env python3
"""CMO-PROD-3 — sandbox pilot walk-through for the weekly marketing report.

Fail-closed orchestrator. Discovers sandbox configuration from tenant
ConnectorConfig rows first, then local/dev environment variable fallback,
validates DB/migration/
connector/mapping/backfill prerequisites, and either:

  * runs the real ``generate_report`` -> CMO-PROD-2 persistence flow and
    prints the redacted verdict that landed in
    ``weekly_report_pilot_proofs``; or
  * prints a redacted preflight envelope listing exactly which env vars
    must be populated and refuses to insert any DB row.

Exit codes:

  0  -> sandbox_proven verdict was persisted (real connectors only).
  2  -> partial / blocked verdict was persisted (still vendor_sandbox,
        still production_claim_allowed=False).
  3  -> preflight failed (no creds / no DB / no migration applied).

Run:

    python scripts/run_weekly_report_sandbox_pilot.py [--format text|json]

This script never prints raw secrets. Output is redacted by the same
marker list used by ``core.marketing.weekly_report_pilot_proof``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.marketing.weekly_report_sandbox_pilot import (  # noqa: E402
    build_blocked_preflight_envelope,
    discover_sandbox_pilot_config,
    run_sandbox_pilot,
)


_EXIT_PASSED = 0
_EXIT_PARTIAL = 2
_EXIT_BLOCKED = 3


def _exit_code(summary: dict) -> int:
    proof_summary = summary.get("latest_weekly_report_pilot_proof") or summary.get(
        "weekly_report_pilot_proof"
    )
    if isinstance(proof_summary, dict):
        status = str(proof_summary.get("proof_status") or "blocked")
    else:
        status = str(summary.get("proof_status") or "blocked")
    if status in {"passed", "sandbox_proven"}:
        return _EXIT_PASSED
    if status == "partial":
        return _EXIT_PARTIAL
    return _EXIT_BLOCKED


def _format_text(summary: dict) -> str:
    preflight_status = summary.get("preflight_status") or "blocked"
    lines = [
        "CMO-PROD-3 Sandbox Pilot Walk-Through",
        "======================================",
        f"Preflight status:         {preflight_status}",
        f"Environment type:         {summary.get('environment_type')}",
        f"Tenant id:                {summary.get('tenant_id') or '<missing>'}",
        f"Company id:               {summary.get('company_id') or '<not set>'}",
    ]
    if preflight_status != "ready":
        missing_envs = summary.get("missing_envs") or []
        missing_categories = summary.get("missing_categories") or []
        lines.append(f"Missing connector categories: {', '.join(missing_categories) or 'none'}")
        if missing_envs:
            lines.append("Missing env vars:")
            for name in missing_envs:
                lines.append(f"  - {name}")
        blockers = summary.get("blockers") or []
        if blockers:
            lines.append("")
            lines.append("Blockers:")
            for row in blockers:
                lines.append(
                    f"  - [{row.get('severity', 'n/a')}] {row.get('category', '')}: "
                    f"{row.get('message', '')} -> {row.get('next_action', '')}"
                )
        next_actions = summary.get("next_actions") or []
        if next_actions:
            lines.append("")
            lines.append("Next actions:")
            for row in next_actions:
                lines.append(f"  - {row.get('action_key', '')}: {row.get('label', '')}")
        lines.append("")
        lines.append(str(summary.get("note") or ""))
        return "\n".join(lines)

    proof = summary.get("latest_weekly_report_pilot_proof") or summary.get(
        "weekly_report_pilot_proof"
    ) or summary
    lines.append(f"Proof id:                {proof.get('proof_id')}")
    lines.append(f"Proof status:            {proof.get('proof_status')}")
    lines.append(
        f"Production claim allowed:{proof.get('production_claim_allowed')}"
    )
    lines.append(
        f"Real-vendor claim:       {proof.get('real_vendor_claim_allowed')}"
    )
    lines.append(f"Readiness score:         {proof.get('readiness_score')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format. JSON output is secret-redacted.",
    )
    parser.add_argument("--json", action="store_true", help="Alias for --format json.")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run the env/DB/connector preflight without invoking the report task.",
    )
    args = parser.parse_args(argv)

    output_format = "json" if args.json else args.format

    if args.preflight_only:
        preflight = discover_sandbox_pilot_config()
        summary: dict = build_blocked_preflight_envelope(preflight) if preflight.preflight_status != "ready" else {
            "schema_version": "preflight_only",
            "preflight_status": "ready",
            "environment_type": "vendor_sandbox",
            "proof_status": preflight.proof_status,
            "production_claim_allowed": False,
            "real_vendor_claim_allowed": False,
            "proof_inserted": False,
            "tenant_id": preflight.config.tenant_id,
            "company_id": preflight.config.company_id,
            "db_discovery_state": preflight.config.db_discovery_state,
            "chosen_connectors": preflight.config.chosen_connectors,
            "note": "Preflight passes. Re-run without --preflight-only to execute the pilot.",
        }
    else:
        summary = asyncio.run(run_sandbox_pilot())

    if output_format == "json":
        print(json.dumps(summary, indent=2, default=str, sort_keys=True))
    else:
        print(_format_text(summary))
    return _exit_code(summary)


if __name__ == "__main__":
    sys.exit(main())
