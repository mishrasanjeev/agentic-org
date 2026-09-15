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


# ── Review follow-ups: unaudited dependencies and malformed entries ─────────


def report_with_skip(name: str = "local-only-lib", reason: str = "Dependency not found on PyPI") -> str:
    return json.dumps(
        {"dependencies": [{"name": "safe-package", "version": "1.0", "vulns": []},
                          {"name": name, "skip_reason": reason}], "fixes": []}
    )


def test_dependency_pip_audit_could_not_audit_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, FakeRunner(0, report_with_skip())) == 1
    out = capsys.readouterr().out
    assert "could not audit" in out
    assert "local-only-lib: Dependency not found on PyPI" in out


def test_reviewed_skip_entry_accepts_an_unaudited_dependency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = """
[[skip]]
package = "Local_Only.Lib"
reason = "Built from this repository; audited through its own requirements."
owner = "@maintainer"
expires = 2026-10-01
"""
    assert run(tmp_path, FakeRunner(0, report_with_skip()), body) == 0
    assert "accepted  local-only-lib (unaudited)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ('exception = ["CVE-2026-0001"]\n', "must be a table"),
        ('exception = "CVE-2026-0001"\n', "must be an array of tables"),
        (EXCEPTION.replace('id = "CVE-2026-0001"', "id = 1"), "id must be non-empty text"),
        (EXCEPTION.replace('package = "Example-Lib"', 'package = ["a", "b"]'), "package must be non-empty text"),
        (EXCEPTION.replace('reason = "Only the unaffected encoder is used."', 'reason = "  "'),
         "reason must be non-empty text"),
        (EXCEPTION + 'severity = "low"\n', "unknown field(s) severity"),
        (EXCEPTION + EXCEPTION, "duplicates an earlier entry"),
        ('[exceptions]\nid = "x"\n', "unknown top-level key(s) exceptions"),
        ("[[skip]]\npackage = \"x\"\n", "missing reason, owner, expires"),
        (EXCEPTION.replace("expires = 2026-10-01", "expires = 2026-10-01T00:00:00"), "must be a date"),
    ],
)
def test_malformed_entries_fail_with_a_message_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str, reason: str
) -> None:
    runner = FakeRunner(0, report())
    assert run(tmp_path, runner, body) == 2
    err = capsys.readouterr().err
    assert reason in err
    assert "Traceback" not in err
    assert runner.calls == []


def test_non_utf8_exception_file_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "exceptions.toml"
    path.write_bytes(b"\xff\xfe[[exception]]\n")
    assert audit.main(["--exceptions", str(path)], runner=FakeRunner(0, report()), today=TODAY) == 2
    assert "cannot read" in capsys.readouterr().err
