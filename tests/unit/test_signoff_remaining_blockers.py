"""Pin the fixes for the post-deploy e2e blockers + infra health probes.

Source-inspection + behavioral tests — intentionally strict so a revert
on any of these is caught before the next release-signoff review.
"""

from __future__ import annotations

import inspect


class TestEvalsBaselineFallback:
    """I1 — /api/v1/evals must serve a baseline when scorecard file is
    missing, so the public /evals page never renders an empty state."""

    def test_load_scorecard_returns_baseline_when_file_missing(self, tmp_path, monkeypatch) -> None:
        from api.v1 import evals

        # Point the module's scorecard path at a non-existent location.
        monkeypatch.setattr(evals, "_SCORECARD_PATH", tmp_path / "nothere.json")
        result = evals._load_scorecard()

        assert result["_is_baseline"] is True
        # The UI renders percentages from these four fields.
        pm = result["platform_metrics"]
        for key in ("stp_rate", "hitl_rate", "mean_confidence", "uptime_sla"):
            assert isinstance(pm[key], (int, float)), f"{key} must be numeric"
            assert 0 < pm[key] <= 1, f"{key} should be a 0-1 ratio"

    def test_require_scorecard_still_raises_404_when_missing(self, tmp_path, monkeypatch) -> None:
        """Single-agent routes must still 404 when no real data — don't
        lie with baseline values for an agent that was never measured."""
        from fastapi import HTTPException

        from api.v1 import evals

        monkeypatch.setattr(evals, "_SCORECARD_PATH", tmp_path / "nothere.json")
        try:
            evals._require_scorecard()
        except HTTPException as exc:
            assert exc.status_code == 404
            return
        raise AssertionError("_require_scorecard did not raise 404")


class TestKnowledgeHealthEndpoint:
    """I4 — /knowledge/health exists and returns the expected shape."""

    def test_route_exists_and_is_public(self) -> None:
        from api.v1 import knowledge

        route_paths = {getattr(r, "path", "") for r in knowledge.router.routes}
        assert "/knowledge/health" in route_paths

    def test_source_reports_effective_mode(self) -> None:
        from api.v1 import knowledge

        src = inspect.getsource(knowledge.knowledge_health)
        for key in ("ragflow_configured", "ragflow_reachable",
                    "pgvector_ready", "effective_mode", "notes"):
            assert key in src


class TestBillingHealthEndpoint:
    """I5 — /billing/health exists and reports gateway config without
    attempting a real charge."""

    def test_route_exists(self) -> None:
        from api.v1 import billing

        route_paths = {getattr(r, "path", "") for r in billing.router.routes}
        assert "/billing/health" in route_paths

    def test_source_does_not_call_real_charge(self) -> None:
        """Safety — the health probe must not invoke the Stripe/Pine Labs
        client (no side effects). Grep the source to confirm."""
        from api.v1 import billing

        src = inspect.getsource(billing.billing_health)
        assert "create_checkout_session" not in src
        assert "create_payment_order" not in src


class TestKbContentExtractionPersisted:
    """I6 — text/markdown uploads persist extracted content into
    metadata, and the search fallback matches against it."""

    def test_upload_extracts_text_for_textual_types(self) -> None:
        from api.v1 import knowledge

        src = inspect.getsource(knowledge.upload_document)
        assert "content_text" in src
        assert "extracted_text" in src
        # Guarded to textual types — PDF/XLSX are tracked as enhancement.
        assert "is_textual" in src

    def test_search_fallback_scans_content_text(self) -> None:
        from api.v1 import knowledge

        src = inspect.getsource(knowledge._native_semantic_search)
        assert "metadata->>'content_text'" in src
