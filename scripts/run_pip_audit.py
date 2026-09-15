#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Dependency vulnerability audit with dated, reviewed exceptions.

Runs ``pip-audit`` over the project metadata and both requirements files (the
targets CI has always audited) and fails on any known vulnerability that is not
covered by an exception in ``config/pip-audit-exceptions.toml``.

An exception names the advisory id (or any of its aliases, such as a CVE), the
package, why the finding is accepted, who owns it and an expiry date no more
than 90 days ahead. Expired or malformed exceptions fail the audit, and so does
anything unexpected from ``pip-audit`` itself: this script never passes because
the audit could not run. See "Dependency audit exceptions" in CONTRIBUTING.md.

    python scripts/run_pip_audit.py                      # the default targets
    python scripts/run_pip_audit.py -- -r requirements.txt
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_EXCEPTIONS = Path("config/pip-audit-exceptions.toml")
DEFAULT_TARGETS: tuple[tuple[str, ...], ...] = ((".",), ("-r", "requirements.txt"), ("-r", "requirements-v4.txt"))
MAX_EXCEPTION_DAYS = 90
REQUIRED_FIELDS = ("id", "package", "reason", "owner", "expires")

Runner = Callable[[Sequence[str]], tuple[int, str, str]]


class AuditError(RuntimeError):
    """The audit could not be completed or its configuration is invalid."""


@dataclass(frozen=True)
class AuditException:
    id: str
    package: str
    reason: str
    owner: str
    expires: dt.date


@dataclass(frozen=True)
class Finding:
    target: str
    package: str
    version: str
    id: str
    aliases: tuple[str, ...]
    fix_versions: tuple[str, ...]


def load_exceptions(path: Path, today: dt.date) -> list[AuditException]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AuditError(f"cannot read {path}: {exc}") from exc
    entries = data.get("exception", [])
    if not isinstance(entries, list):
        raise AuditError(f"{path}: 'exception' must be an array of tables")
    exceptions = []
    problems = []
    for index, entry in enumerate(entries):
        where = f"{path} exception[{index}]"
        missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            problems.append(f"{where}: missing {', '.join(missing)}")
            continue
        expires = entry["expires"]
        if not isinstance(expires, dt.date) or isinstance(expires, dt.datetime):
            problems.append(f"{where}: 'expires' must be a date (YYYY-MM-DD)")
            continue
        if expires < today:
            problems.append(f"{where} ({entry['id']}) expired on {expires}; fix the dependency or renew the review")
            continue
        if (expires - today).days > MAX_EXCEPTION_DAYS:
            problems.append(f"{where} ({entry['id']}) expires more than {MAX_EXCEPTION_DAYS} days ahead")
            continue
        exceptions.append(
            AuditException(str(entry["id"]), str(entry["package"]).lower(), str(entry["reason"]),
                           str(entry["owner"]), expires)
        )
    if problems:
        raise AuditError("invalid audit exceptions:\n  " + "\n  ".join(problems))
    return exceptions


def _run_subprocess(argv: Sequence[str]) -> tuple[int, str, str]:
    result = subprocess.run(list(argv), capture_output=True, text=True, check=False)  # noqa: S603 - fixed argv
    return result.returncode, result.stdout, result.stderr


def audit_target(target: Sequence[str], runner: Runner) -> list[Finding]:
    label = " ".join(target)
    argv = [sys.executable, "-m", "pip_audit", "--format", "json", "--progress-spinner", "off",
            "--timeout", "60", *target]
    code, stdout, stderr = runner(argv)
    if code not in (0, 1):
        raise AuditError(f"pip-audit {label} failed (exit {code}): {stderr.strip()[-500:]}")
    try:
        report: Any = json.loads(stdout)
        dependencies = report["dependencies"] if isinstance(report, dict) else report
        findings = [
            Finding(
                target=label,
                package=str(dep["name"]).lower(),
                version=str(dep.get("version", "")),
                id=str(vuln["id"]),
                aliases=tuple(str(a) for a in vuln.get("aliases", [])),
                fix_versions=tuple(str(v) for v in vuln.get("fix_versions", [])),
            )
            for dep in dependencies
            for vuln in dep.get("vulns", [])
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise AuditError(f"pip-audit {label} produced unreadable output: {exc}") from exc
    if code == 1 and not findings:
        raise AuditError(f"pip-audit {label} reported a failure but no vulnerabilities: {stderr.strip()[-500:]}")
    return findings


def evaluate(
    targets: Sequence[Sequence[str]], exceptions: list[AuditException], runner: Runner
) -> tuple[list[Finding], list[tuple[Finding, AuditException]], list[AuditException]]:
    """Return (unexcepted findings, excepted findings, unused exceptions)."""
    blocking: list[Finding] = []
    accepted: list[tuple[Finding, AuditException]] = []
    used: set[AuditException] = set()
    for target in targets:
        for finding in audit_target(target, runner):
            ids = {finding.id, *finding.aliases}
            match = next((e for e in exceptions if e.id in ids and e.package == finding.package), None)
            if match is None:
                blocking.append(finding)
            else:
                accepted.append((finding, match))
                used.add(match)
    return blocking, accepted, [e for e in exceptions if e not in used]


def main(argv: list[str] | None = None, runner: Runner = _run_subprocess, today: dt.date | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exceptions", type=Path, default=DEFAULT_EXCEPTIONS)
    parser.add_argument("target", nargs="*", help="pip-audit target arguments after '--' (default: all targets)")
    args = parser.parse_args(argv)
    targets: Sequence[Sequence[str]] = [args.target] if args.target else DEFAULT_TARGETS
    try:
        exceptions = load_exceptions(args.exceptions, today or dt.datetime.now(dt.UTC).date())
        blocking, accepted, unused = evaluate(targets, exceptions, runner)
    except AuditError as exc:
        print(f"run_pip_audit: {exc}", file=sys.stderr)
        return 2
    for finding, exception in accepted:
        print(f"accepted  {finding.package} {finding.version} {finding.id} (until {exception.expires}, "
              f"{exception.owner}): {exception.reason}")
    for exception in unused:
        print(f"::warning::audit exception {exception.id} for {exception.package} matched nothing; remove it")
    if blocking:
        print("Vulnerable dependencies:")
        for finding in blocking:
            fix = ", ".join(finding.fix_versions) or "no fixed version"
            aliases = f" ({', '.join(finding.aliases)})" if finding.aliases else ""
            print(f"  {finding.package} {finding.version}: {finding.id}{aliases}; fixed in {fix} [{finding.target}]")
        print("Upgrade the dependency, or add a reviewed exception (CONTRIBUTING.md, 'Dependency audit exceptions').")
        return 1
    print(f"run_pip_audit: no unaccepted vulnerabilities in {len(targets)} target(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
