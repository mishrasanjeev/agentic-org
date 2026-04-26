"""Tests for the hardened coverage-gate enforcement script.

Foundation work item #2 from the 2026-04-26 user directive: the coverage
gate must fail-closed when:
- .coverage doesn't exist
- .coverage is empty / corrupted
- every configured module reports n/a (test process never touched the code)

These tests run the script as a subprocess against synthetic .coverage
fixtures so the failure surfaces are real (exit codes, stderr) rather
than mocked.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_module_coverage.py"


def _run_in(cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the gate script with COVERAGE_GATE_ROOT pointing at cwd."""
    env = {**__import__("os").environ, "COVERAGE_GATE_ROOT": str(cwd)}
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_exit_2_when_no_coverage_file(tmp_path: Path) -> None:
    """No .coverage AND no coverage.xml in the working dir → exit 2.

    This is the case the user explicitly called out: 'coverage script
    returns green with no coverage data' must NOT happen.
    """
    proc = _run_in(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "no .coverage or coverage.xml" in proc.stderr


def test_exit_3_when_coverage_file_empty(tmp_path: Path) -> None:
    """Empty .coverage = test process crashed mid-write → exit 3."""
    (tmp_path / ".coverage").write_bytes(b"")
    proc = _run_in(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "empty" in proc.stderr.lower()


def test_exit_3_when_coverage_file_truncated(tmp_path: Path) -> None:
    """Suspiciously small .coverage (< 64 bytes) = corrupted → exit 3."""
    (tmp_path / ".coverage").write_bytes(b"junk")
    proc = _run_in(tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "small" in proc.stderr.lower() or "empty" in proc.stderr.lower()


def test_passes_against_real_repo_coverage() -> None:
    """The current repo .coverage produces a sensible non-error result.

    Skips when there's no .coverage file in the repo (e.g. on a fresh
    clone before pytest has run). Doesn't pin a specific exit code or
    percentage — only checks that the script doesn't crash on real data.
    """
    if not (REPO / ".coverage").exists():
        pytest.skip(
            ".coverage missing from repo root — run `pytest --cov=...` first"
        )
    proc = _run_in(REPO)
    # Any of: 0 (all met), 1 (below floor), but NEVER 2/3/4/5.
    assert proc.returncode in (0, 1), (
        f"unexpected exit code {proc.returncode} on real repo coverage:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_artifact_written_on_success() -> None:
    """When the gate passes, coverage_report.json is written for the
    release-acceptance gate (item #9 of the closure plan)."""
    if not (REPO / ".coverage").exists():
        pytest.skip(".coverage missing")
    proc = _run_in(REPO)
    if proc.returncode != 0:
        pytest.skip(
            f"gate did not pass on this repo state (exit {proc.returncode}); "
            "artifact-write check requires a passing run"
        )
    assert (REPO / "coverage_report.json").exists(), (
        "coverage_report.json must be written when the gate passes"
    )
