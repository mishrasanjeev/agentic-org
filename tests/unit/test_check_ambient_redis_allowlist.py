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
    monkeypatch.setattr(checker, "merge_base", lambda ref: "0" * 40)  # noqa: ARG005
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
    monkeypatch.setattr(checker, "merge_base", lambda ref: "0" * 40)  # noqa: ARG005
    monkeypatch.setattr(checker, "allowlist_at", lambda ref: there)  # noqa: ARG005
    assert checker.main(["--base", "origin/main"]) == 0


def test_a_base_without_the_file_is_no_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "allowlist_here", lambda: frozenset({"a.py"}))
    monkeypatch.setattr(checker, "merge_base", lambda ref: "0" * 40)  # noqa: ARG005
    monkeypatch.setattr(checker, "allowlist_at", lambda ref: None)  # noqa: ARG005
    assert checker.main(["--base", "origin/main"]) == 0


def test_an_unreadable_allowlist_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> frozenset[str]:
        raise checker.AllowlistError("no such file")

    monkeypatch.setattr(checker, "allowlist_here", _boom)
    assert checker.main(["--base", "origin/main"]) == 2


def test_an_unresolvable_base_ref_fails_closed() -> None:
    """Resolution is by exit code, not by matching git's prose."""
    assert checker.main(["--base", "nope-not-a-ref"]) == 2


def test_an_empty_base_is_refused() -> None:
    """`git show ":path"` reads the index, so an empty base would pass vacuously."""
    assert checker.main(["--base", ""]) == 2


def test_the_comparison_is_against_the_merge_base_not_the_tip() -> None:
    """Another branch removing an entry must not read as this branch adding it."""
    import inspect

    source = inspect.getsource(checker.main)
    assert "merge_base(args.base)" in source
    assert "allowlist_at(base)" in source


def test_an_entry_removed_on_the_base_branch_does_not_accuse_this_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The branch carries what the merge base carried; the base branch has since
    # dropped one. Comparing with the tip would call that an addition.
    merge_base_entries = frozenset({"a.py", "b.py"})
    monkeypatch.setattr(checker, "allowlist_here", lambda: merge_base_entries)
    monkeypatch.setattr(checker, "merge_base", lambda ref: "0" * 40)  # noqa: ARG005
    monkeypatch.setattr(checker, "allowlist_at", lambda ref: merge_base_entries)  # noqa: ARG005
    assert checker.main(["--base", "origin/main"]) == 0
