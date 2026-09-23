# SPDX-License-Identifier: Apache-2.0
"""The cross-loop ratchet baseline only moves down (scripts/check_cross_loop_baseline.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_cross_loop_baseline.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("check_cross_loop_baseline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


checker = _load()


def test_the_committed_baseline_parses() -> None:
    assert checker.baseline_here() >= 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("54\n", 54),
        ("54  # trips tolerated\n# more commentary\n", 54),
        ("0 # clean\n", 0),
    ],
)
def test_the_count_is_read_from_the_first_line(text: str, expected: int) -> None:
    assert checker.parse_baseline(text, "test") == expected


@pytest.mark.parametrize("text", ["", "# only a comment\n", "fifty-four\n"])
def test_a_baseline_without_a_number_is_refused(text: str) -> None:
    with pytest.raises(checker.BaselineError):
        checker.parse_baseline(text, "test")


def test_a_raised_baseline_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "baseline_here", lambda: 60)
    monkeypatch.setattr(checker, "baseline_at", lambda ref: 54)  # noqa: ARG005
    assert checker.main(["--base", "origin/main"]) == 1


@pytest.mark.parametrize(("here", "there"), [(54, 54), (40, 54), (0, 1)])
def test_an_unchanged_or_lowered_baseline_passes(
    monkeypatch: pytest.MonkeyPatch, here: int, there: int
) -> None:
    monkeypatch.setattr(checker, "baseline_here", lambda: here)
    monkeypatch.setattr(checker, "baseline_at", lambda ref: there)  # noqa: ARG005
    assert checker.main(["--base", "origin/main"]) == 0


def test_an_unreadable_baseline_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> int:
        raise checker.BaselineError("no such file")

    monkeypatch.setattr(checker, "baseline_here", _boom)
    assert checker.main(["--base", "origin/main"]) == 2
