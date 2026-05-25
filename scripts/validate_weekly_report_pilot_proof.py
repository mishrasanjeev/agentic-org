#!/usr/bin/env python3
"""Validate a weekly-marketing-report pilot evidence bundle (CMO-PROD-1).

Reads a JSON evidence file (or STDIN) and prints the deterministic proof
verdict. Exit codes:

  0  -> ``passed`` (real_vendor) or ``sandbox_proven`` (vendor_sandbox)
  2  -> ``partial`` (informational; not production)
  3  -> ``blocked`` / ``demo_only`` / ``test_only`` / ``unavailable``

The evaluator is in :mod:`core.marketing.weekly_report_pilot_proof`. This
script never invents data: if the file is missing or empty, evidence is
treated as empty and the proof fails closed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow direct script invocation: ensure the repo root is on sys.path so
# `core.marketing.weekly_report_pilot_proof` resolves whether the script
# is run via `python scripts/validate_weekly_report_pilot_proof.py` or
# `python -m scripts.validate_weekly_report_pilot_proof`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.marketing.weekly_report_pilot_proof import (  # noqa: E402
    build_weekly_marketing_report_evidence_bundle,
    evaluate_weekly_marketing_report_proof,
    serialize_weekly_marketing_report_evidence_bundle,
    summarize_weekly_marketing_report_proof,
)


_EXIT_PASSED = 0
_EXIT_PARTIAL = 2
_EXIT_BLOCKED = 3


def _load_evidence(path: str | None) -> dict[str, Any]:
    if not path or path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        return {}
    return json.loads(text)


def _exit_code(status: str) -> int:
    if status in {"passed", "sandbox_proven"}:
        return _EXIT_PASSED
    if status == "partial":
        return _EXIT_PARTIAL
    return _EXIT_BLOCKED


def _format_output(proof: dict[str, Any], *, fmt: str) -> str:
    if fmt == "json":
        return serialize_weekly_marketing_report_evidence_bundle(
            build_weekly_marketing_report_evidence_bundle(proof)
        )
    summary = summarize_weekly_marketing_report_proof(proof)
    lines = [
        "CMO-PROD-1 Weekly Marketing Report Pilot Proof",
        "==============================================",
        f"Proof ID:                 {summary.get('proof_id')}",
        f"Environment:              {summary.get('environment_type')}",
        f"Status:                   {summary.get('proof_status')}",
        f"Readiness score:          {summary.get('readiness_score')} / 100",
        f"Production claim allowed: {summary.get('production_claim_allowed')}",
        f"Real-vendor claim:        {summary.get('real_vendor_claim_allowed')}",
        f"Proven capabilities:      {summary.get('proven_capabilities')}",
        f"Blockers:                 {summary.get('blockers')}",
        f"Risks:                    {summary.get('risks')}",
        f"Next action CTA:          {summary.get('next_action_cta')}",
        "",
    ]
    blockers = proof.get("blockers") or []
    if blockers:
        lines.append("Blockers:")
        for row in blockers:
            lines.append(
                f"  - [{row.get('severity', 'n/a')}] {row.get('category', '')}: "
                f"{row.get('message', '')} -> {row.get('next_action', '')}"
            )
        lines.append("")
    risks = proof.get("risks") or []
    if risks:
        lines.append("Risks:")
        for row in risks:
            lines.append(
                f"  - [{row.get('severity', 'n/a')}] {row.get('category', '')}: "
                f"{row.get('message', '')} -> {row.get('next_action', '')}"
            )
        lines.append("")
    next_actions = proof.get("next_actions") or []
    if next_actions:
        lines.append("Next actions:")
        for row in next_actions:
            lines.append(f"  - {row.get('action_key', '')}: {row.get('label', '')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        "-e",
        help="Path to JSON evidence file. Use '-' or omit to read from STDIN.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format. JSON output is secret-redacted.",
    )
    args = parser.parse_args(argv)
    try:
        evidence = _load_evidence(args.evidence)
    except FileNotFoundError as exc:
        print(f"error: evidence file not found: {exc.filename}", file=sys.stderr)
        return _EXIT_BLOCKED
    except json.JSONDecodeError as exc:
        print(f"error: evidence JSON is invalid: {exc}", file=sys.stderr)
        return _EXIT_BLOCKED
    proof = evaluate_weekly_marketing_report_proof(evidence)
    print(_format_output(proof, fmt=args.format))
    return _exit_code(str(proof.get("proof_status") or "blocked"))


if __name__ == "__main__":
    sys.exit(main())
