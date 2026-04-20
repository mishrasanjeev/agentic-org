"""Unit tests for knowledge-base helpers that are pure and DB-free.

Covers the TC_006 regression: the public document API must never leak
the legacy "ready" label. Covers TC_010 by confirming DocumentOut
carries both `created_at` and `uploaded_at` so the UI can read either.
"""

from __future__ import annotations

from api.v1.knowledge import (
    DOC_STATUS_FAILED,
    DOC_STATUS_INDEXED,
    DOC_STATUS_PROCESSING,
    DocumentOut,
    _normalize_status,
)


class TestNormalizeStatus:
    def test_ready_maps_to_indexed(self) -> None:
        assert _normalize_status("ready") == DOC_STATUS_INDEXED

    def test_done_ok_processed_all_map_to_indexed(self) -> None:
        for legacy in ("done", "ok", "processed"):
            assert _normalize_status(legacy) == DOC_STATUS_INDEXED

    def test_canonical_values_pass_through(self) -> None:
        for v in (
            DOC_STATUS_PROCESSING,
            DOC_STATUS_INDEXED,
            DOC_STATUS_FAILED,
        ):
            assert _normalize_status(v) == v

    def test_empty_and_none_default_to_processing(self) -> None:
        assert _normalize_status(None) == DOC_STATUS_PROCESSING
        assert _normalize_status("") == DOC_STATUS_PROCESSING

    def test_unknown_value_passes_through_unchanged(self) -> None:
        """Preserving unknown values is intentional — we don't want the
        normaliser to hide a real bug by silently remapping something
        unexpected to 'indexed'. The UI will render it as a fallback
        badge and a reviewer will see it."""
        assert _normalize_status("quarantined") == "quarantined"


class TestDocumentOutFields:
    def test_document_out_has_both_created_at_and_uploaded_at(self) -> None:
        """TC_010: the UI reads `uploaded_at`; back-compat keeps
        `created_at` in the same response."""
        doc = DocumentOut(
            document_id="doc-1",
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=100,
            status=DOC_STATUS_INDEXED,
            created_at="2026-04-20T10:00:00Z",
            uploaded_at="2026-04-20T10:00:00Z",
        )
        as_dict = doc.model_dump()
        assert "created_at" in as_dict
        assert "uploaded_at" in as_dict
        assert as_dict["created_at"] == as_dict["uploaded_at"]
