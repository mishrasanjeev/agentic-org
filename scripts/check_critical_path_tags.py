#!/usr/bin/env python3
"""Enforce that every critical-path Playwright tag appears in ≥1 test.

The Enterprise Readiness plan pins these as the must-have regression
tags. If the test suite ever stops covering one — because a spec was
deleted, a describe was re-worded, or nobody tagged the replacement —
we fail before CI, not after a customer hits the gap.

Usage:

    python scripts/check_critical_path_tags.py [ui/e2e]

Exits 0 if every tag has ≥1 occurrence. Non-zero on first miss.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SPEC_DIR = ROOT / "ui" / "e2e"

CRITICAL_TAGS: list[str] = [
    "@auth",
    "@tenancy",
    "@sdk",
    "@mcp",
    "@hitl",
    "@connector",
    "@governance",
    "@audit",
]


def _scan(spec_dir: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    """Return {tag: [files where it appears]}."""
    hits: dict[str, list[pathlib.Path]] = {t: [] for t in CRITICAL_TAGS}
    for path in spec_dir.rglob("*.spec.ts"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for tag in CRITICAL_TAGS:
            # Match @tag as a whole word — not @tagsomething.
            if re.search(rf"{re.escape(tag)}\b", text):
                hits[tag].append(path)
    return hits


def main() -> int:
    spec_dir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SPEC_DIR
    if not spec_dir.exists():
        print(f"[tags] no such directory: {spec_dir}", file=sys.stderr)
        return 2
    print("=" * 60)
    print(f"Critical-path Playwright tag coverage (spec dir: {spec_dir})")
    print("=" * 60)
    hits = _scan(spec_dir)
    missing: list[str] = []
    for tag in CRITICAL_TAGS:
        files = hits[tag]
        if files:
            print(f"[OK  ] {tag:<14} in {len(files)} file(s): "
                  f"{', '.join(f.name for f in files[:3])}"
                  f"{' ...' if len(files) > 3 else ''}")
        else:
            print(f"[FAIL] {tag:<14} not found")
            missing.append(tag)
    print("=" * 60)
    if missing:
        print(f"{len(missing)} tag(s) uncovered:")
        for tag in missing:
            print(f"  - {tag}")
        return 1
    print("all critical-path tags covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
