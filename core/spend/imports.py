# SPDX-License-Identifier: Apache-2.0
"""Bounded CSV and JSON row parsing for spend imports, and the import report.

An import file is at most ``MAX_IMPORT_BYTES`` (the request gate and the
upload stream both hold it there) and at most ``MAX_IMPORT_ROWS`` rows,
counted while CSV rows are read and as soon as a JSON file is parsed, so a
file of tiny rows never becomes a million row dicts before it is refused.

JSON is a list of objects, or an object holding the list under one of the
envelope keys (``rows``; the invoice import also accepts ``lines``). Its
numbers are read as decimals, never binary floats. CSV is decoded as UTF-8
(a byte-order mark is dropped), else Latin-1; header names are trimmed and
lower-cased; unknown columns are ignored. Every value comes out as text:
numbers as their exact text, ``null`` as empty, a nested list or object as
its JSON text (decimals inside it as strings). A file that cannot be read is
400 ``bad_file``; a missing required column is 400 ``missing_columns``.

This module is the contract a later HR or finance synchronisation writes to.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.spend.errors import SpendError

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
_HASH_CHUNK = 1024 * 1024


def _is_json(filename: str, content_type: str) -> bool:
    return (filename or "").lower().endswith(".json") or (content_type or "").split(";")[0].strip().lower() == (
        "application/json"
    )


def file_format(filename: str, content_type: str) -> str:
    """``json`` or ``csv``: how ``iter_rows`` reads a file of this name and content type."""
    return "json" if _is_json(filename, content_type) else "csv"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        # A Decimal inside a nested cell (the only type json.dumps cannot write here) goes out as its exact text.
        return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)
    return str(value).strip()


def _check_columns(present: set[str], required: tuple[str, ...]) -> None:
    missing = [name for name in required if name not in present]
    if missing:
        raise SpendError(400, "missing_columns", f"the file lacks the column(s): {', '.join(missing)}")


def _json_rows(path: Path, required: tuple[str, ...], envelope_keys: tuple[str, ...]) -> Iterator[dict[str, Any]]:
    with path.open("rb") as handle:
        # Numbers as Decimal: a binary float would change a price past about 15 significant digits.
        data = json.load(handle, parse_float=Decimal)
    if isinstance(data, dict):
        found = next((data[k] for k in envelope_keys if k in data), None)
        data = found
    if not isinstance(data, list):
        raise SpendError(400, "bad_file", f"a JSON import is a list of objects or an object with {list(envelope_keys)}")
    # Counted before any row is copied, so a file of tiny objects is refused at once.
    if len(data) > MAX_IMPORT_ROWS:
        raise SpendError(413, "too_many_rows", f"an import holds at most {MAX_IMPORT_ROWS} rows")
    if not all(isinstance(item, dict) for item in data):
        raise SpendError(400, "bad_file", "every row of a JSON import is an object")
    # The columns of a JSON file are the keys any of its rows carries.
    _check_columns({str(k).strip().lower() for item in data for k in item}, required)
    for item in data:
        yield {str(k).strip().lower(): v for k, v in item.items()}


def _csv_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _csv_rows(path: Path, required: tuple[str, ...]) -> Iterator[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(_csv_text(path), newline=""))
    header = [str(name or "").strip().lower() for name in (reader.fieldnames or [])]
    _check_columns(set(header), required)
    reader.fieldnames = header
    for row in reader:
        yield {k: v for k, v in row.items() if k}


def iter_rows(
    path: Path,
    *,
    filename: str,
    content_type: str,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    envelope_keys: tuple[str, ...] = ("rows",),
) -> Iterator[dict[str, str]]:
    """Rows of the file with only the known columns, as text; a known optional column absent from a row is left out.

    Raises ``SpendError`` 400 ``bad_file`` / ``missing_columns``.
    """
    source = (
        _json_rows(path, required, envelope_keys) if _is_json(filename, content_type) else _csv_rows(path, required)
    )
    try:
        for raw in source:
            row = {name: _cell(raw.get(name)) for name in required}
            row.update({name: _cell(raw[name]) for name in optional if name in raw})
            yield row
    except (json.JSONDecodeError, RecursionError, csv.Error, UnicodeError, ValueError, OSError) as exc:
        raise SpendError(400, "bad_file", f"the file could not be read ({type(exc).__name__})") from None


def parse_rows(path: Path, **kw: Any) -> list[dict[str, str]]:
    """Every row of the file, refusing past ``MAX_IMPORT_ROWS`` (413 ``too_many_rows``) while reading."""
    rows: list[dict[str, str]] = []
    for row in iter_rows(path, **kw):
        if len(rows) >= MAX_IMPORT_ROWS:
            raise SpendError(413, "too_many_rows", f"an import holds at most {MAX_IMPORT_ROWS} rows")
        rows.append(row)
    return rows


def file_sha256(path: Path) -> str:
    """The sha256 of the uploaded file, recorded in the import's audit manifest."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_report(*, dry_run: bool, received: int) -> dict[str, Any]:
    """The report every import answers: counts, and the rejected rows with their reasons."""
    return {"dry_run": dry_run, "received": received, "created": 0, "updated": 0, "unchanged": 0, "rejected": []}


def reject(report: dict[str, Any], *, row: int, key: str, reason: str) -> None:
    """Record a rejected row; rows are numbered from 2 (the CSV header is row 1)."""
    report["rejected"].append({"row": row, "key": key[:200], "reason": reason})
