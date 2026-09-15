# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts import run_pip_audit as audit

TODAY = dt.date(2026, 9, 15)
REPO_ROOT = Path(__file__).resolve().parents[2]


def report(*vulnerable: tuple[str, str, str, list[str]]) -> str:
    dependencies = [{"name": "safe-package", "version": "1.0", "vulns": []}]
    dependencies += [
        {"name": name, "version": version, "vulns": [{"id": vid, "aliases": aliases, "fix_versions": ["9.9"]}]}
        for name, version, vid, aliases in vulnerable
    ]
    return json.dumps({"dependencies": dependencies, "fixes": []})


class FakeRunner:
    def __init__(self, code: int, stdout: str, stderr: str = "") -> None:
        self.result = (code, stdout, stderr)
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        return self.result


def exceptions_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "exceptions.toml"
    path.write_text(body, encoding="utf-8")
    return path


EXCEPTION = """
[[exception]]
id = "CVE-2026-0001"
package = "Example-Lib"
reason = "Only the unaffected encoder is used."
owner = "@maintainer"
expires = 2026-10-01
"""


def run(tmp_path: Path, runner: FakeRunner, body: str = "", capsys: pytest.CaptureFixture[str] | None = None) -> int:
    path = exceptions_file(tmp_path, body)
    return audit.main(["--exceptions", str(path), "--", "-r", "requirements.txt"], runner=runner, today=TODAY)


def test_clean_audit_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner(0, report())
    assert run(tmp_path, runner) == 0
    assert runner.calls[0][-2:] == ["-r", "requirements.txt"]
    assert "--format" in runner.calls[0] and "json" in runner.calls[0]
    assert "no unaccepted vulnerabilities" in capsys.readouterr().out


def test_unexcepted_vulnerability_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner(1, report(("example-lib", "1.2", "GHSA-aaaa-bbbb-cccc", ["CVE-2026-0002"])))
    assert run(tmp_path, runner, EXCEPTION) == 1
    out = capsys.readouterr().out
    assert "example-lib 1.2: GHSA-aaaa-bbbb-cccc (CVE-2026-0002); fixed in 9.9" in out


def test_exception_matches_an_alias_and_the_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner(1, report(("example-lib", "1.2", "GHSA-aaaa-bbbb-cccc", ["CVE-2026-0001"])))
    assert run(tmp_path, runner, EXCEPTION) == 0
    assert "accepted  example-lib 1.2 GHSA-aaaa-bbbb-cccc (until 2026-10-01, @maintainer)" in capsys.readouterr().out


def test_exception_for_another_package_does_not_apply(tmp_path: Path) -> None:
    runner = FakeRunner(1, report(("other-lib", "3.0", "CVE-2026-0001", [])))
    assert run(tmp_path, runner, EXCEPTION) == 1


def test_unused_exception_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, FakeRunner(0, report()), EXCEPTION) == 0
    assert "matched nothing" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (EXCEPTION.replace("2026-10-01", "2026-09-14"), "expired on 2026-09-14"),
        (EXCEPTION.replace("2026-10-01", "2027-06-01"), "more than 90 days"),
        (EXCEPTION.replace('owner = "@maintainer"\n', ""), "missing owner"),
        (EXCEPTION.replace("expires = 2026-10-01", 'expires = "soon"'), "must be a date"),
        ("[[exception]\nbroken", "cannot read"),
    ],
)
def test_invalid_exceptions_fail_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str, reason: str
) -> None:
    runner = FakeRunner(0, report())
    assert run(tmp_path, runner, body) == 2
    assert reason in capsys.readouterr().err
    assert runner.calls == [], "nothing is audited with an invalid exception list"


@pytest.mark.parametrize(
    ("code", "stdout"),
    [(2, ""), (0, "not json"), (1, report()), (0, json.dumps({"unexpected": []}))],
)
def test_audit_that_cannot_complete_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, stdout: str
) -> None:
    assert run(tmp_path, FakeRunner(code, stdout, "resolver error")) == 2
    assert "run_pip_audit:" in capsys.readouterr().err


def test_default_targets_cover_the_project_and_both_requirements_files(tmp_path: Path) -> None:
    runner = FakeRunner(0, report())
    path = exceptions_file(tmp_path, "")
    assert audit.main(["--exceptions", str(path)], runner=runner, today=TODAY) == 0
    assert [call[call.index("60") + 1:] for call in runner.calls] == [
        ["."], ["-r", "requirements.txt"], ["-r", "requirements-v4.txt"]
    ]


def test_committed_exception_list_is_valid() -> None:
    audit.load_exceptions(REPO_ROOT / "config" / "pip-audit-exceptions.toml", dt.datetime.now(dt.UTC).date())
