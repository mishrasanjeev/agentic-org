# SPDX-License-Identifier: Apache-2.0
"""Every raw session in production code is accounted for (NOBYPASSRLS readiness).

Under a ``NOBYPASSRLS`` application role a session opened straight from
``async_session_factory()`` carries no ``agenticorg.tenant_id``: FORCE-RLS
tables read as empty and writes fail ``WITH CHECK``. Application code binds
its context through ``get_tenant_session`` / ``bind_tenant_context`` (tenant)
or ``get_platform_session`` (the documented cross-tenant flows). The raw
sites that remain are listed here with the reason each one is safe; a new
raw site fails this test until it is bound or justified.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("api", "auth", "connectors", "core", "workflows")

TENANT_CATALOGUE = "reads only the tenants catalogue (no RLS) behind SET LOCAL row_security = off"
PLATFORM_TABLE = "platform table without tenant_id or row-level security"
BINDS_ITSELF = "repository binds agenticorg.tenant_id in every transaction it opens"
OPERATOR_CLI = (
    "operator CLI aggregate across tenants: SET LOCAL row_security = off makes a NOBYPASSRLS role "
    "raise instead of reading nothing; run as the BYPASSRLS operator role"
)

ALLOWED: dict[tuple[str, str], str] = {
    ("api/v1/rpa_schedules.py", "_all_tenant_ids"): TENANT_CATALOGUE,
    ("core/billing/budget_evaluator.py", "evaluate_budget_alerts"): TENANT_CATALOGUE,
    ("core/billing/invoice_generator.py", "generate_invoices_for_period"): TENANT_CATALOGUE,
    ("core/cases/push.py", "_all_tenant_ids"): TENANT_CATALOGUE,
    ("core/cron/compliance_alerts.py", "run_compliance_alert_cron"): TENANT_CATALOGUE,
    ("core/lineage/sync.py", "sweep"): TENANT_CATALOGUE,
    ("core/memory/long_term.py", "prune_all_tenants"): TENANT_CATALOGUE,
    ("core/spend/tenants.py", "active_tenant_ids"): TENANT_CATALOGUE,
    ("core/spend/tenants.py", "tenants_since"): TENANT_CATALOGUE,
    ("core/tasks/audit_chain_tasks.py", "_tenant_ids"): TENANT_CATALOGUE,
    ("core/tasks/channel_tasks.py", "_sweep"): TENANT_CATALOGUE,
    ("core/tasks/gateway_tasks.py", "_prune_model_gateway_records_async"): TENANT_CATALOGUE,
    ("core/tasks/knowledge_tasks.py", "_sweep"): TENANT_CATALOGUE,
    ("core/tasks/report_tasks.py", "_all_tenant_ids"): TENANT_CATALOGUE,
    ("core/tasks/synthetic_tasks.py", "_tenant_catalogue"): TENANT_CATALOGUE,
    ("core/tasks/timeline_tasks.py", "_prune_run_spans_async"): TENANT_CATALOGUE,
    ("core/tasks/workflow_tasks.py", "_recover_halted_workflows_async"): TENANT_CATALOGUE,
    ("core/tasks/token_refresh.py", "_refresh_all"): TENANT_CATALOGUE,
    ("core/crypto/backfill_connector_secrets.py", "backfill"): TENANT_CATALOGUE,
    ("core/crypto/verify_all.py", "discover_tenant_company_scopes"): TENANT_CATALOGUE,
    ("core/billing/spend.py", "_spend_by_model"): OPERATOR_CLI,
    ("core/billing/spend.py", "_spend_by_tenant"): OPERATOR_CLI,
    ("core/ai_providers/resolver.py", "_tenant_fallback_allowed"): "reads only the tenants catalogue (no RLS)",
    ("core/crypto/tenant_secrets.py", "_resolve_kek"): "reads only the tenants catalogue (no RLS)",
    (
        "core/llm/router.py",
        "_todays_gemini_spend_usd",
    ): "platform total comes from the restricted SECURITY DEFINER aggregate (v6z85), never an RLS read",
    ("core/spend/gpu.py", "materialise_config_hours"): PLATFORM_TABLE,
    ("core/spend/gpu.py", "_claim"): PLATFORM_TABLE,
    ("core/spend/gpu.py", "_close"): PLATFORM_TABLE,
    ("core/spend/gpu.py", "allocate_pending"): PLATFORM_TABLE,
    ("core/spend/gpu.py", "record_hours"): PLATFORM_TABLE,
    ("core/spend/gpu.py", "list_pool_hours"): PLATFORM_TABLE,
    ("core/cdc/receiver.py", "get_cdc_event_store"): BINDS_ITSELF,
    ("core/live_feed.py", "get_feed_event_repository"): BINDS_ITSELF,
    ("workflows/event_waits.py", "init"): BINDS_ITSELF,
    ("workflows/state_store.py", "init"): BINDS_ITSELF,
}


def _is_raw_factory_use(node: ast.AST) -> bool:
    """``async_session_factory`` referenced (called or passed), or ``current_session_factory()()``."""
    if isinstance(node, ast.Name) and node.id == "async_session_factory":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "async_session_factory":
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Call)
        and isinstance(node.func.func, ast.Name)
        and node.func.func.id == "current_session_factory"
    )


class _Finder(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.stack: list[str] = []
        self.sites: set[tuple[str, str]] = set()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_function  # noqa: N815 - ast.NodeVisitor dispatch name
    visit_AsyncFunctionDef = _visit_function  # noqa: N815 - ast.NodeVisitor dispatch name

    def generic_visit(self, node: ast.AST) -> None:
        if _is_raw_factory_use(node):
            # Module-level uses are recorded as "<module>"; the innermost
            # enclosing function names the site otherwise.
            self.sites.add((self.rel, self.stack[-1] if self.stack else "<module>"))
        super().generic_visit(node)


@functools.cache
def _raw_sites() -> frozenset[tuple[str, str]]:
    # Parsed once per session: walking every module's AST under coverage
    # tracing is slow enough to trip the per-test timeout.
    sites: set[tuple[str, str]] = set()
    for top in SCANNED:
        for path in sorted((ROOT / top).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel == "core/database.py":
                continue  # defines the factory and the bound helpers
            source = path.read_text(encoding="utf-8")
            # Both forms _is_raw_factory_use matches name one of these identifiers.
            if "async_session_factory" not in source and "current_session_factory" not in source:
                continue
            finder = _Finder(rel)
            finder.visit(ast.parse(source, filename=rel))
            sites |= finder.sites
    return frozenset(sites)


def test_every_raw_session_site_is_bound_or_justified() -> None:
    unexpected = sorted(site for site in _raw_sites() if site not in ALLOWED)
    assert unexpected == [], (
        "raw async_session_factory() use outside the justified list; bind it with "
        "get_tenant_session / bind_tenant_context, or get_platform_session for a documented "
        f"cross-tenant flow (docs/RLS_APP_ROLE.md): {unexpected}"
    )


def test_justified_list_has_no_stale_entries() -> None:
    sites = _raw_sites()
    stale = sorted(site for site in ALLOWED if site not in sites)
    assert stale == [], f"remove entries that no longer hold a raw session: {stale}"


def test_tenant_catalogue_sites_guard_with_row_security_off() -> None:
    for (rel, name), reason in ALLOWED.items():
        if reason != TENANT_CATALOGUE:
            continue
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        func = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
        )
        source = ast.get_source_segment((ROOT / rel).read_text(encoding="utf-8"), func) or ""
        assert "SET LOCAL row_security = off" in source, (rel, name)
        assert "Tenant" in source, (rel, name)


def test_demo_receipt_uses_the_database_session_helpers() -> None:
    """The demo receipt must not keep a private session factory.

    A module-level ``async_session_factory`` import in ``api.v1.demo`` would
    bypass both the NOBYPASSRLS platform/tenant binding and the factory that
    ``tests/integration/test_lead_delivery_postgres.py`` installs through
    ``core.database.current_session_factory``.
    """
    from api.v1 import demo
    from core import database

    assert not hasattr(demo, "async_session_factory")
    assert demo.get_platform_session is database.get_platform_session
    assert demo.get_tenant_session is database.get_tenant_session
