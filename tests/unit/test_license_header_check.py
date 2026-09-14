# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_license_headers as check


def _git(repo: Path, *args: str) -> str:
    argv = ["git", "-C", str(repo), *args]
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603, S607


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "ci@example.com")
    _git(tmp_path, "config", "user.name", "ci")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "existing.py").write_text("print('no header, predates the check')\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _commit(repo: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")


def _run(repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = check.main(["--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_new_source_file_with_header_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"core/new_module.py": "# SPDX-License-Identifier: Apache-2.0\nVALUE = 1\n"})

    code, _ = _run(repo, capsys)

    assert code == 0


def test_new_source_file_without_header_fails_and_is_named(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"core/new_module.py": "VALUE = 1\n", "ui/src/Thing.tsx": "export const x = 1;\n"})

    code, out = _run(repo, capsys)

    assert code == 1
    assert "core/new_module.py" in out
    assert "ui/src/Thing.tsx" in out


def test_header_after_shebang_is_accepted(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"scripts/tool.sh": "#!/usr/bin/env bash\n# SPDX-License-Identifier: Apache-2.0\necho ok\n"})

    code, _ = _run(repo, capsys)

    assert code == 0


def test_header_below_the_first_lines_is_rejected(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = "\n".join(["x = 1"] * (check.HEADER_WINDOW + 1)) + "\n# SPDX-License-Identifier: Apache-2.0\n"
    _commit(repo, {"core/late_header.py": body})

    code, out = _run(repo, capsys)

    assert code == 1
    assert "core/late_header.py" in out


def test_modified_existing_file_is_not_required_to_gain_a_header(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"existing.py": "print('edited')\n"})

    code, _ = _run(repo, capsys)

    assert code == 0


def test_non_source_and_excluded_files_are_ignored(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(
        repo,
        {
            "docs/page.md": "# Title\n",
            "config/settings.yaml": "a: 1\n",
            "ui/src/types.d.ts": "declare const x: number;\n",
            "tests/cassettes/case/abc.json": "{}\n",
            "core/pkg/__init__.py": "",
        },
    )

    code, _ = _run(repo, capsys)

    assert code == 0


def test_unresolvable_base_fails_closed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = check.main(["--base", "no-such-ref", "--head", "HEAD", "--repo", str(repo)])

    assert code == 2
    assert "no-such-ref" in capsys.readouterr().err
