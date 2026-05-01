"""File-upload bounds + streaming helpers.

SEC-2026-05-P1-005 (docs/BRUTAL_SECURITY_SCAN_2026-05-01.md).

Three classes of defense:

1. **Pre-read validation** (``validate_upload``): reject by MIME +
   extension before any byte hits memory or disk. Closes the
   ".exe with text/plain content-type" attack class and the
   ".svg with embedded XSL/JS" class.

2. **Bounded streaming** (``stream_to_tempfile``): copy the upload
   to a NamedTemporaryFile in 64 KiB chunks, abort + raise 413 the
   moment we exceed the size cap. Replaces ``await file.read()``
   which was an unbounded memory load.

3. **Bounded text extraction** (``read_text_bounded``): UTF-8 read
   with a byte ceiling so text-extraction paths can't pull a 1 GiB
   log file into a JSONB column.

Heavy parser bounds (PDF page count, DOCX paragraph cap, XLSX
sheet/row cap) are tracked in a follow-up PR — the underlying
parsers aren't fully wired up yet (see knowledge.py:604-605 inline
comment).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import IO, Final

from fastapi import HTTPException, UploadFile

# ── Limits ───────────────────────────────────────────────────────

# 50 MiB — generous for typical document upload (large PDFs are
# usually 5-20 MiB, multi-sheet XLSX rarely above 30 MiB), tight
# enough that a single request can't OOM a 2 GiB worker.
MAX_UPLOAD_BYTES: Final[int] = int(
    os.getenv("AGENTICORG_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
)

# 256 KiB cap on extracted text. Existing code already trimmed to
# 256 KB before slicing further to 8 KiB for the search slot —
# this surfaces the constant so it's testable + override-able.
MAX_EXTRACTED_TEXT_BYTES: Final[int] = 256 * 1024

# Streaming chunk size — 64 KiB is the sweet spot: large enough
# that copy syscall overhead is amortized, small enough that the
# size check fires within a few hundred KB of the cap rather than
# overshooting by a full chunk.
_STREAM_CHUNK_BYTES: Final[int] = 64 * 1024

# Extension allowlist. Anything not in this set returns 415.
# Match the existing knowledge upload path's supported types
# (line 608-611 of api/v1/knowledge.py) plus PDF/DOCX/XLSX which
# are accepted by RAGFlow's chunker.
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({
    # Plain text family
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    # Office documents
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    # Source-friendly
    ".log",
})

# MIME prefix allowlist. Browsers + curl set Content-Type — we
# trust it as a hint, not a security boundary (the extension
# allowlist runs alongside, and parsers do their own sniffing).
ALLOWED_MIME_PREFIXES: Final[tuple[str, ...]] = (
    "text/",
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.",  # docx, xlsx, pptx
    "application/msword",
    "application/vnd.ms-",
    "application/octet-stream",  # some clients fall back to this
)


# ── Validation ───────────────────────────────────────────────────


def validate_upload(file: UploadFile) -> None:
    """Reject uploads by extension or MIME before any byte is read.

    Raises HTTPException(415) on disallowed type so the client gets
    a clear "unsupported media type" error rather than a silent
    parser failure deep in the ingestion pipeline.

    The ``UploadFile.size`` attribute is set by Starlette when the
    multipart parser sees a Content-Length on the part — we use it
    as an early-out check so an obviously-too-large upload is
    rejected before streaming. ``stream_to_tempfile`` enforces the
    same cap during the actual copy in case ``size`` was missing or
    spoofed.
    """
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(
            status_code=400,
            detail={"error": "missing_filename", "message": "Upload must include a filename."},
        )

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail={
                "error": "unsupported_extension",
                "message": (
                    f"File extension {ext!r} is not in the upload allowlist. "
                    f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
                ),
                "extension": ext,
            },
        )

    content_type = (file.content_type or "").lower()
    if content_type and not any(
        content_type.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES
    ):
        raise HTTPException(
            status_code=415,
            detail={
                "error": "unsupported_mime_type",
                "message": (
                    f"Content-Type {content_type!r} is not in the upload "
                    "allowlist. The extension was accepted but the declared "
                    "MIME type isn't recognised — likely a misconfigured "
                    "client or a deliberately-spoofed upload."
                ),
                "content_type": content_type,
            },
        )

    # Early size check — Starlette sets ``size`` on the UploadFile
    # if the multipart part carried a Content-Length. Missing /
    # spoofed sizes are caught later in stream_to_tempfile.
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "upload_too_large",
                "message": (
                    f"Declared upload size {declared_size} bytes exceeds the "
                    f"{MAX_UPLOAD_BYTES} byte cap."
                ),
                "max_bytes": MAX_UPLOAD_BYTES,
                "declared_bytes": declared_size,
            },
        )


# ── Streaming ────────────────────────────────────────────────────


async def stream_to_tempfile(
    file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES
) -> tuple[Path, int]:
    """Copy the upload to a NamedTemporaryFile in chunks; abort if
    the running total exceeds ``max_bytes``.

    Returns ``(path, bytes_written)``. The caller is responsible for
    deleting the tempfile when done (use ``Path.unlink(missing_ok=True)``
    in a ``try/finally``).

    Why a tempfile and not an in-memory buffer: 50 MiB × N concurrent
    uploads is an easy way to OOM a 2 GiB worker. Tempfiles let the
    kernel page the bytes out and keep RSS bounded.

    Raises HTTPException(413) when ``bytes_written > max_bytes`` mid-
    stream. The partial tempfile is cleaned up before raising so we
    don't leak disk on the rejection path.
    """
    total = 0
    fd, tmp_path_str = tempfile.mkstemp(prefix="agenticorg-upload-")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error": "upload_too_large",
                            "message": (
                                f"Upload exceeded the {max_bytes} byte cap "
                                "during streaming."
                            ),
                            "max_bytes": max_bytes,
                            "bytes_seen": total,
                        },
                    )
                out.write(chunk)
        return tmp_path, total
    except HTTPException:
        # Roll back on rejection so we don't leak partial bytes.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except Exception:
        # Same cleanup for unexpected errors — never leak disk.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_text_bounded(
    path: Path, max_bytes: int = MAX_EXTRACTED_TEXT_BYTES
) -> str:
    """Read up to ``max_bytes`` from ``path`` as UTF-8 (errors='ignore').

    Replaces the historical pattern ``content[:256*1024].decode(...)``
    where ``content`` was the full file already in memory. We slice
    at the file-system layer instead so the 1 GiB log file never
    materialises in RAM.

    Returns an empty string on read errors — the caller treats text
    extraction as best-effort, which matches the existing contract.
    """
    try:
        with path.open("rb") as f:
            head = f.read(max_bytes)
        return head.decode("utf-8", errors="ignore")
    except OSError:
        return ""


def cleanup_tempfile(path: Path) -> None:
    """Best-effort removal of a tempfile produced by ``stream_to_tempfile``.

    Wraps the ``unlink(missing_ok=True)`` pattern so callers can use
    a single call in a ``finally`` block instead of nested try/except.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# Re-export names for convenient ``from core.file_ingestion.limits import *``
__all__ = [
    "ALLOWED_EXTENSIONS",
    "ALLOWED_MIME_PREFIXES",
    "MAX_EXTRACTED_TEXT_BYTES",
    "MAX_UPLOAD_BYTES",
    "cleanup_tempfile",
    "read_text_bounded",
    "stream_to_tempfile",
    "validate_upload",
]


# Suppress unused-import warning for ``IO`` and ``shutil`` if the
# linter complains — they're kept intentionally for future PRs that
# add object-storage streaming.
_ = (IO, shutil)
