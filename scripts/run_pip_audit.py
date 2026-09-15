#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Dependency vulnerability audit with dated, reviewed exceptions.

Runs ``pip-audit`` over the project metadata and both requirements files (the
targets CI has always audited) and fails on any known vulnerability that is not
covered by an exception in ``config/pip-audit-exceptions.toml``.

A dependency ``pip-audit`` could not audit (it reports a ``skip_reason``, for
example a package that is not on PyPI) fails the audit too, because nothing is
known about it; a reviewed ``[[skip]]`` entry accepts it.

Every entry names the package, why it is accepted, who owns it and an expiry
date no more than 90 days ahead; an ``[[exception]]`` also names the advisory id
(or any alias, such as a CVE). Expired, malformed, duplicated or unknown
entries fail the audit with a message rather than a traceback, and so does
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
TEXT_FIELDS = {"exception": ("id", "package", "reason", "owner"), "skip": ("package", "reason", "owner")}

Runner = Callable[[Sequence[str]], tuple[int, str, str]]


class AuditError(RuntimeError):
    """The audit could not be completed or its configuration is invalid."""


@dataclass(frozen=True)
class AuditException:
    kind: str  # "exception" (a vulnerability) or "skip" (a dependency pip-audit could not audit)
    id: str  # empty for "skip"
    package: str
    reason: str
    owner: str
    expires: dt.date

    @property
    def label(self) -> str:
        return f"{self.id} for {self.package}" if self.id else f"skip for {self.package}"


@dataclass(frozen=True)
class Finding:
    target: str
    package: str
    version: str
    id: str
    aliases: tuple[str, ...]
    fix_versions: tuple[str, ...]


@dataclass(frozen=True)
class Skipped:
    target: str
    package: str
    reason: str


def _normalise_package(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _entry_problems(entry: dict[str, Any], text_fields: Sequence[str], where: str) -> list[str]:
    problems = []
    unknown = sorted(set(entry) - {*text_fields, "expires"})
    missing = [name for name in (*text_fields, "expires") if name not in entry]
    wrong = [name for name in text_fields if name in entry and not (isinstance(entry[name], str) and entry[name].strip())]
    if unknown:
        problems.append(f"{where}: unknown field(s) {', '.join(unknown)}")
    if missing:
        problems.append(f"{where}: missing {', '.join(missing)}")
    if wrong:
        problems.append(f"{where}: {', '.join(wrong)} must be non-empty text")
    return problems


def load_exceptions(path: Path, today: dt.date) -> list[AuditException]:
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise AuditError(f"cannot read {path}: {exc}") from exc
    problems: list[str] = []
    unknown_tables = sorted(set(data) - set(TEXT_FIELDS))
    if unknown_tables:
        problems.append(f"{path}: unknown top-level key(s) {', '.join(unknown_tables)} (use [[exception]] or [[skip]])")
    exceptions: list[AuditException] = []
    seen: set[tuple[str, str, str]] = set()
    for kind, text_fields in TEXT_FIELDS.items():
        entries = data.get(kind, [])
        if not isinstance(entries, list):
            problems.append(f"{path}: '{kind}' must be an array of tables ([[{kind}]])")
            continue
        for index, entry in enumerate(entries):
            where = f"{path} {kind}[{index}]"
            if not isinstance(entry, dict):
                problems.append(f"{where}: must be a table")
                continue
            entry_problems = _entry_problems(entry, text_fields, where)
            if entry_problems:
                problems.extend(entry_problems)
                continue
            expires = entry["expires"]
            name = entry.get("id", entry["package"])
            if not isinstance(expires, dt.date) or isinstance(expires, dt.datetime):
                problems.append(f"{where}: 'expires' must be a date (YYYY-MM-DD)")
                continue
            if expires < today:
                problems.append(f"{where} ({name}) expired on {expires}; fix the dependency or renew the review")
                continue
            if (expires - today).days > MAX_EXCEPTION_DAYS:
                problems.append(f"{where} ({name}) expires more than {MAX_EXCEPTION_DAYS} days ahead")
                continue
            item = AuditException(
                kind=kind,
                id=entry.get("id", "").strip(),
                package=_normalise_package(entry["package"]),
                reason=entry["reason"].strip(),
                owner=entry["owner"].strip(),
                expires=expires,
            )
            key = (item.kind, item.id, item.package)
            if key in seen:
                problems.append(f"{where}: duplicates an earlier entry ({item.label})")
                continue
            seen.add(key)
            exceptions.append(item)
    if problems:
        raise AuditError("invalid audit exceptions:\n  " + "\n  ".join(problems))
    return exceptions


def _run_subprocess(argv: Sequence[str]) -> tuple[int, str, str]:
    result = subprocess.run(list(argv), capture_output=True, text=True, check=False)  # noqa: S603 - fixed argv
    return result.returncode, result.stdout, result.stderr


def audit_target(target: Sequence[str], runner: Runner) -> tuple[list[Finding], list[Skipped]]:
    label = " ".join(target)
    argv = [sys.executable, "-m", "pip_audit", "--format", "json", "--progress-spinner", "off",
            "--timeout", "60", *target]
    code, stdout, stderr = runner(argv)
    if code not in (0, 1):
        raise AuditError(f"pip-audit {label} failed (exit {code}): {stderr.strip()[-500:]}")
    findings: list[Finding] = []
    skipped: list[Skipped] = []
    try:
        report: Any = json.loads(stdout)
        dependencies = report["dependencies"] if isinstance(report, dict) else report
        if not isinstance(dependencies, list):
            raise TypeError("'dependencies' is not a list")
        for dep in dependencies:
            package = _normalise_package(str(dep["name"]))
            if "skip_reason" in dep:
                skipped.append(Skipped(label, package, str(dep["skip_reason"])))
                continue
            for vuln in dep.get("vulns", []):
                findings.append(
                    Finding(
                        target=label,
                        package=package,
                        version=str(dep.get("version", "")),
                        id=str(vuln["id"]),
                        aliases=tuple(str(a) for a in vuln.get("aliases", [])),
                        fix_versions=tuple(str(v) for v in vuln.get("fix_versions", [])),
                    )
                )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AuditError(f"pip-audit {label} produced unreadable output: {exc}") from exc
    if code == 1 and not findings:
        raise AuditError(f"pip-audit {label} reported a failure but no vulnerabilities: {stderr.strip()[-500:]}")
    return findings, skipped


@dataclass
class Outcome:
    blocking: list[Finding]
    unaudited: list[Skipped]
    accepted: list[tuple[Finding | Skipped, AuditException]]
    unused: list[AuditException]


def evaluate(targets: Sequence[Sequence[str]], exceptions: list[AuditException], runner: Runner) -> Outcome:
    outcome = Outcome([], [], [], [])
    used: set[AuditException] = set()
    for target in targets:
        findings, skipped = audit_target(target, runner)
        for finding in findings:
            ids = {finding.id, *finding.aliases}
            match = next(
                (e for e in exceptions if e.kind == "exception" and e.id in ids and e.package == finding.package),
                None,
            )
            if match is None:
                outcome.blocking.append(finding)
            else:
                outcome.accepted.append((finding, match))
                used.add(match)
        for skip in skipped:
            match = next((e for e in exceptions if e.kind == "skip" and e.package == skip.package), None)
            if match is None:
                outcome.unaudited.append(skip)
            else:
                outcome.accepted.append((skip, match))
                used.add(match)
    outcome.unused = [e for e in exceptions if e not in used]
    return outcome


def main(argv: list[str] | None = None, runner: Runner = _run_subprocess, today: dt.date | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exceptions", type=Path, default=DEFAULT_EXCEPTIONS)
    parser.add_argument("target", nargs="*", help="pip-audit target arguments after '--' (default: all targets)")
    args = parser.parse_args(argv)
    targets: Sequence[Sequence[str]] = [args.target] if args.target else DEFAULT_TARGETS
    try:
        exceptions = load_exceptions(args.exceptions, today or dt.datetime.now(dt.UTC).date())
        outcome = evaluate(targets, exceptions, runner)
    except AuditError as exc:
        print(f"run_pip_audit: {exc}", file=sys.stderr)
        return 2
    for item, exception in outcome.accepted:
        what = f"{item.package} {item.version} {item.id}" if isinstance(item, Finding) else f"{item.package} (unaudited)"
        print(f"accepted  {what} (until {exception.expires}, {exception.owner}): {exception.reason}")
    for exception in outcome.unused:
        print(f"::warning::audit {exception.kind} entry {exception.label} matched nothing; remove it")
    if outcome.blocking:
        print("Vulnerable dependencies:")
        for finding in outcome.blocking:
            fix = ", ".join(finding.fix_versions) or "no fixed version"
            aliases = f" ({', '.join(finding.aliases)})" if finding.aliases else ""
            print(f"  {finding.package} {finding.version}: {finding.id}{aliases}; fixed in {fix} [{finding.target}]")
    if outcome.unaudited:
        print("Dependencies pip-audit could not audit:")
        for skip in outcome.unaudited:
            print(f"  {skip.package}: {skip.reason} [{skip.target}]")
    if outcome.blocking or outcome.unaudited:
        print("Upgrade the dependency, or add a reviewed entry (CONTRIBUTING.md, 'Dependency audit exceptions').")
        return 1
    print(f"run_pip_audit: no unaccepted vulnerabilities or unaudited dependencies in {len(targets)} target(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
