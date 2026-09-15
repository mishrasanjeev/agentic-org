# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_schemas

DRAFT7 = "http://json-schema.org/draft-07/schema#"


def _write(root: Path, name: str, body: object) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return path


def _run(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = check_schemas.main([str(root)])
    return code, capsys.readouterr().out


def test_valid_schemas_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "a.schema.json", {"$schema": DRAFT7, "type": "object"})
    _write(tmp_path, "nested/b.schema.json", {"$schema": "https://json-schema.org/draft/2020-12/schema"})
    code, out = _run(tmp_path, capsys)
    assert code == 0
    assert "2 schema(s) valid" in out


def test_invalid_schema_fails_with_location(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "bad.schema.json", {"$schema": DRAFT7, "properties": {"x": {"type": "not-a-type"}}})
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert "bad.schema.json: invalid schema at properties/x/type" in out


def test_missing_dialect_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "nodialect.schema.json", {"type": "object"})
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert "missing $schema" in out


def test_unknown_dialect_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "odd.schema.json", {"$schema": "https://example.com/not-a-dialect", "type": "object"})
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert "unknown $schema dialect" in out


def test_unreadable_json_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "broken.schema.json", "{not json")
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert "not readable JSON" in out


def test_missing_directory_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(tmp_path / "absent", capsys)
    assert code == 1
    assert "does not exist" in out


def test_empty_directory_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert "no *.schema.json files" in out


def test_repository_schemas_are_valid(capsys: pytest.CaptureFixture[str]) -> None:
    repo_schemas = Path(__file__).resolve().parents[2] / "schemas"
    code, out = _run(repo_schemas, capsys)
    assert code == 0, out
