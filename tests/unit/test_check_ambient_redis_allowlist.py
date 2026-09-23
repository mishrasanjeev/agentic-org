# SPDX-License-Identifier: Apache-2.0
"""The ambient-Redis allowlist only shrinks (scripts/check_ambient_redis_allowlist.py)."""

from __future__ import annotations

import importlib.util
import runpy
import subprocess
import sys
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


def test_empty_redis_url_does_not_disable_ambient_socket_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.test_doubles.ambient_redis_policy import declared_redis_url

    monkeypatch.setenv("AGENTICORG_REDIS_URL", "")
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert declared_redis_url() is None


def test_empty_primary_redis_url_still_honors_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.test_doubles.ambient_redis_policy import declared_redis_url

    monkeypatch.setenv("AGENTICORG_REDIS_URL", "")
    monkeypatch.setenv("REDIS_URL", "redis://legacy.test:6379/0")

    assert declared_redis_url() == "redis://legacy.test:6379/0"


@pytest.mark.parametrize("redis_env", ["AGENTICORG_REDIS_URL", "REDIS_URL"])
def test_nonempty_redis_url_declares_ambient_infrastructure(
    monkeypatch: pytest.MonkeyPatch, redis_env: str
) -> None:
    from core.test_doubles.ambient_redis_policy import declared_redis_url

    monkeypatch.delenv("AGENTICORG_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv(redis_env, "redis://explicit.test:6379/0")
    assert declared_redis_url() == "redis://explicit.test:6379/0"


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


def test_allowlist_here_reports_a_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    with pytest.raises(checker.AllowlistError, match="cannot be read"):
        checker.allowlist_here()


def test_merge_base_reports_git_ref_and_merge_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_ref(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "missing ref")

    monkeypatch.setattr(checker, "_git", missing_ref)
    with pytest.raises(checker.AllowlistError, match="does not name a commit"):
        checker.merge_base("missing")

    def no_common_base(*args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "cat-file":
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "unrelated history")

    monkeypatch.setattr(checker, "_git", no_common_base)
    with pytest.raises(checker.AllowlistError, match="no merge base"):
        checker.merge_base("unrelated")


def test_allowlist_at_handles_missing_and_unreadable_tree_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreadable_tree(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 128, "", "bad object")

    monkeypatch.setattr(checker, "_git", unreadable_tree)
    with pytest.raises(checker.AllowlistError, match="cannot be read"):
        checker.allowlist_at("missing")

    monkeypatch.setattr(
        checker,
        "_git",
        lambda *args: subprocess.CompletedProcess(args, 0, "", ""),
    )
    assert checker.allowlist_at("without-file") is None


def test_allowlist_at_fails_if_git_cannot_read_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable_file(*args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "ls-tree":
            return subprocess.CompletedProcess(args, 0, f"{checker.ALLOWLIST_FILE}\n", "")
        return subprocess.CompletedProcess(args, 128, "", "bad object")

    monkeypatch.setattr(checker, "_git", unreadable_file)
    with pytest.raises(checker.AllowlistError, match="cannot be read"):
        checker.allowlist_at("broken")


def test_allowlist_at_parses_the_committed_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def readable_file(*args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "ls-tree":
            return subprocess.CompletedProcess(args, 0, f"{checker.ALLOWLIST_FILE}\n", "")
        return subprocess.CompletedProcess(args, 0, "tests/unit/test_a.py\n", "")

    monkeypatch.setattr(checker, "_git", readable_file)
    assert checker.allowlist_at("commit") == frozenset({"tests/unit/test_a.py"})


def test_cli_entrypoint_runs_against_the_current_commit() -> None:
    if checker._git("rev-parse", "--verify", "HEAD").returncode != 0:
        pytest.skip("this tools container has no Git metadata; CI checks the entrypoint")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--base", "HEAD"])
    try:
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        assert exc_info.value.code == 0
    finally:
        monkeypatch.undo()


def test_the_comparison_is_against_the_merge_base_not_the_tip() -> None:
    """Another branch removing an entry must not read as this branch adding it."""
    import inspect

    source = inspect.getsource(checker.main)
    assert "merge_base(args.base)" in source
    assert "allowlist_at(base)" in source


def test_push_check_uses_exact_pre_push_commit() -> None:
    import inspect

    workflow = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )
    assert 'mode=(--exact-base)' in workflow
    assert 'python scripts/check_ambient_redis_allowlist.py --base "$base" "${mode[@]}"' in workflow
    assert "exact_commit(args.base) if args.exact_base else merge_base(args.base)" in inspect.getsource(checker.main)


def test_exact_base_does_not_use_merge_base(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = frozenset({"tests/a.py"})
    seen: list[str] = []
    monkeypatch.setattr(checker, "allowlist_here", lambda: entries)
    monkeypatch.setattr(checker, "exact_commit", lambda ref: "pre-push-sha")
    monkeypatch.setattr(checker, "merge_base", lambda _ref: pytest.fail("must use exact base"))
    monkeypatch.setattr(checker, "allowlist_at", lambda ref: seen.append(ref) or entries)

    assert checker.main(["--base", "pre-push-sha", "--exact-base"]) == 0
    assert seen == ["pre-push-sha"]


def test_exact_commit_refuses_an_empty_ref() -> None:
    with pytest.raises(checker.AllowlistError, match="--base is empty"):
        checker.exact_commit("")


def test_exact_commit_fails_closed_when_git_cannot_resolve_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        checker,
        "_git",
        lambda *args: subprocess.CompletedProcess(args, 1, "", "missing ref"),
    )

    with pytest.raises(checker.AllowlistError, match="does not name a commit"):
        checker.exact_commit("missing")


def test_exact_commit_returns_the_verified_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    def verified_ref(*args: str) -> subprocess.CompletedProcess[str]:
        assert args == ("rev-parse", "--verify", "before-sha^{commit}")
        return subprocess.CompletedProcess(args, 0, "resolved-before-sha\n", "")

    monkeypatch.setattr(checker, "_git", verified_ref)

    assert checker.exact_commit("before-sha") == "resolved-before-sha"


def test_push_baseline_guards_use_the_pre_push_commit() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count('PUSH_BEFORE: ${{ github.event.before }}') == 2
    assert workflow.count('base="$PUSH_BEFORE"') == 2
    assert workflow.count(
        'if [[ "$GITHUB_EVENT_NAME" == "push" && "$GITHUB_REF_TYPE" == "branch" ]]; then'
    ) == 2


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
