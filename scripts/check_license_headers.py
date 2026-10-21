#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""CI guard: new source files must carry the SPDX licence identifier for their directory.

The repository has two licences (see LICENSING.md):

- the client packages ``sdk/``, ``sdk-ts/`` and ``mcp-server/`` are Apache-2.0, so a new
  source file there must contain ``SPDX-License-Identifier: Apache-2.0``;
- everything else is the AgenticOrg platform, licensed under the Orchestrum Personal Use
  License 1.0 (the root LICENSE), so a new source file must contain
  ``SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0``.

The identifier must appear within the first few lines (a shebang or encoding line may come
first). A file carrying the other directory's identifier fails. Files that existed before the
check are not required to gain a header when they are edited.

Fails closed: if either ref does not resolve or git fails, the check exits 2
rather than passing.

Run locally:

    python scripts/check_license_headers.py --base origin/main
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

PLATFORM_LICENSE = "LicenseRef-Orchestrum-Personal-Use-1.0"
SDK_LICENSE = "Apache-2.0"
HEADER = f"SPDX-License-Identifier: {PLATFORM_LICENSE}"
SDK_HEADER = f"SPDX-License-Identifier: {SDK_LICENSE}"
SDK_PREFIXES = ("sdk/", "sdk-ts/", "mcp-server/")
HEADER_WINDOW = 5
SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh"})
EXCLUDED_PREFIXES = ("tests/cassettes/",)
EXCLUDED_SUFFIXES = (".d.ts", ".min.js")


class GitError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    # Fixed git argv, no shell.
    argv = ["git", "-C", str(repo), *args]
    result = subprocess.run(argv, capture_output=True, text=True)  # noqa: S603, S607
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _resolve(repo: Path, ref: str) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except GitError as exc:
        raise GitError(f"cannot resolve ref '{ref}'") from exc


def added_files(repo: Path, base: str, head: str) -> list[str]:
    base_sha = _resolve(repo, base)
    head_sha = _resolve(repo, head)
    out = _git(repo, "diff", "--name-only", "--diff-filter=A", "-z", f"{base_sha}...{head_sha}")
    return [name for name in out.split("\0") if name]


def requires_header(path: str) -> bool:
    posix = PurePosixPath(path)
    if posix.suffix not in SOURCE_SUFFIXES:
        return False
    if path.startswith(EXCLUDED_PREFIXES) or path.endswith(EXCLUDED_SUFFIXES):
        return False
    return True


def required_header(path: str) -> str:
    """The SPDX line a new source file at ``path`` must carry."""
    return SDK_HEADER if path.startswith(SDK_PREFIXES) else HEADER


def _spdx_id(line: str) -> str | None:
    marker = "SPDX-License-Identifier:"
    if marker not in line:
        return None
    rest = line.split(marker, 1)[1].split()
    return rest[0].rstrip("*/") if rest else ""


def has_header(file_path: Path, header: str = HEADER) -> bool:
    expected = header.split(":", 1)[1].strip()
    with file_path.open(encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            if index >= HEADER_WINDOW:
                return False
            if _spdx_id(line) == expected:
                return True
    return False


def missing_headers(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    missing = []
    for name in added_files(repo, base, head):
        if not requires_header(name):
            continue
        file_path = repo / name
        if not file_path.is_file() or file_path.stat().st_size == 0:
            continue
        header = required_header(name)
        if not has_header(file_path, header):
            missing.append((name, header))
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=os.environ.get("BASE_REF", "origin/main"))
    parser.add_argument("--head", default=os.environ.get("HEAD_REF", "HEAD"))
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)

    try:
        missing = missing_headers(Path(args.repo), args.base, args.head)
    except GitError as exc:
        print(f"check_license_headers: {exc}", file=sys.stderr)
        return 2

    if missing:
        print(f"New source files without their SPDX licence line in the first {HEADER_WINDOW} lines:")
        for name, header in missing:
            print(f"  {name}: needs '{header}'")
        print(
            f"Platform files use '# {HEADER}'; files under {', '.join(SDK_PREFIXES)} "
            f"use '# {SDK_HEADER}' (see LICENSING.md)."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
