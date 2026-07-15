#!/usr/bin/env python3
"""Validate the claim registry and scan governed public product surfaces."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.claims import scan_surfaces  # noqa: E402


def _instant(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("surfaces", nargs="*", help="Optional repository-relative surface paths")
    parser.add_argument("--root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--registry", type=Path, default=_REPO_ROOT / "config" / "public_claim_registry.json")
    parser.add_argument("--at", help="Timezone-aware validation instant (ISO-8601); defaults to now")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        report = scan_surfaces(args.root, args.registry, paths=args.surfaces or None, now=_instant(args.at))
    except (OSError, ValueError) as exc:
        print(f"claim lint configuration error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        payload = report.model_dump(mode="json")
        payload["valid"] = report.valid
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Claim registry {report.registry_version}: {'PASS' if report.valid else 'FAIL'}")
        for issue in report.issues:
            location = issue.surface or "registry"
            if issue.line is not None:
                location += f":{issue.line}"
            subject = issue.claim_id or issue.capability_id or issue.evidence_id or "-"
            print(f"[{issue.severity.upper()}] {location} {issue.code} ({subject}): {issue.message}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
