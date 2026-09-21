# SPDX-License-Identifier: Apache-2.0
"""The ambient-Redis allowlist only shrinks (scripts/check_ambient_redis_allowlist.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_ambient_redis_allowlist.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("check_ambient_redis_allowlist", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


checker = _load()


def test_the_committed_allowlist_parses_and_names_test_files() -> None:
    entries = checker.allowlist_here()
    assert entries, "the committed allowlist is empty; update this test if that is deliberate"
    assert all(entry.startswith("tests/") and entry.endswith(".py") for entry in entries)


def test_comments_and_blank_lines_are_ignored() -> None:
    text = "# a comment\n\ntests/unit/test_a.py\ntests/unit/test_b.py  # why\n"
    assert checker.parse_allowlist(text) == frozenset(
        {"tests/unit/test_a.py", "tests/unit/test_b.py"}
    )


def test_an_added_entry_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "allowlist_here", lambda: frozenset({"a.py", "b.py"}))
    monkeypatch.setattr(checker, "allowlist_at", lambda ref: frozenset({"a.py"}))  # noqa: ARG005
    assert checker.main(["--base", "origin/main"]) == 1


@pytest.mark.parametrize(
    ("here", "there"),
    [
        (frozenset({"a.py"}), frozenset({"a.py"})),
        (frozenset(), frozenset({"a.py"})),
        (frozenset({"a.py"}), frozenset({"a.py", "b.py"})),
    ],
)
def test_an_unchanged_or_shrunken_allowlist_passes(
    monkeypatch: pytest.MonkeyPatch, here: frozenset[str], there: frozenset[str]
) -> None:
    monkeypatch.setattr(checker, "allowlist_here", lambda: here)
    monkeypatch.setattr(checker, "allowlist_at", lambda ref: there)  # noqa: ARG005
    assert checker.main(["--base", "origin/main"]) == 0


def test_a_base_without_the_file_is_no_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "allowlist_here", lambda: frozenset({"a.py"}))
    monkeypatch.setattr(checker, "allowlist_at", lambda ref: None)  # noqa: ARG005
    assert checker.main(["--base", "origin/main"]) == 0


def test_an_unreadable_allowlist_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> frozenset[str]:
        raise checker.AllowlistError("no such file")

    monkeypatch.setattr(checker, "allowlist_here", _boom)
    assert checker.main(["--base", "origin/main"]) == 2


def test_an_unresolvable_base_ref_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "allowlist_here", lambda: frozenset({"a.py"}))

    def _boom(ref: str) -> frozenset[str]:
        raise checker.AllowlistError(f"{ref} is not a ref")

    monkeypatch.setattr(checker, "allowlist_at", _boom)
    assert checker.main(["--base", "nope"]) == 2
