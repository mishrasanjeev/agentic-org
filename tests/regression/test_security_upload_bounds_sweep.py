"""Regression pins for upload paths outside the knowledge-ingestion flow."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_upload_endpoints_stream_to_tempfile_instead_of_unbounded_read() -> None:
    upload_routes = {
        "api/v1/sop.py": "stream_to_tempfile(file, max_bytes=MAX_FILE_SIZE",
        "api/v1/agents.py": "stream_to_tempfile(file, max_bytes=MAX_AGENT_CSV_IMPORT_BYTES",
        "api/v1/sales.py": "stream_to_tempfile(file, max_bytes=MAX_CSV_IMPORT_BYTES",
        "api/v1/abm.py": "stream_to_tempfile(file, max_bytes=MAX_ABM_CSV_UPLOAD_BYTES",
    }
    for rel, expected_call in upload_routes.items():
        src = (REPO / rel).read_text(encoding="utf-8")
        assert expected_call in src, f"{rel} must copy uploads with a byte cap"
        assert "await file.read()" not in src, f"{rel} must not read the full upload into memory"
        assert "cleanup_tempfile" in src, f"{rel} must remove temporary uploads"


def test_agent_csv_import_rejects_non_csv_empty_and_non_utf8_source() -> None:
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "not filename.endswith(\".csv\")" in src
    assert "not content.strip()" in src
    assert "UnicodeDecodeError" in src
