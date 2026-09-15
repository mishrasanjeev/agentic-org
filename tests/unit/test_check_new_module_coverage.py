# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_new_module_coverage as gate


def _git(repo: Path, *args: str) -> str:
    argv = ["git", "-C", str(repo), *args]
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603, S607


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "ci@example.com")
    _git(root, "config", "user.name", "ci")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "core").mkdir()
    (root / "core" / "existing.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _add(repo: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")


def _coverage(repo: Path, files: dict[str, list[tuple[int, int]]], source: str | None = None) -> Path:
    classes = "".join(
        f'<class name="{Path(name).stem}" filename="{name}"><lines>'
        + "".join(f'<line number="{n}" hits="{h}"/>' for n, h in lines)
        + "</lines></class>"
        for name, lines in files.items()
    )
    xml = (
        f'<?xml version="1.0" ?><coverage version="7"><sources><source>{source or repo}</source></sources>'
        f'<packages><package name="p"><classes>{classes}</classes></package></packages></coverage>'
    )
    path = repo / "coverage.xml"
    path.write_text(xml)
    return path


def _run(repo: Path, capsys: pytest.CaptureFixture[str], floor: str = "75") -> tuple[int, str, str]:
    code = gate.main(["--repo", str(repo), "--coverage-xml", str(repo / "coverage.xml"),
                      "--base", "HEAD~1", "--head", "HEAD", "--floor", floor])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_new_module_at_or_above_the_floor_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _add(repo, {"core/new.py": "a = 1\nb = 2\nc = 3\nd = 4\n"})
    _coverage(repo, {"core/new.py": [(1, 1), (2, 1), (3, 1), (4, 0)]})
    code, out, _ = _run(repo, capsys)
    assert code == 0
    assert "ok    core/new.py: 75.0% (3/4 lines)" in out


def test_new_module_below_the_floor_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _add(repo, {"core/new.py": "a = 1\nb = 2\n"})
    _coverage(repo, {"core/new.py": [(1, 1), (2, 0)]})
    code, out, _ = _run(repo, capsys)
    assert code == 1
    assert "FAIL  core/new.py: 50.0%" in out


def test_new_module_never_imported_counts_as_zero(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _add(repo, {"tools/unused.py": "def f():\n    return 1\n"})
    _coverage(repo, {"core/existing.py": [(1, 1)]})
    code, out, _ = _run(repo, capsys)
    assert code == 1
    assert "tools/unused.py: 0.0% (not imported by any test)" in out


def test_docstring_only_module_is_skipped(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _add(repo, {"tools/__init__.py": '"""Package docstring."""\n'})
    _coverage(repo, {"core/existing.py": [(1, 1)]})
    code, out, _ = _run(repo, capsys)
    assert code == 0
    assert "skip  tools/__init__.py" in out


def test_tests_migrations_and_existing_modules_are_not_gated(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "core" / "existing.py").write_text("x = 1\ny = 2\n")
    _add(
        repo,
        {
            "tests/unit/test_new.py": "def test_x():\n    assert True\n",
            "migrations/versions/v1_new.py": "revision = 'v1'\n",
            "core/conftest.py": "x = 1\n",
        },
    )
    _coverage(repo, {"core/existing.py": [(1, 0), (2, 0)]})
    code, out, _ = _run(repo, capsys)
    assert code == 0
    assert "no new Python modules" in out


def test_absolute_filenames_inside_the_repository_are_mapped(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _add(repo, {"core/new.py": "a = 1\n"})
    _coverage(repo, {str(repo / "core" / "new.py"): [(1, 1)]})
    code, out, _ = _run(repo, capsys)
    assert code == 0, out


def test_sources_outside_the_repository_fail_closed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # A report written elsewhere (for example in a container at /src) cannot be
    # matched to files here without guessing, so the gate refuses it.
    _add(repo, {"core/new.py": "a = 1\n"})
    _coverage(repo, {"/src/core/new.py": [(1, 1)]}, source="/definitely-not-this-repo/src")
    code, _, err = _run(repo, capsys)
    assert code == 2
    assert "outside the repository" in err


def test_unresolvable_base_fails_closed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _coverage(repo, {})
    code = gate.main(["--repo", str(repo), "--coverage-xml", str(repo / "coverage.xml"), "--base", "nope"])
    assert code == 2
    assert "cannot resolve ref 'nope'" in capsys.readouterr().err


@pytest.mark.parametrize("content", [None, "<not-xml", "<?xml version='1.0'?><report/>",
                                     "<coverage><packages><package><classes><class><lines/></class>"
                                     "</classes></package></packages></coverage>"])
def test_missing_or_malformed_report_fails_closed(
    repo: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    _add(repo, {"core/new.py": "a = 1\n"})
    if content is not None:
        (repo / "coverage.xml").write_text(content)
    code, _, err = _run(repo, capsys)
    assert code == 2
    assert "check_new_module_coverage:" in err


# ── Overlapping coverage sources ───────────────────────────────────────────


def _overlapping_report(repo: Path, classes: dict[str, list[tuple[int, int]]]) -> None:
    body = "".join(
        f'<class name="{name}" filename="{name}"><lines>'
        + "".join(f'<line number="{n}" hits="{h}"/>' for n, h in lines)
        + "</lines></class>"
        for name, lines in classes.items()
    )
    sources = "".join(f"<source>{repo / sub}</source>" for sub in ("", "api", "core"))
    (repo / "coverage.xml").write_text(
        f'<?xml version="1.0" ?><coverage version="7"><sources>{sources}</sources>'
        f'<packages><package name="."><classes>{body}</classes></package></packages></coverage>'
    )


def test_overlapping_sources_resolve_unique_names_to_the_right_module(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _add(repo, {"core/fresh.py": "a = 1\nb = 2\n", "api/other.py": "c = 3\n"})
    # coverage.py wrote both names relative to the source that matched (core/, api/).
    _overlapping_report(repo, {"fresh.py": [(1, 1), (2, 1)], "other.py": [(1, 0)]})
    code, out, _ = _run(repo, capsys)
    assert code == 1
    assert "ok    core/fresh.py: 100.0%" in out
    assert "FAIL  api/other.py: 0.0%" in out


def test_overlapping_sources_with_an_ambiguous_name_fail_closed(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _add(repo, {"core/__init__.py": "x = 1\n", "api/__init__.py": "y = 2\n"})
    # Both packages' __init__.py appear as "__init__.py": merging them, or
    # picking one, would report the wrong module's coverage.
    _overlapping_report(repo, {"__init__.py": [(1, 1)]})
    code, _, err = _run(repo, capsys)
    assert code == 2
    assert "ambiguous" in err and "api/__init__.py" in err and "core/__init__.py" in err


def test_real_coverage_report_with_overlapping_sources_is_never_misattributed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = pytest.importorskip("coverage")
    root = tmp_path / "proj"
    for package, body in (("alphapkg", "def f():\n    return 1\n"), ("betapkg", "def g():\n    return 2\n")):
        (root / package).mkdir(parents=True)
        (root / package / "__init__.py").write_text("VALUE = 1\n")
        (root / package / "mod.py").write_text(body)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "ci@example.com")
    _git(root, "config", "user.name", "ci")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README").write_text("x\n")
    _git(root, "add", "README")
    _git(root, "commit", "-qm", "base")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "packages")

    import importlib
    import sys

    cov = coverage.Coverage(
        source=[str(root), str(root / "alphapkg"), str(root / "betapkg")], data_file=None, config_file=False
    )
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    try:
        cov.start()
        try:
            core_mod = importlib.import_module("alphapkg.mod")
            importlib.import_module("betapkg.mod")
            core_mod.f()
        finally:
            cov.stop()
        cov.xml_report(outfile=str(root / "coverage.xml"))
    finally:
        sys.path.remove(str(root))
        for name in ("alphapkg", "alphapkg.mod", "betapkg", "betapkg.mod"):
            sys.modules.pop(name, None)

    code = gate.main(["--repo", str(root), "--coverage-xml", str(root / "coverage.xml"),
                      "--base", "HEAD~1", "--head", "HEAD", "--floor", "75"])
    out, err = capsys.readouterr()
    if code == 2:
        assert "ambiguous" in err, err
    else:
        # Resolved without ambiguity: each module carries its own numbers.
        assert "alphapkg/mod.py: 100.0%" in out, out
        assert "betapkg/mod.py: 50.0%" in out, out


@pytest.mark.parametrize(
    ("name", "gated"),
    [
        ("core/test_helpers.py", True),
        ("core/tools/test_runner.py", True),
        ("tests/unit/helpers.py", False),
        ("services/api/tests/test_thing.py", False),
        ("core/test_doubles/fake_thing.py", False),
        ("connectors/providers/mock/fixtures/loader.py", False),
        ("migrations/versions/v9_new.py", False),
        ("core/conftest.py", False),
        ("core/schema.json", False),
    ],
)
def test_which_added_files_are_gated(name: str, gated: bool) -> None:
    assert gate.is_gated_module(name) is gated


def test_a_renamed_module_is_gated_as_added(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "core" / "old_name.py").write_text("a = 1\nb = 2\nc = 3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "old")
    _git(repo, "mv", "core/old_name.py", "core/new_name.py")
    _git(repo, "commit", "-qm", "rename")
    _coverage(repo, {"core/new_name.py": [(1, 0), (2, 0), (3, 0)]})
    code, out, _ = _run(repo, capsys)
    assert code == 1
    assert "core/new_name.py: 0.0%" in out
