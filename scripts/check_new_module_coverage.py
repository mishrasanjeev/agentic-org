#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Coverage floor for Python modules a change adds.

Complements ``diff-cover`` (which gates changed lines that appear in the
coverage report): every Python module *added* between ``--base`` and
``--head`` must reach ``--floor`` percent line coverage in ``coverage.xml``. A
new module missing from the report was never imported by the tests and counts
as 0%, unless it has no executable statements (a docstring-only
``__init__.py``, for example).

Not gated: anything under a ``tests``, ``test``, ``test_doubles`` or
``fixtures`` directory, ``conftest.py`` files and Alembic revisions under
``migrations/``. A ``test_*.py`` file anywhere else is an ordinary module.
Renames count as additions (``--no-renames``), so a moved module must meet the
floor too.

Each ``filename`` in the report is resolved through the report's ``<source>``
directories to a repository-relative path. coverage.py writes a filename
relative to whichever source matched, and with overlapping sources (``.``
together with ``core``) that choice varies between runs, so ``__init__.py``
can mean several files. A filename that resolves to no file, or to more than
one, fails the gate instead of being attributed to the wrong module; run the
tests with a single coverage source (``make test`` does).

Fails closed (exit 2) when git fails, a ref does not resolve, the coverage
report is missing, unreadable or ambiguous, or a source lies outside the
repository. Exit 1 lists the modules below the floor.

    python scripts/check_new_module_coverage.py --coverage-xml coverage.xml --base origin/main --floor 75
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from defusedxml import DefusedXmlException
from defusedxml import ElementTree

EXCLUDED_DIRECTORIES = frozenset({"tests", "test", "test_doubles", "fixtures"})
EXCLUDED_PREFIXES = ("migrations/",)
EXCLUDED_NAMES = frozenset({"conftest.py"})


class GateError(RuntimeError):
    """The gate cannot be evaluated; treat as a failure."""


@dataclass(frozen=True)
class ModuleCoverage:
    path: str
    covered: int
    statements: int

    @property
    def percent(self) -> float:
        return 100.0 if self.statements == 0 else 100.0 * self.covered / self.statements


def _git(repo: Path, *args: str) -> str:
    argv = ["git", "-C", str(repo), *args]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603, S607 - fixed argv
    except OSError as exc:
        raise GateError(f"cannot run git: {exc}") from exc
    if result.returncode != 0:
        raise GateError(f"git {args[0]} failed: {result.stderr.strip() or result.returncode}")
    return result.stdout


def _resolve(repo: Path, ref: str) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except GateError as exc:
        raise GateError(f"cannot resolve ref {ref!r}") from exc


def added_modules(repo: Path, base: str, head: str) -> list[str]:
    base_sha, head_sha = _resolve(repo, base), _resolve(repo, head)
    out = _git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=A", "-z", f"{base_sha}...{head_sha}")
    return sorted(name for name in out.split("\0") if name and is_gated_module(name))


def is_gated_module(name: str) -> bool:
    posix = PurePosixPath(name)
    if posix.suffix != ".py" or name.startswith(EXCLUDED_PREFIXES) or posix.name in EXCLUDED_NAMES:
        return False
    return not EXCLUDED_DIRECTORIES.intersection(posix.parts[:-1])


def read_coverage(path: Path, repo: Path) -> dict[str, ModuleCoverage]:
    """Line coverage per repository-relative path from a Cobertura report."""
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError, DefusedXmlException) as exc:
        raise GateError(f"cannot read coverage report {path}: {exc}") from exc
    if root.tag != "coverage":
        raise GateError(f"{path} is not a Cobertura coverage report")
    repo_root = repo.resolve()
    prefixes = _source_prefixes(root, repo_root, path)
    lines: dict[str, dict[int, bool]] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename")
        if not filename:
            raise GateError(f"{path}: a class entry has no filename")
        relative = resolve_filename(filename, prefixes, repo_root)
        seen = lines.setdefault(relative, {})
        for line in cls.iter("line"):
            number, hits = line.get("number"), line.get("hits")
            if number is None or hits is None or not number.isdigit() or not hits.isdigit():
                raise GateError(f"{path}: malformed line entry in {filename}")
            seen[int(number)] = seen.get(int(number), False) or int(hits) > 0
    return {
        name: ModuleCoverage(name, sum(1 for hit in hits.values() if hit), len(hits)) for name, hits in lines.items()
    }


def _source_prefixes(root: object, repo_root: Path, report: Path) -> list[PurePosixPath]:
    """Each ``<source>`` as a path relative to the repository root."""
    prefixes: list[PurePosixPath] = []
    for element in root.iter("source"):  # type: ignore[attr-defined]
        text = (element.text or "").strip()
        if not text:
            continue
        try:
            relative = Path(text).resolve().relative_to(repo_root)
        except (OSError, ValueError) as exc:
            raise GateError(
                f"{report}: coverage source {text} is outside the repository {repo_root}; "
                "run the gate where the tests ran"
            ) from exc
        prefixes.append(PurePosixPath(relative.as_posix()))
    if not prefixes:
        raise GateError(f"{report}: the report names no <source> directory")
    return prefixes


def resolve_filename(filename: str, prefixes: list[PurePosixPath], repo_root: Path) -> str:
    """The repository-relative path a report filename refers to; GateError if none or several."""
    normalised = filename.replace("\\", "/")
    if PurePosixPath(normalised).is_absolute() or Path(filename).is_absolute():
        try:
            return Path(filename).resolve().relative_to(repo_root).as_posix()
        except (OSError, ValueError) as exc:
            raise GateError(f"coverage path {filename} is outside the repository") from exc
    matches = {
        str(PurePosixPath(prefix, normalised))
        for prefix in prefixes
        if (repo_root / PurePosixPath(prefix, normalised)).is_file()
    }
    if not matches:
        raise GateError(f"coverage filename {filename!r} is not a file under any <source> directory")
    if len(matches) > 1:
        raise GateError(
            f"coverage filename {filename!r} is ambiguous ({', '.join(sorted(matches))}): the report was "
            "produced with overlapping coverage sources; run the tests with a single source such as --cov=."
        )
    return matches.pop()


def has_statements(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        raise GateError(f"cannot parse {path}: {exc}") from exc
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        body = body[1:]
    return bool(body)


def evaluate(repo: Path, coverage_xml: Path, base: str, head: str, floor: float) -> tuple[list[str], list[str]]:
    """Return (report lines, failures)."""
    modules = added_modules(repo, base, head)
    if not modules:
        return ["no new Python modules"], []
    coverage = read_coverage(coverage_xml, repo)
    report, failures = [], []
    for name in modules:
        entry = coverage.get(name)
        if entry is None:
            if not has_statements(repo / name):
                report.append(f"  skip  {name} (no executable statements)")
                continue
            entry = ModuleCoverage(name, 0, 1)
            detail = "not imported by any test"
        else:
            detail = f"{entry.covered}/{entry.statements} lines"
        verdict = "ok  " if entry.percent >= floor else "FAIL"
        line = f"  {verdict}  {name}: {entry.percent:.1f}% ({detail})"
        report.append(line)
        if entry.percent < floor:
            failures.append(line)
    return report, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--coverage-xml", type=Path, default=Path("coverage.xml"))
    parser.add_argument("--base", default=os.environ.get("BASE_REF", "origin/main"))
    parser.add_argument("--head", default=os.environ.get("HEAD_REF", "HEAD"))
    parser.add_argument("--floor", type=float, default=75.0)
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        report, failures = evaluate(args.repo, args.coverage_xml, args.base, args.head, args.floor)
    except GateError as exc:
        print(f"check_new_module_coverage: {exc}", file=sys.stderr)
        return 2
    print(f"New-module coverage floor {args.floor:g}%:")
    print("\n".join(report))
    if failures:
        print(f"{len(failures)} new module(s) below the floor; add tests for them.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
