"""qa_audit — heuristic classification of the 434 manual TCs.

Companion to ``qa_matrix.py``. The user directive (2026-04-27):
"check if these all 434 test cases are valid or not — do not do
brute force work". Brute-forcing 434 Playwright specs is wasteful
when many TCs are duplicates, stale references, or inherently
manual.

This script runs four cheap heuristics and proposes a classification
per TC:

  1. URL existence: extract paths from TC body, HEAD them against
     ``BASE_URL`` (default https://app.agenticorg.ai). Any TC whose
     entire URL set returns 404 → ``deprecated``.

  2. Manual-only patterns: TC body mentions "view source", "DevTools",
     "Inspect element", "visually verify", "looks correct" etc. →
     proposes ``manual_only``.

  3. Existing automation by title-keyword fuzzy match: extract the
     dominant nouns from the title, search test files for any file
     whose body contains BOTH the noun(s) and the TC's URL path.
     If found → proposes ``automated`` with the file path as ref.

  4. Title-similarity duplicates within a module: pairs of TCs whose
     titles share >0.8 ratio (rapidfuzz / difflib) and whose steps
     overlap heavily → proposes ``duplicate`` for the second one.

The output is written to ``docs/qa_test_audit.json`` so a human can
review the proposals before flipping them into ``qa_test_matrix.yml``
(or running with ``--apply`` to write straight into the matrix —
default is dry-run; manual review is the safer first pass).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = REPO / "QA_MANUAL_TEST_PLAN.md"
DEFAULT_MATRIX = REPO / "docs" / "qa_test_matrix.yml"
DEFAULT_AUDIT = REPO / "docs" / "qa_test_audit.json"
DEFAULT_BASE = os.getenv("AGENTICORG_AUDIT_BASE", "https://app.agenticorg.ai").rstrip("/")
DEFAULT_TEST_TREES = (REPO / "tests", REPO / "ui" / "e2e")

_TC_HEAD_RE = re.compile(r"^###\s+(TC-(?:[A-Z0-9]+-)+\d+):\s*(.+?)\s*$")
_MODULE_RE = re.compile(r"^##\s+(Module\s+\d+:\s*.+?)\s*$")

# Phrase patterns that strongly suggest the TC is inherently manual.
_MANUAL_PATTERNS = [
    r"view\s+page\s+source",
    r"open\s+browser\s+devtools",
    r"inspect\s+element",
    r"visually\s+verify|visual\s+check|visual\s+inspection",
    r"looks?\s+correct|renders?\s+properly|design\s+looks",
    r"send\s+(?:a\s+)?real\s+email",
    r"verify.*in\s+(?:gmail|outlook)\s+inbox",
    r"check.*physical\s+(?:device|phone)",
    r"switch\s+(?:to|between)\s+(?:dark|light)\s+mode",
    r"tap\s+(?:on\s+)?(?:screen|device)",
]

_URL_RE = re.compile(r"https?://[^\s)\]\"']+")


@dataclass
class TCRecord:
    id: str
    module: str
    title: str
    body: str
    paths: set[str] = field(default_factory=set)
    proposal: str = "needs-review"
    reason: str = ""
    refs: list[str] = field(default_factory=list)
    duplicate_of: str | None = None


# ──────────────────────────────────────────────────────────────────────
# Plan parser (extracts full body, not just the heading)
# ──────────────────────────────────────────────────────────────────────


def parse_plan_with_bodies(plan_path: Path) -> list[TCRecord]:
    """Return one TCRecord per TC, including the full body text."""
    text = plan_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    records: list[TCRecord] = []
    current_module = "Module 0: Unscoped"
    current_tc: TCRecord | None = None
    body_buf: list[str] = []

    def flush() -> None:
        if current_tc is not None:
            current_tc.body = "\n".join(body_buf).strip()
            current_tc.paths = _extract_paths(current_tc.body)
            records.append(current_tc)

    for line in lines:
        m_mod = _MODULE_RE.match(line)
        if m_mod:
            flush()
            current_tc = None
            body_buf = []
            current_module = m_mod.group(1)
            continue
        m_tc = _TC_HEAD_RE.match(line)
        if m_tc:
            flush()
            current_tc = TCRecord(
                id=m_tc.group(1), module=current_module, title=m_tc.group(2), body=""
            )
            body_buf = []
            continue
        if current_tc is not None:
            body_buf.append(line)
    flush()
    return records


def _extract_paths(body: str) -> set[str]:
    """Pull URL paths out of a TC body."""
    out: set[str] = set()
    for full in _URL_RE.findall(body):
        # Strip trailing punctuation that the regex may pick up.
        full = full.rstrip(".,;:!?")
        try:
            from urllib.parse import urlparse

            p = urlparse(full)
            if p.path:
                out.add(p.path)
        except Exception:  # noqa: BLE001, S112 — bad URL in test plan body, skip
            continue
    return out


# ──────────────────────────────────────────────────────────────────────
# Heuristics
# ──────────────────────────────────────────────────────────────────────


def _is_manual_only(body: str) -> str | None:
    body_lower = body.lower()
    for pat in _MANUAL_PATTERNS:
        if re.search(pat, body_lower):
            return pat
    return None


def _check_url_health(paths: set[str], base: str, head_cache: dict[str, int]) -> dict[str, int]:
    """HEAD each path, cache results across calls. Returns ``{path: status}``.

    SEC-2026-05-P2-011: validate that ``base`` is HTTP(S) before
    calling ``urlopen`` — defends against ``file:`` / ``ftp:`` /
    custom-scheme switching even if a misconfigured operator config
    points ``base`` at something other than a web URL.
    """
    if not (base.startswith("https://") or base.startswith("http://")):
        raise ValueError(
            f"refusing non-HTTP(S) base URL (scheme hijack guard): {base!r}"
        )
    out = {}
    for path in paths:
        if path in head_cache:
            out[path] = head_cache[path]
            continue
        url = base + path if path.startswith("/") else f"{base}/{path}"
        try:
            # nosec B310 — base scheme validated above; URL is built
            # from a trusted config + a path the audit script controls.
            req = urllib.request.Request(url, method="HEAD")  # noqa: S310
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310  # nosec B310
                head_cache[path] = resp.status
                out[path] = resp.status
        except urllib.error.HTTPError as e:
            head_cache[path] = e.code
            out[path] = e.code
        except Exception:  # noqa: BLE001
            head_cache[path] = 0  # network error — don't flag as deprecated
            out[path] = 0
    return out


def _build_test_index(
    test_trees: tuple[Path, ...],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build two strict indices over test files:

    - ``by_tc``: TC id (literal ``TC-XX-NNN``) → files mentioning it
    - ``by_path``: URL path string → files mentioning it

    The earlier shallow keyword index produced massive false positives
    (e.g. ``TC-AUTH-003 Login`` matched ``test_gstn_sandbox.py`` because
    both files contained the word "login"). These two indices only flag
    a TC as automated when the test file mentions either the literal
    TC id or one of the TC's URL paths.
    """
    tc_re = re.compile(r"\bTC-(?:[A-Z0-9]+-)+\d+\b")
    path_re = re.compile(r"['\"]/(api|dashboard|pricing|evals|playground|login|signup|blog|resources|solutions)[^\"']*['\"]")
    by_tc: dict[str, set[str]] = defaultdict(set)
    by_path: dict[str, set[str]] = defaultdict(set)
    for tree in test_trees:
        if not tree.exists():
            continue
        for path in tree.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".js"}:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = path.relative_to(REPO).as_posix()
            for tc_id in set(tc_re.findall(content)):
                by_tc[tc_id].add(rel)
            for match in path_re.findall(content):
                by_path["/" + match].add(rel)
    return by_tc, by_path


def _existing_automation_match(
    record: TCRecord,
    tc_index: dict[str, set[str]],
    path_index: dict[str, set[str]],
) -> list[str]:
    """Strict match: literal TC id reference OR URL path overlap."""
    refs: set[str] = set()
    if record.id in tc_index:
        refs |= tc_index[record.id]
    for p in record.paths:
        # Match by the leading segment ("/playground" wins for any
        # /playground or /playground/foo). Reduces noise from generic
        # paths like "/" or "/api".
        first_seg = "/" + p.strip("/").split("/")[0]
        if len(first_seg) > 4 and first_seg in path_index:
            refs |= path_index[first_seg]
    return sorted(refs)


def _find_dupes_within_module(records: list[TCRecord]) -> dict[str, str]:
    """Return ``{tc_id: master_tc_id}`` for likely duplicates inside the same module."""
    out: dict[str, str] = {}
    by_module: dict[str, list[TCRecord]] = defaultdict(list)
    for r in records:
        by_module[r.module].append(r)
    for module, group in by_module.items():
        del module  # not used in body
        for i, r1 in enumerate(group):
            for r2 in group[i + 1 :]:
                if r2.id in out:
                    continue
                ratio = difflib.SequenceMatcher(
                    None, r1.title.lower(), r2.title.lower()
                ).ratio()
                if ratio >= 0.85:
                    out[r2.id] = r1.id
    return out


# ──────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────


def audit(
    plan_path: Path = DEFAULT_PLAN,
    matrix_path: Path = DEFAULT_MATRIX,
    audit_path: Path = DEFAULT_AUDIT,
    base: str = DEFAULT_BASE,
    skip_url_check: bool = False,
    apply: bool = False,
) -> int:
    print(f"qa_audit: parsing {plan_path}")
    records = parse_plan_with_bodies(plan_path)
    print(f"qa_audit: {len(records)} TCs to triage")

    print("qa_audit: building strict test indices (TC ids + URL paths)…")
    tc_index, path_index = _build_test_index(DEFAULT_TEST_TREES)
    print(
        f"qa_audit:   {len(tc_index)} TC ids referenced; "
        f"{len(path_index)} distinct URL paths"
    )

    print("qa_audit: detecting in-module duplicates…")
    dupes = _find_dupes_within_module(records)
    print(f"qa_audit:   {len(dupes)} likely duplicate(s) flagged")

    head_cache: dict[str, int] = {}
    skipped_url = 0
    for r in records:
        # 1. duplicate
        if r.id in dupes:
            r.proposal = "duplicate"
            r.duplicate_of = dupes[r.id]
            r.reason = f"title >=85% similar to {dupes[r.id]} in same module"
            continue
        # 2. manual-only by phrase
        m = _is_manual_only(r.body)
        if m:
            r.proposal = "manual_only"
            r.reason = f"manual phrase pattern: {m}"
            continue
        # 3. existing automation — STRICT: TC id reference OR URL path
        # overlap. Title-keyword fuzzy match was producing massive
        # false positives (audit history 2026-04-27).
        refs = _existing_automation_match(r, tc_index, path_index)
        if refs:
            r.proposal = "automated"
            r.refs = refs[:5]
            r.reason = (
                "matched on TC id reference or URL path overlap"
            )
            continue
        # 4. URL health
        if not skip_url_check and r.paths:
            statuses = _check_url_health(r.paths, base, head_cache)
            if statuses and all(v in (404, 410) for v in statuses.values()):
                r.proposal = "deprecated"
                r.reason = f"all paths return 404/410 against {base}: {statuses}"
                continue
            if statuses and any(v == 0 for v in statuses.values()):
                skipped_url += 1
        # 5. fall through
        r.proposal = "needs-automation"
        r.reason = "no existing test match; not flagged manual or deprecated"

    print("qa_audit: classification summary:")
    buckets = defaultdict(int)
    for r in records:
        buckets[r.proposal] += 1
    for status in sorted(buckets):
        print(f"  {status:>20}: {buckets[status]}")
    if skipped_url:
        print(f"  (URL health was inconclusive on {skipped_url} TC(s))")

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(
            {
                "generated_against": base,
                "totals": dict(buckets),
                "records": [
                    {
                        "id": r.id,
                        "module": r.module,
                        "title": r.title,
                        "proposal": r.proposal,
                        "reason": r.reason,
                        "refs": r.refs,
                        "duplicate_of": r.duplicate_of,
                        "paths": sorted(r.paths),
                    }
                    for r in records
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"qa_audit: wrote {audit_path}")

    if apply:
        _apply_proposals(matrix_path, records)
    else:
        print("qa_audit: --apply not set; matrix unchanged. Review the JSON, then re-run with --apply.")
    return 0


def _apply_proposals(matrix_path: Path, records: list[TCRecord]) -> None:
    """Merge proposals into the matrix YAML, preserving operator notes."""
    if not matrix_path.exists():
        print(f"qa_audit: matrix {matrix_path} missing — run qa_matrix generate first", file=sys.stderr)
        return
    raw: Any = yaml.safe_load(matrix_path.read_text(encoding="utf-8")) or {}
    entries = raw.get("entries") or []
    by_id = {e["id"]: e for e in entries}
    flipped = 0
    for r in records:
        e = by_id.get(r.id)
        if not e:
            continue
        # Don't trample operator-locked statuses.
        if e.get("status") in ("automated", "partial", "manual_only", "deprecated", "duplicate"):
            continue
        if r.proposal == "needs-review":
            continue
        e["status"] = r.proposal
        existing_notes = (e.get("notes") or "").strip()
        new_note = f"audit: {r.reason}"
        if r.duplicate_of:
            new_note += f" (master={r.duplicate_of})"
        if r.refs:
            new_note += f" (refs={r.refs[:3]})"
        e["notes"] = new_note if not existing_notes else existing_notes
        if r.refs and not e.get("references"):
            e["references"] = r.refs
        flipped += 1
    matrix_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, width=120), encoding="utf-8"
    )
    print(f"qa_audit: applied {flipped} proposal(s) into {matrix_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--base", type=str, default=DEFAULT_BASE)
    parser.add_argument(
        "--skip-url-check",
        action="store_true",
        help="Skip the per-path HEAD probe (useful offline / faster).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write proposals straight into qa_test_matrix.yml (preserves operator notes).",
    )
    args = parser.parse_args(argv)
    return audit(
        plan_path=args.plan,
        matrix_path=args.matrix,
        audit_path=args.audit,
        base=args.base,
        skip_url_check=args.skip_url_check,
        apply=args.apply,
    )


if __name__ == "__main__":
    raise SystemExit(main())
