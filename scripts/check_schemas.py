#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Schema validation step of ``make check``.

Every ``*.schema.json`` file under the given directories (default: ``schemas``)
must be valid JSON, declare its dialect with ``$schema``, name a dialect the
installed ``jsonschema`` knows, and be a valid schema of that dialect.

This is the hook later contract checks extend (fixture and manifest
validation); keep each addition a function called from :func:`main`.

Fails closed: an unreadable file, a missing or unknown ``$schema`` and a
directory that does not exist are all failures, never skips.

    python scripts/check_schemas.py [DIR ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import exceptions, validators

DEFAULT_ROOTS = ("schemas",)


def schema_files(roots: list[Path]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    problems: list[str] = []
    for root in roots:
        if not root.is_dir():
            problems.append(f"{root}: schema directory does not exist")
            continue
        files.extend(sorted(root.rglob("*.schema.json")))
    return files, problems


def check_schema_file(path: Path) -> str | None:
    """Return a problem description, or ``None`` when the schema is valid."""
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"{path}: not readable JSON ({exc})"
    if not isinstance(schema, dict):
        return f"{path}: top level is not a JSON object"
    dialect = schema.get("$schema")
    if not isinstance(dialect, str) or not dialect:
        return f"{path}: missing $schema (declare the JSON Schema dialect)"
    validator_cls = validators.validator_for(schema, default=None)
    if validator_cls is None:
        return f"{path}: unknown $schema dialect {dialect!r}"
    try:
        validator_cls.check_schema(schema)
    except exceptions.SchemaError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        return f"{path}: invalid schema at {location}: {exc.message}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="*", default=list(DEFAULT_ROOTS))
    args = parser.parse_args(argv)

    files, problems = schema_files([Path(root) for root in args.roots])
    if not files and not problems:
        problems.append(f"no *.schema.json files found under {', '.join(args.roots)}")
    for path in files:
        problem = check_schema_file(path)
        if problem:
            problems.append(problem)

    if problems:
        print("Schema validation failed:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"check_schemas: {len(files)} schema(s) valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
