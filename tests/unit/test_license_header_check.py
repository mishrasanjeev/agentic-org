# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_license_headers as check

HEADER_LINE = "# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0\n"
SDK_HEADER_LINE = "# SPDX-License-Identifier: Apache-2.0\n"


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
    _commit(repo, {"core/new_module.py": HEADER_LINE + "VALUE = 1\n"})

    code, _ = _run(repo, capsys)

    assert code == 0


def test_header_constants_name_the_platform_and_sdk_licences() -> None:
    assert check.HEADER == "SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0"
    assert check.SDK_HEADER == "SPDX-License-Identifier: Apache-2.0"
    assert check.SDK_PREFIXES == ("sdk/", "sdk-ts/", "mcp-server/")


def test_new_platform_file_with_the_apache_identifier_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # From 2026-10-21 the platform is PolyForm-Noncommercial-1.0.0; a new platform file that
    # copies an Apache-2.0 header (for example from an SDK file) must not pass.
    _commit(repo, {"core/copied_header.py": SDK_HEADER_LINE + "VALUE = 1\n"})

    code, out = _run(repo, capsys)

    assert code == 1
    assert "core/copied_header.py" in out
    assert check.HEADER in out


@pytest.mark.parametrize("path", ["sdk/agenticorg/new.py", "sdk-ts/src/new.ts", "mcp-server/src/new.ts"])
def test_new_sdk_file_needs_the_apache_identifier(
    repo: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    comment = "# " if path.endswith(".py") else "// "
    _commit(repo, {path: comment + "SPDX-License-Identifier: Apache-2.0\nexport const x = 1;\n"})

    code, _ = _run(repo, capsys)

    assert code == 0


@pytest.mark.parametrize("path", ["sdk/agenticorg/new.py", "sdk-ts/src/new.ts", "mcp-server/src/new.ts"])
def test_new_sdk_file_with_the_platform_identifier_fails(
    repo: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    comment = "# " if path.endswith(".py") else "// "
    _commit(repo, {path: comment + "SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0\nx = 1\n"})

    code, out = _run(repo, capsys)

    assert code == 1
    assert f"{path}: needs '{check.SDK_HEADER}'" in out


def test_directory_named_like_an_sdk_inside_the_platform_is_not_an_sdk(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"core/sdk/new.py": SDK_HEADER_LINE + "VALUE = 1\n"})

    code, out = _run(repo, capsys)

    assert code == 1
    assert "core/sdk/new.py" in out


def test_block_comment_header_is_accepted(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"ui/src/thing.js": "/* SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 */\nexport {};\n"})

    code, _ = _run(repo, capsys)

    assert code == 0


def test_new_source_file_without_header_fails_and_is_named(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"core/new_module.py": "VALUE = 1\n", "ui/src/Thing.tsx": "export const x = 1;\n"})

    code, out = _run(repo, capsys)

    assert code == 1
    assert "core/new_module.py" in out
    assert "ui/src/Thing.tsx" in out


def test_header_after_shebang_is_accepted(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"scripts/tool.sh": "#!/usr/bin/env bash\n" + HEADER_LINE + "echo ok\n"})

    code, _ = _run(repo, capsys)

    assert code == 0


def test_header_below_the_first_lines_is_rejected(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = "\n".join(["x = 1"] * (check.HEADER_WINDOW + 1)) + "\n" + HEADER_LINE
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
