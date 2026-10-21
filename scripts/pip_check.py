#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""``pip check`` that accepts exactly one known conflict: fastembed's Pillow ceiling.

fastembed is installed with ``--no-deps`` from ``requirements-fastembed.txt``
because its latest release declares ``pillow<13`` while ``pyproject.toml``
requires Pillow 13 for decoder security fixes. ``pip check`` therefore reports
that one requirement. This runs ``pip check`` and fails on anything else: a
missing package, any other broken requirement, or a fastembed Pillow
requirement that the installed Pillow fails for a reason other than being
newer than the ceiling (for example an older Pillow).

When a fastembed release accepts Pillow 13 and fastembed moves back into
``pyproject.toml``, replace calls to this script with ``python -m pip check``
and delete it.

    python scripts/pip_check.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Sequence

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

# pip's wording for a requirement the installed version does not satisfy.
_CONFLICT = re.compile(
    r"^(?P<package>\S+) (?P<version>\S+) has requirement (?P<requirement>.+), "
    r"but you have (?P<dependency>\S+) (?P<installed>\S+)\.$"
)
ACCEPTED_PACKAGE = "fastembed"
ACCEPTED_DEPENDENCY = "pillow"

Runner = Callable[[Sequence[str]], tuple[int, str]]


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def is_accepted(line: str) -> bool:
    """Whether a ``pip check`` line is fastembed's Pillow ceiling and nothing more."""
    match = _CONFLICT.match(line.strip())
    if match is None:
        return False
    if _normalise(match["package"]) != ACCEPTED_PACKAGE or _normalise(match["dependency"]) != ACCEPTED_DEPENDENCY:
        return False
    try:
        requirement = Requirement(match["requirement"])
        installed = Version(match["installed"])
    except (InvalidRequirement, InvalidVersion):
        return False
    if _normalise(requirement.name) != ACCEPTED_DEPENDENCY:
        return False
    # Accept only "too new": every lower bound holds and some upper bound fails.
    failing = [spec for spec in requirement.specifier if not spec.contains(installed, prereleases=True)]
    return bool(failing) and all(spec.operator in {"<", "<="} for spec in failing)


def _run_pip_check(argv: Sequence[str]) -> tuple[int, str]:
    result = subprocess.run(list(argv), capture_output=True, text=True, check=False)  # noqa: S603 - fixed argv
    return result.returncode, (result.stdout + result.stderr)


def check(runner: Runner = _run_pip_check) -> list[str]:
    """The ``pip check`` problems that are not the accepted conflict."""
    code, output = runner([sys.executable, "-m", "pip", "check"])
    lines = [line for line in output.splitlines() if line.strip()]
    if code == 0:
        return []
    problems = [line for line in lines if not is_accepted(line)]
    if not lines:
        problems = [f"pip check failed (exit {code}) without output"]
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("pip check found broken requirements:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("pip check: no broken requirements other than fastembed's Pillow ceiling")
    return 0


if __name__ == "__main__":
    sys.exit(main())
