"""Bounded file ingestion helpers (PR-D / SEC-2026-05-P1-005).

The knowledge-base upload path historically called ``await file.read()``
without size, MIME, or extension limits — a classic DoS surface.

This package centralises the bounds:

- ``limits.MAX_UPLOAD_BYTES`` — request-layer ceiling.
- ``limits.ALLOWED_EXTENSIONS`` / ``limits.ALLOWED_MIME_PREFIXES`` — what
  gets to reach a parser at all.
- ``limits.validate_upload(file)`` — pre-read MIME/extension check.
- ``limits.stream_to_tempfile(file, max_bytes)`` — chunked copy that
  refuses anything larger than the cap.
- ``limits.read_text_bounded(path, max_bytes)`` — UTF-8 read with a
  byte ceiling so text extraction can't blow up on a 1 GB log file.

See ``docs/BRUTAL_SECURITY_SCAN_2026-05-01.md`` SEC-005 for the
full requirement list. Heavy parser bounds (PDF page count, DOCX
paragraph cap, XLSX sheet/row cap) are a follow-up — they need the
parser deps wired in first.
"""
