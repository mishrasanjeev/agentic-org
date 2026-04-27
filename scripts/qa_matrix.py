"""qa_matrix — manage the manual ↔ automated test mapping.

Foundation #1 of the closure plan in
``feedback_no_manual_qa_closure_plan.md``: every one of the 434 TCs
in ``QA_MANUAL_TEST_PLAN.md`` must have a recorded mapping to either
an automated test, a tracked-as-manual exception, or an explicit
``needs-automation`` status.

Two operations:

  generate
    Walk QA_MANUAL_TEST_PLAN.md, parse out every ``### TC-NAMESPACE-NNN``
    heading + module + title, search the test tree for files that
    reference each TC id, and write ``docs/qa_test_matrix.yml`` with
    one entry per TC. Existing ``status`` / ``notes`` fields are
    preserved across regenerations so manual edits stick.

  check
    Read ``docs/qa_test_matrix.yml`` and report coverage + gaps. Exit
    codes:
      0  matrix is well-formed (informational mode — DEFAULT). Counts
         per status are printed; CI can grep the report.
      1  ``--enforce-unknown``: one or more TCs have ``status: unknown``
         (Foundation #1 ratchet — flip on once the backlog is cleared).
      2  the YAML is missing or malformed.

Usage::

    python -m scripts.qa_matrix generate
    python -m scripts.qa_matrix check                  # informational
    python -m scripts.qa_matrix check --enforce-unknown # ratchet
    python -m scripts.qa_matrix check --strict          # also fail on needs-automation
    python -m scripts.qa_matrix check --json            # machine-readable summary

Initial state of this PR: all 434 TCs land as ``unknown`` because no
existing test file references the TC ids by name. The path to clean:

  1. Engineer adds Playwright spec / pytest that exercises the manual
     scenario. Adds ``TC-LP-007`` (or whichever id) in a docstring
     or comment.
  2. ``python -m scripts.qa_matrix generate`` re-scans, picks up the
     reference, flips status to ``automated``.
  3. Once a module is fully classified, the team can flip the
     module's TCs to ``manual_only`` (rare) or set the gate to
     ``--enforce-unknown`` to catch future drift.

Foundation #5 (``Playwright-replaces-manual``) is the burndown lever
for the remaining ``unknown`` entries. This PR is the inventory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = REPO / "QA_MANUAL_TEST_PLAN.md"
DEFAULT_MATRIX = REPO / "docs" / "qa_test_matrix.yml"
DEFAULT_TEST_TREES = (
    REPO / "tests",
    REPO / "ui" / "e2e",
)

# Match `### TC-NS-NNN: Title text...` where namespace can be
# multi-segment (e.g. ``TC-LP-001``, ``TC-ORG-CHART-001``,
# ``TC-AGENT-WIZARD-FORK-001``). One or more dash-separated
# alphanumeric segments before the numeric suffix.
_TC_RE = re.compile(r"^###\s+(TC-(?:[A-Z0-9]+-)+\d+):\s*(.+?)\s*$")
# Module headings look like `## Module N: Name`. Track the latest one
# we've seen so each TC can record which module it belongs to.
_MODULE_RE = re.compile(r"^##\s+(Module\s+\d+:\s*.+?)\s*$")

# Statuses operators may write back into the YAML manually.
_STATUS_OK_VALUES = frozenset({
    "automated",          # has at least one automated reference
    "partial",            # automated but with a documented gap
    "manual_only",        # cannot be automated (e.g. visual / customer-comms)
    "deprecated",         # TC retired but the id is reserved
})
_STATUS_NEEDS_WORK = frozenset({"needs-automation"})
_STATUS_OPEN = frozenset({"unknown"})  # generated default — must be classified


@dataclass
class TC:
    id: str
    title: str
    module: str
    references: list[str] = field(default_factory=list)
    status: str = "unknown"
    notes: str = ""


# ──────────────────────────────────────────────────────────────────────
# Parse the markdown plan
# ──────────────────────────────────────────────────────────────────────


def parse_plan(plan_path: Path) -> list[TC]:
    """Walk QA_MANUAL_TEST_PLAN.md and yield every TC heading."""
    if not plan_path.exists():
        raise SystemExit(f"qa_matrix: plan not found at {plan_path}")
    out: list[TC] = []
    current_module = "Module 0: Unscoped"
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        m = _MODULE_RE.match(line)
        if m:
            current_module = m.group(1)
            continue
        t = _TC_RE.match(line)
        if t:
            out.append(TC(id=t.group(1), title=t.group(2), module=current_module))
    return out


# ──────────────────────────────────────────────────────────────────────
# Reference scanner — find files that mention a given TC id
# ──────────────────────────────────────────────────────────────────────


def _build_reference_index(test_trees: tuple[Path, ...]) -> dict[str, list[str]]:
    """Return ``{TC_ID: [relative_test_path, …]}``.

    Walks each test tree once (``rg`` would be faster but we want zero
    external deps so the script runs in any environment). Indexed
    so the per-TC lookup is O(1) instead of re-scanning the whole tree
    for every TC id.
    """
    by_tc: dict[str, list[str]] = {}
    pattern = re.compile(r"\bTC-(?:[A-Z0-9]+-)+\d+\b")
    for tree in test_trees:
        if not tree.exists():
            continue
        for path in tree.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".md"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for tc_id in set(pattern.findall(text)):
                rel = path.relative_to(REPO).as_posix()
                by_tc.setdefault(tc_id, []).append(rel)
    for refs in by_tc.values():
        refs.sort()
    return by_tc


# ──────────────────────────────────────────────────────────────────────
# YAML round-trip
# ──────────────────────────────────────────────────────────────────────


def _load_existing(matrix_path: Path) -> dict[str, dict[str, Any]]:
    """Return ``{tc_id: existing_entry_dict}`` from a previous generation.

    Empty dict if the file doesn't exist yet — first run.
    """
    if not matrix_path.exists():
        return {}
    raw = yaml.safe_load(matrix_path.read_text(encoding="utf-8")) or {}
    entries = raw.get("entries") or []
    return {e["id"]: e for e in entries if isinstance(e, dict) and "id" in e}


def _classify(refs: list[str], existing_status: str) -> str:
    """Decide the auto-fillable status for a TC.

    - If operator already set a value in {automated, partial, manual_only,
      deprecated, needs-automation}, keep it (don't trample manual review).
    - Otherwise: if we found references → ``automated``; else ``unknown``.
    """
    if existing_status in (_STATUS_OK_VALUES | _STATUS_NEEDS_WORK):
        return existing_status
    return "automated" if refs else "unknown"


def generate(
    plan_path: Path = DEFAULT_PLAN,
    matrix_path: Path = DEFAULT_MATRIX,
    test_trees: tuple[Path, ...] = DEFAULT_TEST_TREES,
) -> int:
    """Build / refresh the matrix YAML in place."""
    tcs = parse_plan(plan_path)
    if not tcs:
        print(f"qa_matrix: no TCs parsed from {plan_path}", file=sys.stderr)
        return 2
    refs_index = _build_reference_index(test_trees)
    existing = _load_existing(matrix_path)

    entries: list[dict[str, Any]] = []
    for tc in tcs:
        existing_entry = existing.get(tc.id, {})
        existing_status = existing_entry.get("status", "")
        existing_notes = existing_entry.get("notes", "") or ""
        refs = refs_index.get(tc.id, [])
        status = _classify(refs, existing_status)
        entries.append(
            {
                "id": tc.id,
                "module": tc.module,
                "title": tc.title,
                "status": status,
                "references": refs,
                "notes": existing_notes,
            }
        )

    payload = {
        "version": 1,
        "_about": (
            "Generated by `python -m scripts.qa_matrix generate` from "
            "QA_MANUAL_TEST_PLAN.md. Hand-edits to status/notes are "
            "preserved across regenerations. Allowed statuses: "
            f"{sorted(_STATUS_OK_VALUES | _STATUS_NEEDS_WORK | _STATUS_OPEN)}."
        ),
        "total_tcs": len(entries),
        "entries": entries,
    }
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    matrix_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, width=120), encoding="utf-8"
    )
    print(
        f"qa_matrix: wrote {matrix_path} "
        f"({len(entries)} TCs; {sum(1 for e in entries if e['references'])} have refs)"
    )
    return 0


# ──────────────────────────────────────────────────────────────────────
# Check / report
# ──────────────────────────────────────────────────────────────────────


def _bucket_summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for e in entries:
        s = e.get("status", "unknown")
        buckets[s] = buckets.get(s, 0) + 1
    return buckets


def check(
    matrix_path: Path = DEFAULT_MATRIX,
    *,
    strict: bool = False,
    enforce_unknown: bool = False,
    output_json: bool = False,
) -> int:
    """Validate the matrix and print a coverage report.

    Exit codes documented at the top of the module.
    """
    if not matrix_path.exists():
        print(
            f"qa_matrix: {matrix_path} missing — run `python -m scripts.qa_matrix generate` first",
            file=sys.stderr,
        )
        return 2
    raw = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        print(f"qa_matrix: {matrix_path} is malformed (missing top-level 'entries')", file=sys.stderr)
        return 2
    entries = raw["entries"]
    buckets = _bucket_summary(entries)
    unknown = [e["id"] for e in entries if e.get("status") == "unknown"]
    needs = [e["id"] for e in entries if e.get("status") == "needs-automation"]

    if output_json:
        print(
            json.dumps(
                {
                    "total": len(entries),
                    "buckets": buckets,
                    "unknown_ids": unknown,
                    "needs_automation_ids": needs,
                },
                indent=2,
            )
        )
    else:
        print(f"qa_matrix: {len(entries)} TCs total")
        for status in sorted(buckets):
            print(f"  {status:>20}: {buckets[status]}")
        if unknown and enforce_unknown:
            print(
                f"\nFAIL (--enforce-unknown): {len(unknown)} TC(s) with "
                "status=unknown — must be classified before merge:"
            )
            for tc_id in unknown[:10]:
                print(f"  - {tc_id}")
            if len(unknown) > 10:
                print(f"  … and {len(unknown) - 10} more")
        elif unknown:
            print(
                f"\nINFO: {len(unknown)} TC(s) still unclassified "
                "(use --enforce-unknown to fail CI on this)."
            )
        if strict and needs:
            print(
                f"\nFAIL (--strict): {len(needs)} TC(s) marked needs-automation"
            )
            for tc_id in needs[:10]:
                print(f"  - {tc_id}")
            if len(needs) > 10:
                print(f"  … and {len(needs) - 10} more")
        if not unknown and not (strict and needs):
            print("\nOK: every TC has an explicit status.")

    if enforce_unknown and unknown:
        return 1
    if strict and needs:
        return 1
    return 0


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="Refresh the matrix YAML in place.")
    g.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    g.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)

    c = sub.add_parser(
        "check",
        help="Validate the matrix and report gaps. Exits non-zero on unknown rows.",
    )
    c.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    c.add_argument(
        "--enforce-unknown",
        dest="enforce_unknown",
        action="store_true",
        help=(
            "Fail when any TC has status=unknown. Off by default during "
            "the Foundation #1 burndown (all 434 TCs land as unknown on "
            "first generate)."
        ),
    )
    c.add_argument(
        "--strict",
        action="store_true",
        help="Also fail when TCs are marked needs-automation.",
    )
    c.add_argument("--json", action="store_true", help="Emit JSON summary.")

    args = parser.parse_args(argv)
    if args.cmd == "generate":
        return generate(plan_path=args.plan, matrix_path=args.matrix)
    if args.cmd == "check":
        return check(
            matrix_path=args.matrix,
            strict=args.strict,
            enforce_unknown=args.enforce_unknown,
            output_json=args.json,
        )
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
