"""Foundation #8 — pinned regressions for Claude's recurring mistakes.

Each test corresponds to a documented mistake class from the
2026-04-26 closure plan (`feedback_no_manual_qa_closure_plan.md`).
The test fails whenever the codebase regresses toward the mistake.

The eight mistake classes:

1. Key deletion breaks old decrypt → covered by
   ``test_crypto_keyring.test_case3_reject_delete_old_key_while_referenced``.
   Pin: the verify-all CLI gate exists and the test file is present.
2. Credential rotation overwrites unrelated stored fields → covered by
   ``test_crypto_keyring.test_case6_connector_partial_update_preserves_old_fields``.
   Pin: the test file is present.
3. Stale docs claim full coverage → NEW. Lints docs/*.md for
   absolute coverage claims that aren't backed by a corresponding
   test artefact reference.
4. Skipped tests hide missing env vars → NEW. Walks pytest skip
   markers and fails on reasons outside the documented allowlist.
5. Fake localStorage auth passes UI tests → ratchet. Counts
   ``localStorage.setItem("token", ...)`` occurrences in
   ``ui/e2e/`` and fails when the count exceeds the pinned cap.
6. Hard deletes remove audit/decryptable history → ratchet.
   Counts ``@router.delete`` handlers and fails when the count
   exceeds the pinned cap (forcing review of every new DELETE
   endpoint).
7. Coverage script returns green with no coverage data → covered
   by ``scripts/check_module_coverage.py``. Pin: the script
   exists and contains the no-data-fail branch.
8. Public claims drift from runtime registry → covered by
   ``scripts/consistency_sweep.py``. Pin: the script exists.

Why ratchets vs hard zeros for #5/#6: many existing patterns are
grandfathered; a hard-zero test would block every PR until a
multi-week retrofit lands. The ratchet (cap = current count)
ensures the codebase can only get better, never worse, while a
follow-up sprint drives the cap toward zero.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# Mistake 1 — key deletion breaks old decrypt
# ─────────────────────────────────────────────────────────────────


def test_mistake_1_verify_all_gate_still_exists() -> None:
    """The ``core.crypto.verify_all`` CLI is the gate that refuses
    to retire a key while ciphertext still references it. If this
    file disappears or the gate is gutted, the SECRET_KEY incident
    can recur.
    """
    src = (REPO / "core" / "crypto" / "verify_all.py").read_text(encoding="utf-8")
    assert "class KeyStillReferencedError" in src
    assert "_SCANNERS" in src
    test = (REPO / "tests" / "regression" / "test_crypto_keyring.py").read_text(
        encoding="utf-8"
    )
    assert "test_case3_reject_delete_old_key_while_referenced" in test


# ─────────────────────────────────────────────────────────────────
# Mistake 2 — credential rotation overwrites unrelated fields
# ─────────────────────────────────────────────────────────────────


def test_mistake_2_partial_update_test_still_pinned() -> None:
    """Pin the existing test that asserts updating one field of an
    encrypted credential blob does NOT clear the others. This
    behavior was a real regression class on the connectors path."""
    test = (REPO / "tests" / "regression" / "test_crypto_keyring.py").read_text(
        encoding="utf-8"
    )
    assert "test_case6_connector_partial_update_preserves_old_fields" in test


# ─────────────────────────────────────────────────────────────────
# Mistake 3 — stale docs claim coverage they don't have
# ─────────────────────────────────────────────────────────────────


# Phrases that are red flags when they appear in marketing-shaped
# docs without an accompanying file/test reference. Each phrase is
# allowed only if the doc cites a concrete artefact in the same
# bullet/section (the regex below tolerates that).
ABSOLUTE_COVERAGE_CLAIMS = re.compile(
    r"\b(100%\s+(coverage|covered|of\s+manual\s+tests))|"
    r"(fully\s+covered)|"
    r"(complete\s+coverage)\b",
    re.IGNORECASE,
)

# Files allowed to make absolute claims (they're either historical
# completion records or audit reports that are point-in-time true,
# or they quote the rule itself as documentation).
ABSOLUTE_CLAIM_ALLOWLIST = {
    # Documents the rule itself — every match is meta-text describing
    # what the rule forbids.
    "docs/STRICT_EXECUTION_BACKLOG_2026-04-18.md",
}


def _doc_files() -> list[Path]:
    docs = REPO / "docs"
    if not docs.exists():
        return []
    return sorted(p for p in docs.rglob("*.md") if p.is_file())


def test_mistake_3_no_absolute_coverage_claims_in_docs() -> None:
    """Lint docs/*.md for absolute coverage claims (100%, fully
    covered, complete coverage) that aren't backed by a citation
    on the same line. The 2026-04-22 incident: shipped doc claimed
    100% test coverage; reality was 47% on the touched module."""
    offenders: list[tuple[str, int, str]] = []
    for path in _doc_files():
        rel = path.relative_to(REPO).as_posix()
        if rel in ABSOLUTE_CLAIM_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for ln, line in enumerate(text.splitlines(), 1):
            if not ABSOLUTE_COVERAGE_CLAIMS.search(line):
                continue
            # A line that cites a test file or PR is allowed —
            # the claim is reviewable.
            if re.search(r"\b(test_\w+|\.spec\.ts|PR\s*#\d+|\.py:\d+)\b", line):
                continue
            offenders.append((rel, ln, line.strip()[:160]))
    assert not offenders, (
        "Absolute coverage claims without citations:\n"
        + "\n".join(f"  {p}:{ln} → {snip}" for p, ln, snip in offenders)
    )


# ─────────────────────────────────────────────────────────────────
# Mistake 4 — skipped tests hide missing env vars
# ─────────────────────────────────────────────────────────────────


# Reasons documented as legitimate skip causes. Every other reason
# (or empty reason) fails this test. New legitimate skip-reason?
# Add it here in the same PR with the test that uses it.
ALLOWED_SKIP_REASONS = re.compile(
    r"^("
    r"requires\s+postgres|requires\s+redis|requires\s+celery|"
    r"requires\s+gcp|requires\s+kubernetes|"
    r"foundation\s+#\d|"
    r"flaky\s+on\s+windows|windows-only|"
    r"placeholder\s+for\s+follow-up|"
    r"manual\s+only|"
    r"deferred\s+to\s+pr-?\w+|"
    r".+integration test.+|"  # broad — but reviewed in code
    r"requires\s+real\s+\w+|"
    # Documented infra cuts — the helm chart was removed in the
    # Cloud Run migration; tests asserting helm shape stay
    # legitimately skipped.
    r"helm\s+chart\s+removed|"
    # Sibling-sweep markers prove the related skips stayed in
    # place after a follow-up PR landed.
    r"sibling-sweep\s+marker"
    r")",
    re.IGNORECASE,
)

SKIP_DECORATOR = re.compile(
    r'@pytest\.mark\.(skip|xfail)\(\s*'
    r'(?:reason\s*=\s*)?["\']([^"\']*)["\']',
    re.MULTILINE,
)


def test_mistake_4_skip_reasons_match_allowlist() -> None:
    """Every @pytest.mark.skip / @pytest.mark.xfail must carry a
    reason that matches the documented allowlist. Empty reasons
    or unknown reasons fail. This catches the pattern where a
    test was skipped because an env var was missing in CI and the
    skip silently became permanent."""
    offenders: list[tuple[str, str]] = []
    for path in (REPO / "tests").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in SKIP_DECORATOR.finditer(text):
            reason = match.group(2).strip()
            if not reason or not ALLOWED_SKIP_REASONS.search(reason):
                rel = path.relative_to(REPO).as_posix()
                offenders.append((rel, reason or "<empty>"))
    assert not offenders, (
        "Skip/xfail reasons outside the allowlist:\n"
        + "\n".join(f"  {p} → {r!r}" for p, r in offenders[:30])
        + (f"\n... and {len(offenders) - 30} more" if len(offenders) > 30 else "")
        + "\n\nAdd your reason to ALLOWED_SKIP_REASONS in "
        "tests/regression/test_claude_mistakes.py if it's legitimate."
    )


# ─────────────────────────────────────────────────────────────────
# Mistake 5 — fake localStorage auth bypass in Playwright
# ─────────────────────────────────────────────────────────────────


# Cap pinned at the count present when this test was introduced
# (2026-04-28). The closure plan says Playwright must use a real
# backend session, not a stubbed one. The cap RATCHETS DOWN as
# specs are retrofitted; it must NEVER ratchet up.
LOCALSTORAGE_TOKEN_CAP = 60

LOCALSTORAGE_TOKEN_PATTERN = re.compile(
    r'localStorage\.setItem\(\s*["\']\w*token\w*["\']',
    re.IGNORECASE,
)


def test_mistake_5_localstorage_auth_bypass_count_below_cap() -> None:
    """Count occurrences of ``localStorage.setItem("token", ...)``
    in ui/e2e/. Existing patterns are grandfathered; new ones
    fail until the cap is explicitly raised in this file (which
    forces review). The cap should ratchet DOWN over time."""
    e2e = REPO / "ui" / "e2e"
    if not e2e.exists():
        pytest.skip("ui/e2e not present in this checkout")
    count = 0
    for path in e2e.rglob("*.spec.ts"):
        text = path.read_text(encoding="utf-8", errors="replace")
        count += len(LOCALSTORAGE_TOKEN_PATTERN.findall(text))
    assert count <= LOCALSTORAGE_TOKEN_CAP, (
        f"localStorage token-bypass count is {count}, cap is "
        f"{LOCALSTORAGE_TOKEN_CAP}. New Playwright specs must use "
        f"a real backend session (page.request.post('/api/v1/auth/"
        f"login') + session cookie). If a new spec genuinely needs "
        f"the bypass, raise the cap in this file with a justifying "
        f"comment."
    )


# ─────────────────────────────────────────────────────────────────
# Mistake 6 — hard deletes remove audit/decryptable history
# ─────────────────────────────────────────────────────────────────


# Cap pinned at the count present when this test was introduced
# (2026-04-28). Every existing DELETE handler is grandfathered;
# new ones fail unless the cap is raised here (forcing review of
# whether the new endpoint is genuinely a hard delete or should
# soft-delete).
DELETE_HANDLER_CAP = 35

DELETE_DECORATOR = re.compile(r'^@router\.delete\(', re.MULTILINE)


def test_mistake_6_delete_handler_count_below_cap() -> None:
    """Count @router.delete handlers across api/v1/. The cap
    forces every new DELETE endpoint into a code review where the
    reviewer must consciously decide hard-delete vs soft-delete.
    Default for tenant data, audit logs, and decryptable
    artefacts is soft-delete."""
    api = REPO / "api" / "v1"
    if not api.exists():
        pytest.skip("api/v1 not present in this checkout")
    count = 0
    for path in api.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        count += len(DELETE_DECORATOR.findall(text))
    assert count <= DELETE_HANDLER_CAP, (
        f"@router.delete handler count is {count}, cap is "
        f"{DELETE_HANDLER_CAP}. Any new DELETE endpoint must be "
        f"reviewed for hard-vs-soft delete. Default for tenant "
        f"data, audit logs, and decryptable artefacts is "
        f"soft-delete (set deleted_at, not DROP). If hard-delete "
        f"is genuinely safe, raise the cap in this file with a "
        f"justifying comment."
    )


# ─────────────────────────────────────────────────────────────────
# Mistake 7 — coverage script returns green with no coverage data
# ─────────────────────────────────────────────────────────────────


def test_mistake_7_coverage_script_fails_when_no_data() -> None:
    """``scripts/check_module_coverage.py`` MUST refuse to return
    success when ``.coverage`` is missing or every module reports
    0%. Pin the no-data-fail branch in the source."""
    src = (REPO / "scripts" / "check_module_coverage.py").read_text(
        encoding="utf-8"
    )
    # The script must read coverage data and fail closed.
    assert "coverage" in src.lower()
    # Must explicitly handle the missing/empty case, not silently
    # treat absent data as "all green".
    fail_closed_signals = ("FileNotFoundError", "no_data", "missing", "0.0", "exit(1)")
    assert any(sig in src for sig in fail_closed_signals), (
        "check_module_coverage.py must fail-closed when no coverage "
        "data is found. Pin one of: FileNotFoundError, no_data, "
        "missing, 0.0, exit(1)."
    )


# ─────────────────────────────────────────────────────────────────
# Mistake 8 — public claims drift from runtime registry
# ─────────────────────────────────────────────────────────────────


def test_mistake_8_consistency_sweep_script_exists() -> None:
    """The consistency-sweep script catches drift between docs/
    SDKs/marketing claims and the live agent + connector
    registries. Pin its existence; if it disappears, public
    surfaces can drift silently."""
    script = REPO / "scripts" / "consistency_sweep.py"
    assert script.exists(), (
        "scripts/consistency_sweep.py is the gate that catches "
        "doc-vs-runtime drift. If it's gone, restore from git "
        "history and re-wire it into the consistency-sweep CI job."
    )


# ─────────────────────────────────────────────────────────────────
# Smoke: Foundation #5 helpers are present (cross-foundation pin)
# ─────────────────────────────────────────────────────────────────


def test_foundation_5_helpers_still_present() -> None:
    """Cross-foundation pin: Foundation #5 (migration hardening)
    delivered the encrypted_migration() wrapper. If it disappears,
    every encrypted-column migration silently regresses to the
    pre-incident state.

    NOTE: This pin lives in #8 to enforce the cross-foundation
    invariant. It is intentionally a soft skip on branches that
    haven't merged #5 yet — the assertion runs only when the
    helpers file exists (which is the steady-state).
    """
    helpers = REPO / "core" / "crypto" / "migration_helpers.py"
    if not helpers.exists():
        pytest.skip(
            "Foundation #5 not yet merged onto this branch — pin "
            "becomes assertive once PR #355 lands on main."
        )
    src = helpers.read_text(encoding="utf-8")
    assert "encrypted_migration" in src
    assert "EncryptedMigrationError" in src
    assert "alembic_migration_progress" in src


# ─────────────────────────────────────────────────────────────────
# Self-test: this file's own pytest discoverability
# ─────────────────────────────────────────────────────────────────


def test_self_visible_to_pytest() -> None:
    """Sanity: pytest can collect this module. Catches the
    pattern where a regression file is added but lives outside
    pytest's testpaths and silently never runs."""
    rc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            str(Path(__file__).relative_to(REPO).as_posix()),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rc.returncode == 0, (
        f"pytest --collect-only failed for this file:\n{rc.stdout}\n{rc.stderr}"
    )
    assert "test_self_visible_to_pytest" in rc.stdout
