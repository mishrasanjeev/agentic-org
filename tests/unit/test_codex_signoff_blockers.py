"""Pin the fixes from the Codex 2026-04-22 release-signoff blocker list.

Source-inspection tests — intentionally cheap, intentionally pinned
verbatim at the lines Codex flagged so a silent revert is caught.
"""

from __future__ import annotations

import inspect


class TestKpiHelpersHonorCompanyId:
    """H1 — the four KPI helpers now accept ``company_id`` and filter."""

    def test_query_agent_results_accepts_company_id(self) -> None:
        from api.v1 import kpis

        sig = inspect.signature(kpis._query_agent_results)
        assert "company_id" in sig.parameters
        src = inspect.getsource(kpis._query_agent_results)
        assert "company_id = :cid" in src
        assert "_parse_company_uuid" in src

    def test_count_pending_approvals_accepts_company_id(self) -> None:
        from api.v1 import kpis

        sig = inspect.signature(kpis._count_pending_approvals)
        assert "company_id" in sig.parameters
        src = inspect.getsource(kpis._count_pending_approvals)
        assert "company_id = :cid" in src

    def test_get_tax_calendar_accepts_company_id(self) -> None:
        from api.v1 import kpis

        sig = inspect.signature(kpis._get_tax_calendar)
        assert "company_id" in sig.parameters
        src = inspect.getsource(kpis._get_tax_calendar)
        assert "company_id = :cid" in src

    def test_get_recent_escalations_accepts_company_id(self) -> None:
        from api.v1 import kpis

        sig = inspect.signature(kpis._get_recent_escalations)
        assert "company_id" in sig.parameters
        src = inspect.getsource(kpis._get_recent_escalations)
        assert "company_id = :cid" in src

    def test_cfo_endpoint_threads_company_id_into_helpers(self) -> None:
        """CEO/CFO route bodies must pass company_id to each helper."""
        from api.v1 import kpis

        cfo_src = inspect.getsource(kpis.get_cfo_kpis)
        assert "company_id=company_id" in cfo_src
        ceo_src = inspect.getsource(kpis.get_ceo_kpis)
        assert "company_id=company_id" in ceo_src


class TestSchemaRegistryBackendLoad:
    """H2 — Schemas.tsx reads backend json_schema via GET /schemas/{name}."""

    def test_get_schema_by_name_route_exists(self) -> None:
        from api.v1 import schemas

        assert any(
            getattr(r, "path", "") == "/schemas/{name}"
            and "GET" in getattr(r, "methods", set())
            for r in schemas.router.routes
        )


class TestKbDedupFailClosed:
    """H3 — KB upload refuses the write when dedup lookup fails."""

    def test_dedup_lookup_failure_raises_503(self) -> None:
        from api.v1 import knowledge

        src = inspect.getsource(knowledge.upload_document)
        # Old fail-open comment is gone; new behaviour is fail-closed 503.
        assert "dedup_lookup_unavailable" in src
        assert "status_code=503" in src
        # Confirm no remnant of the old ``existing = None`` swallow path
        # in the dedup branch.
        dedup_branch = src[src.index("if not allow_duplicate and not replace"):]
        dedup_branch = dedup_branch[: dedup_branch.index("if existing is not None")]
        assert "existing = None" not in dedup_branch
