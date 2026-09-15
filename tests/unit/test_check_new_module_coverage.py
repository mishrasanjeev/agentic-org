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


def test_absolute_paths_under_a_container_source_are_mapped(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _add(repo, {"core/new.py": "a = 1\n"})
    _coverage(repo, {"/src/core/new.py": [(1, 1)]}, source="/src")
    code, out, _ = _run(repo, capsys)
    assert code == 0, out


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
