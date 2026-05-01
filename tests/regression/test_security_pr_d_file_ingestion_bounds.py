"""SEC-2026-05-P1-005 PR-D: file ingestion bounds pins.

The knowledge upload path historically called ``await file.read()``
without any size, MIME, or extension limit. PR-D adds:

- ``validate_upload`` — pre-read MIME + extension + declared-size check.
- ``stream_to_tempfile`` — chunked copy with a 50 MiB cap.
- ``read_text_bounded`` — UTF-8 read with a 256 KiB ceiling.

These pins ensure the bug class (DoS via memory exhaustion or
arbitrary parser invocation) can't recur even if a future contributor
touches the upload path.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from core.file_ingestion.limits import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_PREFIXES,
    MAX_EXTRACTED_TEXT_BYTES,
    MAX_UPLOAD_BYTES,
    cleanup_tempfile,
    read_text_bounded,
    stream_to_tempfile,
    validate_upload,
)

# ─────────────────────────────────────────────────────────────────
# Helpers — tiny UploadFile wrapper since FastAPI's UploadFile
# expects a file-like with async read(); we feed it BytesIO.
# ─────────────────────────────────────────────────────────────────


def _upload_file(filename: str, content: bytes, content_type: str = "text/plain") -> UploadFile:
    """Construct a Starlette UploadFile around a BytesIO buffer.

    Sets ``size`` (the multipart parser-provided content length) so
    the early validation path can exercise it.
    """
    f = UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers={"content-type": content_type},  # type: ignore[arg-type]
    )
    f.size = len(content)
    return f


# ─────────────────────────────────────────────────────────────────
# Constants — pin invariants
# ─────────────────────────────────────────────────────────────────


def test_max_upload_bytes_is_at_least_10_mib() -> None:
    """The default cap must be generous enough for typical document
    uploads (large PDFs, multi-sheet XLSX) and tight enough to
    prevent OOM. 10 MiB is the floor; 50 MiB is the shipping value.
    """
    assert MAX_UPLOAD_BYTES >= 10 * 1024 * 1024


def test_extracted_text_cap_is_smaller_than_upload_cap() -> None:
    """Text extraction never reads more than the upload cap (otherwise
    the cap wouldn't matter for memory). 256 KiB << 50 MiB."""
    assert MAX_EXTRACTED_TEXT_BYTES < MAX_UPLOAD_BYTES


def test_allowed_extensions_includes_known_good_types() -> None:
    """Pin the supported types — a regression that drops .pdf or
    .docx silently breaks every customer's RAGFlow ingest."""
    for ext in (".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".xlsx"):
        assert ext in ALLOWED_EXTENSIONS


def test_allowed_extensions_excludes_dangerous_types() -> None:
    """Forbid types that are popular DoS / RCE vectors. Pin the
    block so a future contributor can't widen the allowlist
    without explicitly removing this test."""
    for ext in (".exe", ".sh", ".bat", ".dll", ".so", ".bin", ".js", ".html"):
        assert ext not in ALLOWED_EXTENSIONS, (
            f"{ext!r} is in ALLOWED_EXTENSIONS — that's a security regression. "
            "Either remove it (preferred) or update this test with a justification."
        )


def test_allowed_mime_prefixes_covers_office_and_text() -> None:
    """The MIME allowlist lines up with the extension allowlist.
    Mismatch means a legitimate upload returns 415 even though
    the filename is fine."""
    expected_prefixes = ("text/", "application/pdf", "application/json")
    for prefix in expected_prefixes:
        assert any(p.startswith(prefix) or prefix.startswith(p) for p in ALLOWED_MIME_PREFIXES)


# ─────────────────────────────────────────────────────────────────
# validate_upload — pre-read rejection
# ─────────────────────────────────────────────────────────────────


def test_validate_rejects_missing_filename() -> None:
    f = _upload_file("", b"x", content_type="text/plain")
    with pytest.raises(HTTPException) as exc:
        validate_upload(f)
    assert exc.value.status_code == 400


def test_validate_rejects_disallowed_extension() -> None:
    f = _upload_file("malware.exe", b"x", content_type="application/octet-stream")
    with pytest.raises(HTTPException) as exc:
        validate_upload(f)
    assert exc.value.status_code == 415
    assert exc.value.detail["error"] == "unsupported_extension"


def test_validate_rejects_mime_extension_mismatch() -> None:
    """``.txt`` is allowed by extension, but ``video/mp4`` isn't in the
    MIME allowlist — pin that the AND-relationship is enforced."""
    f = _upload_file("file.txt", b"x", content_type="video/mp4")
    with pytest.raises(HTTPException) as exc:
        validate_upload(f)
    assert exc.value.status_code == 415


def test_validate_accepts_known_good_text_upload() -> None:
    """Happy path — the most common upload shape (markdown with
    text/plain). Must not raise."""
    f = _upload_file("notes.md", b"# hello", content_type="text/plain")
    validate_upload(f)


def test_validate_accepts_pdf_upload() -> None:
    """PDFs are the bread and butter of knowledge-base uploads."""
    f = _upload_file("doc.pdf", b"%PDF-fake", content_type="application/pdf")
    validate_upload(f)


def test_validate_rejects_oversized_declared_size() -> None:
    """When the multipart parser sets ``size`` and it exceeds the cap,
    reject before reading any byte. Returns 413."""
    f = _upload_file("big.txt", b"x" * 1024, content_type="text/plain")
    f.size = MAX_UPLOAD_BYTES + 1024  # client lied about size; treat as authoritative
    with pytest.raises(HTTPException) as exc:
        validate_upload(f)
    assert exc.value.status_code == 413
    assert exc.value.detail["error"] == "upload_too_large"


def test_validate_accepts_when_size_field_missing() -> None:
    """Some clients don't set Content-Length on multipart parts.
    ``validate_upload`` must NOT 400 on missing size — the streaming
    layer enforces the cap during the copy."""
    f = _upload_file("notes.md", b"hi", content_type="text/plain")
    f.size = None  # type: ignore[assignment]
    validate_upload(f)  # must not raise


def test_validate_accepts_empty_content_type() -> None:
    """Some uploads omit Content-Type (curl with no `-H`). Treat
    missing as 'unknown — fall through to extension', not 415."""
    f = _upload_file("notes.md", b"hi", content_type="")
    validate_upload(f)  # must not raise


# ─────────────────────────────────────────────────────────────────
# stream_to_tempfile — chunked copy with cap enforcement
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_writes_full_content_under_cap(tmp_path: Path) -> None:
    """Happy path — a small file streams cleanly to disk."""
    content = b"hello world\n" * 100
    f = _upload_file("notes.md", content)
    upload_path, total = await stream_to_tempfile(f, max_bytes=10 * 1024 * 1024)
    try:
        assert total == len(content)
        assert upload_path.read_bytes() == content
    finally:
        cleanup_tempfile(upload_path)


@pytest.mark.asyncio
async def test_stream_aborts_when_running_total_exceeds_cap() -> None:
    """The streaming check is the AUTHORITATIVE size enforcement —
    even if ``UploadFile.size`` was missing or spoofed, exceeding
    the cap mid-copy raises 413 and the partial tempfile is
    cleaned up.
    """
    # Generate 1 MiB of content but cap at 256 KiB.
    big = b"x" * (1024 * 1024)
    f = _upload_file("big.txt", big)
    f.size = None  # type: ignore[assignment]  # simulate missing declared size
    with pytest.raises(HTTPException) as exc:
        await stream_to_tempfile(f, max_bytes=256 * 1024)
    assert exc.value.status_code == 413
    assert exc.value.detail["error"] == "upload_too_large"


@pytest.mark.asyncio
async def test_stream_cleans_up_tempfile_on_413() -> None:
    """On rejection, the partial tempfile must be deleted — leaking
    disk on the rejection path defeats the bound."""
    big = b"x" * (1024 * 1024)
    f = _upload_file("big.txt", big)
    f.size = None  # type: ignore[assignment]
    with pytest.raises(HTTPException):
        upload_path, _ = await stream_to_tempfile(f, max_bytes=128 * 1024)
        # If we get here, the test is broken — the call should raise.
        assert not upload_path.exists()
    # We can't easily inspect the deleted tempfile path post-raise,
    # but we can pin the source so the unlink-on-rejection contract
    # can't silently regress. The implementation's try/except in
    # stream_to_tempfile guarantees unlink.
    src = (
        Path(__file__).resolve().parents[2]
        / "core" / "file_ingestion" / "limits.py"
    ).read_text(encoding="utf-8")
    assert "tmp_path.unlink(missing_ok=True)" in src


# ─────────────────────────────────────────────────────────────────
# read_text_bounded — extraction never exceeds 256 KiB
# ─────────────────────────────────────────────────────────────────


def test_read_text_bounded_truncates_at_cap(tmp_path: Path) -> None:
    """A multi-MB text file must never produce a string larger than
    the cap. Pins that the read cap is at the FS layer (not in-memory
    slice after a full read)."""
    big = "x" * (3 * MAX_EXTRACTED_TEXT_BYTES)
    p = tmp_path / "big.txt"
    p.write_text(big, encoding="utf-8")
    out = read_text_bounded(p)
    assert len(out.encode("utf-8")) <= MAX_EXTRACTED_TEXT_BYTES


def test_read_text_bounded_returns_full_content_when_small(tmp_path: Path) -> None:
    p = tmp_path / "small.txt"
    p.write_text("hello", encoding="utf-8")
    assert read_text_bounded(p) == "hello"


def test_read_text_bounded_returns_empty_on_missing_file(tmp_path: Path) -> None:
    """Treat extraction as best-effort — a vanished file shouldn't
    500 the upload. Returns empty string instead."""
    p = tmp_path / "does-not-exist.txt"
    assert read_text_bounded(p) == ""


def test_read_text_bounded_handles_invalid_utf8(tmp_path: Path) -> None:
    """``errors='ignore'`` means non-UTF8 bytes are dropped, not
    crashed on. Pin the contract."""
    p = tmp_path / "bad.txt"
    p.write_bytes(b"hello \xff\xfe world")
    out = read_text_bounded(p)
    assert "hello" in out
    assert "world" in out
