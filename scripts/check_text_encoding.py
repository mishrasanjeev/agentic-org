#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Reject invalid UTF-8 and UTF-8 text misdecoded as Windows-1252/Latin-1."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pdf", ".woff",
    ".woff2", ".ttf", ".eot", ".zip", ".gz", ".mp3", ".mp4", ".wav",
})


def _byte_characters(start: int, stop: int) -> str:
    characters = set()
    for value in range(start, stop):
        characters.add(chr(value))
        characters.add(bytes([value]).decode("cp1252", errors="replace"))
    characters.discard("\ufffd")
    return re.escape("".join(sorted(characters)))


_CONTINUATION = _byte_characters(0x80, 0xC0)
MOJIBAKE = re.compile(
    rf"[{_byte_characters(0xC2, 0xE0)}][{_CONTINUATION}]"
    rf"|[{_byte_characters(0xE0, 0xF0)}][{_CONTINUATION}]{{2}}"
    rf"|[{_byte_characters(0xF0, 0xF5)}][{_CONTINUATION}]{{3}}"
    r"|\ufffd"
)


def scan_file(path: Path) -> list[str]:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"{path}: invalid UTF-8"]
    # Latin-1 NEL can be a corrupt continuation byte, not a source newline.
    return [
        f"{path}:{number}: corrupted text {match.group().encode('unicode_escape').decode('ascii')}"
        for number, line in enumerate(content.split("\n"), 1)
        for match in MOJIBAKE.finditer(line)
    ]


def tracked_files(root: Path) -> list[Path]:
    # Include staged additions, and fail closed if the tracked-file inventory fails.
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "ls-files", "-z"],  # noqa: S607
        capture_output=True, check=True,
    )
    return [root / name.decode("utf-8") for name in result.stdout.split(b"\0") if name]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    try:
        paths = args.paths or tracked_files(ROOT)
        failures = [failure for path in paths for failure in scan_file(path)]
    except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as exc:
        print(f"Text encoding check could not complete: {exc}")
        return 2
    for failure in failures:
        print(failure)
    print(f"Text encoding: {len(paths)} files checked; {len(failures)} findings")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
