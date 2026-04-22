"""Pin the seven-discipline fixes from the Codex 2026-04-22 enterprise deep audit.

These are signature-level tests, not full end-to-end. The goal is to
prevent regression of the admin-gate / domain-RBAC / fail-closed /
history contract even without a live DB. Integration coverage for the
new template-history endpoints is in
``tests/integration/test_db_api_endpoints.py``.
"""

from __future__ import annotations

import inspect

from api.v1 import agents as agents_mod
from api.v1 import companies as companies_mod
from api.v1 import kpis as kpis_mod
from api.v1 import prompt_templates as tpl_mod


def _route_has_admin_guard(module, path_substr: str, method: str) -> bool:
    """Walk router.routes and confirm the admin dep is attached."""
    for route in module.router.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path"):
            continue
        if method.upper() not in route.methods:
            continue
        if path_substr not in route.path:
            continue
        deps = getattr(route, "dependencies", []) or []
        # FastAPI stores the dep call expr; string-match the target.
        for d in deps:
            dep_call = getattr(d, "dependency", None)
            if dep_call is None:
                continue
            # require_tenant_admin wraps require_scope("agenticorg:admin");
            # the closure captures that scope string.
            cells = (dep_call.__closure__ or ())
            for cell in cells:
                try:
                    val = cell.cell_contents
                except ValueError:
                    continue
                if isinstance(val, str) and "admin" in val.lower():
                    return True
    return False


class TestAdminGateOnMutations:
    """F1 — every sensitive mutation must sit behind require_tenant_admin."""

    def test_create_agent_requires_admin(self) -> None:
        assert _route_has_admin_guard(agents_mod, "/agents", "POST")

    def test_replace_agent_requires_admin(self) -> None:
        assert _route_has_admin_guard(agents_mod, "/agents/{agent_id}", "PUT")

    def test_delete_agent_requires_admin(self) -> None:
        assert _route_has_admin_guard(agents_mod, "/agents/{agent_id}", "DELETE")

    def test_clone_agent_requires_admin(self) -> None:
        assert _route_has_admin_guard(agents_mod, "/clone", "POST")

    def test_create_company_requires_admin(self) -> None:
        assert _route_has_admin_guard(companies_mod, "/companies", "POST")

    def test_update_company_requires_admin(self) -> None:
        assert _route_has_admin_guard(companies_mod, "/companies/{company_id}", "PATCH")

    def test_create_prompt_template_requires_admin(self) -> None:
        assert _route_has_admin_guard(tpl_mod, "/prompt-templates", "POST")


class TestDomainRbacOnObjectRoutes:
    """F2 — object-by-id routes must accept user_domains."""

    def test_get_agent_honors_user_domains(self) -> None:
        sig = inspect.signature(agents_mod.get_agent)
        assert "user_domains" in sig.parameters

    def test_replace_agent_honors_user_domains(self) -> None:
        sig = inspect.signature(agents_mod.replace_agent)
        assert "user_domains" in sig.parameters

    def test_update_agent_honors_user_domains(self) -> None:
        sig = inspect.signature(agents_mod.update_agent)
        assert "user_domains" in sig.parameters

    def test_delete_agent_honors_user_domains(self) -> None:
        sig = inspect.signature(agents_mod.delete_agent)
        assert "user_domains" in sig.parameters

    def test_get_prompt_template_honors_user_domains(self) -> None:
        sig = inspect.signature(tpl_mod.get_prompt_template)
        assert "user_domains" in sig.parameters

    def test_enforce_domain_access_raises_404_on_mismatch(self) -> None:
        # Forge a minimal object matching the helper's duck-typed usage.
        class _Stub:
            domain = "finance"

        try:
            agents_mod._enforce_domain_access(_Stub(), ["hr"])
        except Exception as exc:
            # Any HTTP 404 is the required behavior — leak existence as
            # "not found" not "forbidden".
            assert getattr(exc, "status_code", None) == 404
            return
        raise AssertionError("enforce_domain_access did not raise")


class TestShadowLimitFailsClosed:
    """F7 — shadow-limit must not bypass on query failure."""

    def test_create_agent_source_no_longer_has_silent_bypass(self) -> None:
        src = inspect.getsource(agents_mod.create_agent)
        # The old pattern was a bare ``except Exception: pass`` wrapping
        # the shadow-limit check. It's gone — the new path logs and
        # raises a 503 on any unexpected failure.
        assert "shadow_limit_check_failed" in src
        # Defensive: the legacy bypass comment must not reappear.
        assert "Skip limit check if query fails" not in src


class TestReplaceAgentFullFields:
    """F5 — PUT /agents/{id} must honour every AgentCreate field."""

    def test_source_writes_company_id(self) -> None:
        src = inspect.getsource(agents_mod.replace_agent)
        assert "agent.company_id" in src
        assert "agent.connector_ids" in src
        assert "agent.employee_name" in src
        assert "agent.avatar_url" in src
        assert "agent.designation" in src
        assert "agent.routing_filter" in src
        assert "agent.reporting_to" in src
        assert "agent.org_level" in src


class TestClonePreservesScope:
    """F6 — clone must preserve company_id + connector_ids."""

    def test_clone_source_passes_company_and_connector_ids(self) -> None:
        src = inspect.getsource(agents_mod.clone_agent)
        assert "company_id=" in src
        assert "connector_ids=" in src


class TestPromptTemplateHistoryContract:
    """F8 — template history + rollback endpoints must exist."""

    def test_history_endpoint_exists(self) -> None:
        assert any(
            getattr(r, "path", "") == "/prompt-templates/{template_id}/history"
            and "GET" in getattr(r, "methods", set())
            for r in tpl_mod.router.routes
        )

    def test_rollback_endpoint_exists(self) -> None:
        assert any(
            getattr(r, "path", "") == "/prompt-templates/{template_id}/rollback"
            and "POST" in getattr(r, "methods", set())
            for r in tpl_mod.router.routes
        )

    def test_create_populates_created_by(self) -> None:
        src = inspect.getsource(tpl_mod.create_prompt_template)
        assert "created_by=" in src
        assert "_user_uuid_from_claims" in src

    def test_update_writes_history_row(self) -> None:
        src = inspect.getsource(tpl_mod.update_prompt_template)
        assert "PromptTemplateEditHistory" in src


class TestKpiCompanyFilter:
    """F3 — KPI SQL helper must accept company_id."""

    def test_compute_basic_metrics_accepts_company_id(self) -> None:
        sig = inspect.signature(kpis_mod._compute_basic_metrics)
        assert "company_id" in sig.parameters
