from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from api.route_metadata import ROUTE_METADATA_ATTR, route_meta
from scripts import check_enterprise_stability_gates as gates


def _write(root: Path, rel_path: str, content: str) -> Path:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_gate_detects_unannotated_broad_exception(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "api/v1/example.py",
        "def route():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        pass\n",
    )

    findings = gates.scan_broad_exceptions([path], tmp_path)

    assert [finding.category for finding in findings] == ["broad_exception"]
    assert findings[0].path == "api/v1/example.py"


def test_gate_allows_broad_exception_with_non_empty_reason(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "api/v1/example.py",
        "def route():\n"
        "    try:\n"
        "        risky()\n"
        "    # enterprise-gate: broad-except-ok reason=best-effort cleanup\n"
        "    except Exception:\n"
        "        pass\n",
    )

    assert gates.scan_broad_exceptions([path], tmp_path) == []


def test_broad_exception_allowance_requires_non_empty_reason(tmp_path: Path) -> None:
    missing_reason = _write(
        tmp_path,
        "api/v1/example.py",
        "def route():\n"
        "    try:\n"
        "        risky()\n"
        "    # enterprise-gate: broad-except-ok\n"
        "    except Exception:\n"
        "        pass\n",
    )
    allowed = _write(
        tmp_path,
        "api/v1/allowed.py",
        "def route():\n"
        "    try:\n"
        "        cleanup()\n"
        "    # enterprise-gate: broad-except-ok reason=best-effort cleanup\n"
        "    except Exception:\n"
        "        pass\n",
    )

    findings = gates.scan_broad_exceptions([missing_reason, allowed], tmp_path)

    assert [finding.category for finding in findings] == ["broad_exception"]
    assert findings[0].path == "api/v1/example.py"


def test_gate_detects_process_local_mutable_store(tmp_path: Path) -> None:
    path = _write(tmp_path, "core/cdc/receiver.py", "_event_store: list[dict] = []\n")

    findings = gates.scan_process_local_state([path], tmp_path)

    assert [finding.category for finding in findings] == ["process_local_state"]
    assert "_event_store" in findings[0].code


def test_gate_detects_process_local_suspicious_suffixes(tmp_path: Path) -> None:
    cases = {
        "_payment_map",
        "_payment_maps",
        "_revoked_tokens",
        "_refresh_token",
        "_rate_window",
        "_rate_windows",
        "_tenant_bucket",
        "_tenant_buckets",
        "_event_buffer",
        "_event_buffers",
        "_job_queue",
        "_job_queues",
        "_resume_pending",
        "_event_listeners",
        "_active_sessions",
        "_tenant_locks",
        "_workflow_states",
    }
    content = "\n".join(f"{name}: dict[str, object] = {{}}" for name in sorted(cases))
    path = _write(tmp_path, "core/stateful.py", f"{content}\n")

    findings = gates.scan_process_local_state([path], tmp_path)

    assert len(findings) == len(cases)
    detected = {finding.code for finding in findings}
    assert detected == cases


def test_gate_detects_queue_like_module_state(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "core/queue_state.py",
        "from queue import Queue\n"
        "_event_queue = Queue()\n",
    )

    findings = gates.scan_process_local_state([path], tmp_path)

    assert [finding.category for finding in findings] == ["process_local_state"]
    assert findings[0].code == "_event_queue"


def test_gate_ignores_immutable_constants_and_local_variables(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "core/constants.py",
        "ORDER_MAP: tuple[tuple[str, str], ...] = (('a', 'b'),)\n"
        "def build_local():\n"
        "    local_map: dict[str, object] = {}\n"
        "    return local_map\n",
    )

    assert gates.scan_process_local_state([path], tmp_path) == []


def test_process_local_allowance_requires_non_empty_reason(tmp_path: Path) -> None:
    missing_reason = _write(
        tmp_path,
        "core/cache_example.py",
        "_registry: dict[str, object] = {}  # enterprise-gate: process-local-ok\n",
    )
    allowed = _write(
        tmp_path,
        "core/allowed_cache.py",
        "_cache: dict[str, object] = {}  # enterprise-gate: process-local-ok reason=bounded-local-cache\n",
    )

    findings = gates.scan_process_local_state([missing_reason, allowed], tmp_path)

    assert [finding.category for finding in findings] == ["process_local_state"]
    assert findings[0].path == "core/cache_example.py"


def test_gate_detects_stub_success_status(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "workflows/step_types.py",
        "def execute():\n"
        "    return {'status': 'completed', 'output': {}}\n",
    )

    findings = gates.scan_stub_success([path], tmp_path)

    assert [finding.category for finding in findings] == ["stub_success_status"]


def test_gate_detects_stub_reference_without_annotation(tmp_path: Path) -> None:
    path = _write(tmp_path, "connectors/foo.py", "def execute_stub():\n    return None\n")

    findings = gates.scan_stub_success([path], tmp_path)

    assert [finding.category for finding in findings] == ["stub_path"]


def test_parse_alembic_multiple_heads() -> None:
    output = "v4913_cdc (head)\nv4914_feed (head)\n"

    assert gates.parse_alembic_heads(output) == ["v4913_cdc", "v4914_feed"]


def test_route_inventory_metadata_enforcement(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "api/v1/example.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/example')\n"
        "# enterprise-route: auth_required=true tenant_required=true "
        "idempotency=none rate_limit=standard audit_event=example.list\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    return []\n",
    )

    routes = gates.scan_routes([path], tmp_path)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 1
    assert routes[0].path == "/api/v1/example/items"
    assert routes[0].metadata_present is True
    assert findings == []


def test_route_metadata_helper_attaches_metadata_to_http_handler() -> None:
    router = APIRouter()

    @router.get("/items")
    @route_meta(
        auth_required=True,
        tenant_required=True,
        scope="items.read",
        rate_limit="standard",
        idempotency="read-only",
        audit_event="items.list",
    )
    def list_items() -> list[dict[str, str]]:
        return []

    metadata = getattr(list_items, ROUTE_METADATA_ATTR)

    assert metadata["auth_required"] is True
    assert metadata["tenant_required"] is True
    assert metadata["scope"] == "items.read"
    assert metadata["audit_event"] == "items.list"


def test_route_metadata_helper_attaches_metadata_to_websocket_handler() -> None:
    router = APIRouter()

    @router.websocket("/ws/items")
    @route_meta(
        auth_required=True,
        tenant_required=True,
        scope="items.websocket",
        rate_limit="websocket-connect",
        idempotency="connection-session",
        audit_event="items.websocket.connect",
    )
    async def items_socket() -> None:
        return None

    metadata = getattr(items_socket, ROUTE_METADATA_ATTR)

    assert metadata["scope"] == "items.websocket"
    assert metadata["rate_limit"] == "websocket-connect"


def test_gate_reads_route_metadata_helper(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "api/v1/example.py",
        "from fastapi import APIRouter\n"
        "from api.route_metadata import route_meta\n"
        "router = APIRouter(prefix='/example')\n"
        "@router.post('/items')\n"
        "@route_meta(auth_required=True, tenant_required=True, scope='items.write', "
        "rate_limit='standard', idempotency='client-key', audit_event='items.create')\n"
        "def create_item():\n"
        "    return {'ok': True}\n",
    )

    routes = gates.scan_routes([path], tmp_path)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 1
    assert routes[0].metadata_present is True
    assert routes[0].scope == "items.write"
    assert routes[0].idempotency == "client-key"
    assert findings == []


def test_new_public_route_without_annotation_fails_gate(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "api/v1/auth.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/auth')\n"
        "@router.post('/login')\n"
        "def login():\n"
        "    return {'ok': True}\n",
    )

    routes = gates.scan_routes([path], tmp_path)
    findings = gates.route_metadata_findings(routes)

    assert {finding.category for finding in findings} == {
        "route_missing_metadata",
        "public_mutating_route_missing_metadata",
    }


def test_route_metadata_findings_cannot_be_baselined(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "api/v1/example.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/example')\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    return []\n",
    )

    routes = gates.scan_routes([path], tmp_path)
    findings = gates.route_metadata_findings(routes)
    blocked, allowed = gates.filter_baselined(
        findings,
        {
            "allowed_findings": {},
            "routes_missing_metadata": [
                f"{findings[0].code} {findings[0].path}:{findings[0].line}"
            ],
        },
    )

    assert len(findings) == 1
    assert allowed == []
    assert blocked == findings


def test_route_metadata_baseline_allowance_fails_gate() -> None:
    findings = gates.route_metadata_baseline_findings(
        {"routes_missing_metadata": ["GET /api/v1/example api/v1/example.py:10"]}
    )

    assert [finding.category for finding in findings] == ["route_metadata_baseline"]
    assert "no longer permitted" in findings[0].message


def test_route_metadata_debt_is_hard_zero() -> None:
    routes = gates.scan_routes(gates.production_python_files(gates.REPO_ROOT), gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)
    baseline = gates.load_baseline(gates.DEFAULT_BASELINE)

    assert findings == []
    assert baseline.get("routes_missing_metadata") == []


def test_fixed_process_local_state_entries_are_no_longer_baselined() -> None:
    baseline = gates.load_baseline(gates.DEFAULT_BASELINE)
    process_local_entries = baseline.get("allowed_findings", {}).get("process_local_state", [])

    fixed_paths = {
        "api/v1/branding.py",
        "api/v1/composio.py",
        "api/websocket/feed.py",
        "auth/jwt.py",
        "bridge/server_handler.py",
        "core/ai_providers/resolver.py",
        "core/ai_providers/settings.py",
        "core/billing/pinelabs_client.py",
        "core/connectors/provider_registry.py",
        "core/feature_flags.py",
        "core/langgraph/tool_adapter.py",
    }
    assert not any(
        any(f"process_local_state:{path}:" in entry for path in fixed_paths)
        for entry in process_local_entries
    )


def test_process_local_state_baseline_is_zero_after_burndown() -> None:
    baseline = gates.load_baseline(gates.DEFAULT_BASELINE)
    process_local_entries = baseline.get("allowed_findings", {}).get("process_local_state", [])

    assert process_local_entries == []


def test_broad_exception_baseline_reduced_by_runtime_slice() -> None:
    baseline = gates.load_baseline(gates.DEFAULT_BASELINE)
    broad_entries = baseline.get("allowed_findings", {}).get("broad_exception", [])

    assert len(broad_entries) < 439
    assert len(broad_entries) <= 399


def test_broad_exception_baseline_reduced_by_payment_callback_connector_slice() -> None:
    baseline = gates.load_baseline(gates.DEFAULT_BASELINE)
    broad_entries = baseline.get("allowed_findings", {}).get("broad_exception", [])

    assert len(broad_entries) < 387
    assert len(broad_entries) <= 352


def test_docs_tests_and_migrations_are_ignored(tmp_path: Path) -> None:
    paths = [
        _write(tmp_path, "tests/test_example.py", "try:\n    risky()\nexcept Exception:\n    pass\n"),
        _write(tmp_path, "docs/example.py", "try:\n    risky()\nexcept Exception:\n    pass\n"),
        _write(tmp_path, "migrations/versions/x.py", "try:\n    risky()\nexcept Exception:\n    pass\n"),
    ]

    assert gates.scan_broad_exceptions(paths, tmp_path) == []


def test_high_risk_target_routes_have_metadata() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/cdc_webhooks.py",
        gates.REPO_ROOT / "api/v1/webhooks.py",
        gates.REPO_ROOT / "api/websocket/feed.py",
        gates.REPO_ROOT / "api/v1/bridge.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 15
    assert findings == []
    assert all(route.metadata_present for route in routes)
    assert all(route.scope for route in routes)
    assert all(route.rate_limit for route in routes)
    assert all(route.idempotency for route in routes)
    assert all(route.audit_event for route in routes)


def test_public_target_routes_include_public_reason() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/cdc_webhooks.py",
        gates.REPO_ROOT / "api/v1/webhooks.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    public_routes = [route for route in routes if route.auth_required is False]

    assert public_routes
    assert all(route.public_exempt_reason for route in public_routes)


def test_bridge_mutating_routes_include_idempotency_and_audit_metadata() -> None:
    routes = gates.scan_routes([gates.REPO_ROOT / "api/v1/bridge.py"], gates.REPO_ROOT)
    mutating_routes = [
        route
        for route in routes
        if any(method in gates.MUTATING_METHODS for method in route.methods)
    ]

    assert {route.path for route in mutating_routes} == {
        "/api/v1/bridge/register",
        "/api/v1/bridge/route/{connector_type}",
        "/api/v1/bridge/{bridge_id}",
    }
    assert all(route.idempotency for route in mutating_routes)
    assert all(route.audit_event for route in mutating_routes)


def test_auth_billing_connector_target_routes_have_metadata() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/auth.py",
        gates.REPO_ROOT / "api/v1/org.py",
        gates.REPO_ROOT / "api/v1/billing.py",
        gates.REPO_ROOT / "api/v1/oauth_connector.py",
        gates.REPO_ROOT / "api/v1/connectors.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 41
    assert findings == []
    assert all(route.metadata_present for route in routes)
    assert all(route.scope for route in routes)
    assert all(route.rate_limit for route in routes)
    assert all(route.idempotency for route in routes)
    assert all(route.audit_event for route in routes)


def test_public_auth_billing_oauth_routes_include_public_reason() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/auth.py",
        gates.REPO_ROOT / "api/v1/org.py",
        gates.REPO_ROOT / "api/v1/billing.py",
        gates.REPO_ROOT / "api/v1/oauth_connector.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    public_routes = [route for route in routes if route.auth_required is False]

    assert {route.path for route in public_routes} >= {
        "/api/v1/auth/login",
        "/api/v1/auth/signup",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/billing/callback",
        "/api/v1/billing/callback/stripe",
        "/api/v1/oauth/callback",
        "/api/v1/org/accept-invite",
    }
    assert all(route.public_exempt_reason for route in public_routes)


def test_mutating_billing_oauth_connector_routes_include_audit_and_idempotency() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/billing.py",
        gates.REPO_ROOT / "api/v1/oauth_connector.py",
        gates.REPO_ROOT / "api/v1/connectors.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    mutating_routes = [
        route
        for route in routes
        if any(method in gates.MUTATING_METHODS for method in route.methods)
    ]

    assert mutating_routes
    assert all(route.audit_event for route in mutating_routes)
    assert all(route.idempotency for route in mutating_routes)


def test_core_execution_target_routes_have_metadata() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/workflows.py",
        gates.REPO_ROOT / "api/v1/approvals.py",
        gates.REPO_ROOT / "api/v1/agents.py",
        gates.REPO_ROOT / "api/v1/companies.py",
        gates.REPO_ROOT / "api/v1/knowledge.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 74
    assert findings == []
    assert all(route.metadata_present for route in routes)
    assert all(route.scope for route in routes)
    assert all(route.rate_limit for route in routes)
    assert all(route.idempotency for route in routes)
    assert all(route.audit_event for route in routes)


def test_core_execution_mutating_routes_include_audit_and_idempotency() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/workflows.py",
        gates.REPO_ROOT / "api/v1/approvals.py",
        gates.REPO_ROOT / "api/v1/agents.py",
        gates.REPO_ROOT / "api/v1/companies.py",
        gates.REPO_ROOT / "api/v1/knowledge.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    mutating_routes = [
        route
        for route in routes
        if any(method in gates.MUTATING_METHODS for method in route.methods)
    ]

    assert len(mutating_routes) >= 45
    assert all(route.audit_event for route in mutating_routes)
    assert all(route.idempotency for route in mutating_routes)


def test_core_execution_sensitive_routes_are_marked_in_scope() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/workflows.py",
        gates.REPO_ROOT / "api/v1/approvals.py",
        gates.REPO_ROOT / "api/v1/agents.py",
        gates.REPO_ROOT / "api/v1/companies.py",
        gates.REPO_ROOT / "api/v1/knowledge.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    by_method_path = {
        (route.methods[0], route.path): route
        for route in routes
        if len(route.methods) == 1
    }

    sensitive_routes = {
        ("GET", "/api/v1/approvals"),
        ("GET", "/api/v1/agents/{agent_id}/budget"),
        ("GET", "/api/v1/companies/{company_id}/credentials"),
        ("POST", "/api/v1/knowledge/search"),
        ("GET", "/api/v1/workflows/runs/{run_id}"),
    }
    assert sensitive_routes <= set(by_method_path)
    assert all(
        "sensitive" in by_method_path[route_key].scope
        for route_key in sensitive_routes
    )


def test_route_metadata_debt_reduced_by_core_execution_slice() -> None:
    routes = gates.scan_routes(gates.production_python_files(gates.REPO_ROOT), gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)
    missing_count = sum(
        finding.category == "route_missing_metadata" for finding in findings
    )

    assert missing_count <= 156


def test_security_admin_target_routes_have_metadata() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/api_keys.py",
        gates.REPO_ROOT / "api/v1/audit.py",
        gates.REPO_ROOT / "api/v1/approval_policies.py",
        gates.REPO_ROOT / "api/v1/tenant_ai_credentials.py",
        gates.REPO_ROOT / "api/v1/tenant_ai_settings.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 17
    assert findings == []
    assert all(route.metadata_present for route in routes)
    assert all(route.auth_required is True for route in routes)
    assert all(route.scope for route in routes)
    assert all(route.rate_limit for route in routes)
    assert all(route.idempotency for route in routes)
    assert all(route.audit_event for route in routes)


def test_security_admin_mutating_routes_include_audit_and_idempotency() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/api_keys.py",
        gates.REPO_ROOT / "api/v1/approval_policies.py",
        gates.REPO_ROOT / "api/v1/tenant_ai_credentials.py",
        gates.REPO_ROOT / "api/v1/tenant_ai_settings.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    mutating_routes = [
        route
        for route in routes
        if any(method in gates.MUTATING_METHODS for method in route.methods)
    ]

    assert len(mutating_routes) == 9
    assert all(route.audit_event for route in mutating_routes)
    assert all(route.idempotency for route in mutating_routes)


def test_security_admin_sensitive_routes_are_marked_in_scope() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/api_keys.py",
        gates.REPO_ROOT / "api/v1/audit.py",
        gates.REPO_ROOT / "api/v1/tenant_ai_credentials.py",
        gates.REPO_ROOT / "api/v1/tenant_ai_settings.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    by_method_path = {
        (route.methods[0], route.path): route
        for route in routes
        if len(route.methods) == 1
    }

    sensitive_routes = {
        ("GET", "/api/v1/audit"),
        ("GET", "/api/v1/org/api-keys"),
        ("GET", "/api/v1/tenant-ai-credentials"),
        ("GET", "/api/v1/tenant-ai-settings"),
    }
    assert sensitive_routes <= set(by_method_path)
    assert all(
        "sensitive" in by_method_path[route_key].scope
        for route_key in sensitive_routes
    )


def test_route_metadata_debt_reduced_by_security_admin_slice() -> None:
    routes = gates.scan_routes(gates.production_python_files(gates.REPO_ROOT), gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)
    missing_count = sum(
        finding.category == "route_missing_metadata" for finding in findings
    )

    assert missing_count <= 139


def test_sso_rpa_target_routes_have_metadata() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/sso.py",
        gates.REPO_ROOT / "api/v1/report_schedules.py",
        gates.REPO_ROOT / "api/v1/rpa.py",
        gates.REPO_ROOT / "api/v1/rpa_schedules.py",
        gates.REPO_ROOT / "api/v1/prompt_templates.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 28
    assert findings == []
    assert all(route.metadata_present for route in routes)
    assert all(route.scope for route in routes)
    assert all(route.rate_limit for route in routes)
    assert all(route.idempotency for route in routes)
    assert all(route.audit_event for route in routes)


def test_public_sso_routes_include_public_reason() -> None:
    routes = gates.scan_routes([gates.REPO_ROOT / "api/v1/sso.py"], gates.REPO_ROOT)
    public_routes = [route for route in routes if route.auth_required is False]

    assert {route.path for route in public_routes} == {
        "/api/v1/auth/sso/providers",
        "/api/v1/auth/sso/{provider_key}/callback",
        "/api/v1/auth/sso/{provider_key}/login",
    }
    assert all(route.public_exempt_reason for route in public_routes)


def test_sso_rpa_mutating_routes_include_audit_and_idempotency() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/sso.py",
        gates.REPO_ROOT / "api/v1/report_schedules.py",
        gates.REPO_ROOT / "api/v1/rpa.py",
        gates.REPO_ROOT / "api/v1/rpa_schedules.py",
        gates.REPO_ROOT / "api/v1/prompt_templates.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    mutating_routes = [
        route
        for route in routes
        if any(method in gates.MUTATING_METHODS for method in route.methods)
    ]

    assert len(mutating_routes) == 15
    assert all(route.audit_event for route in mutating_routes)
    assert all(route.idempotency for route in mutating_routes)


def test_sso_rpa_sensitive_and_external_action_routes_are_marked() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/sso.py",
        gates.REPO_ROOT / "api/v1/report_schedules.py",
        gates.REPO_ROOT / "api/v1/rpa.py",
        gates.REPO_ROOT / "api/v1/rpa_schedules.py",
        gates.REPO_ROOT / "api/v1/prompt_templates.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    by_method_path = {
        (route.methods[0], route.path): route
        for route in routes
        if len(route.methods) == 1
    }

    sensitive_routes = {
        ("GET", "/api/v1/sso/configs"),
        ("GET", "/api/v1/rpa/history"),
        ("GET", "/api/v1/rpa-schedules/{schedule_id}"),
        ("GET", "/api/v1/prompt-templates/{template_id}/history"),
    }
    external_action_routes = {
        ("GET", "/api/v1/auth/sso/{provider_key}/callback"),
        ("POST", "/api/v1/report-schedules/{schedule_id}/run-now"),
        ("POST", "/api/v1/rpa/scripts/{script_id}/run"),
        ("POST", "/api/v1/rpa-schedules/{schedule_id}/run-now"),
    }
    behavior_routes = {
        ("POST", "/api/v1/prompt-templates"),
        ("PUT", "/api/v1/prompt-templates/{template_id}"),
        ("POST", "/api/v1/prompt-templates/{template_id}/rollback"),
    }

    assert sensitive_routes <= set(by_method_path)
    assert external_action_routes <= set(by_method_path)
    assert behavior_routes <= set(by_method_path)
    assert all(
        "sensitive" in by_method_path[route_key].scope
        for route_key in sensitive_routes
    )
    assert all(
        "external" in by_method_path[route_key].scope
        for route_key in external_action_routes
    )
    assert all(
        "behavior" in by_method_path[route_key].scope
        for route_key in behavior_routes
    )


def test_route_metadata_debt_reduced_by_sso_rpa_slice() -> None:
    routes = gates.scan_routes(gates.production_python_files(gates.REPO_ROOT), gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)
    missing_count = sum(
        finding.category == "route_missing_metadata" for finding in findings
    )

    assert missing_count <= 111


def test_business_control_target_routes_have_metadata() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/sales.py",
        gates.REPO_ROOT / "api/v1/abm.py",
        gates.REPO_ROOT / "api/v1/kpis.py",
        gates.REPO_ROOT / "api/v1/departments.py",
        gates.REPO_ROOT / "api/v1/feature_flags.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 33
    assert findings == []
    assert all(route.metadata_present for route in routes)
    assert all(route.auth_required is True for route in routes)
    assert all(route.tenant_required is True for route in routes)
    assert all(route.scope for route in routes)
    assert all(route.rate_limit for route in routes)
    assert all(route.idempotency for route in routes)
    assert all(route.audit_event for route in routes)


def test_business_control_mutating_routes_include_audit_and_idempotency() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/sales.py",
        gates.REPO_ROOT / "api/v1/abm.py",
        gates.REPO_ROOT / "api/v1/departments.py",
        gates.REPO_ROOT / "api/v1/feature_flags.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    mutating_routes = [
        route
        for route in routes
        if any(method in gates.MUTATING_METHODS for method in route.methods)
    ]

    assert len(mutating_routes) == 15
    assert all(route.audit_event for route in mutating_routes)
    assert all(route.idempotency for route in mutating_routes)


def test_business_control_sensitive_and_external_routes_are_marked() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/sales.py",
        gates.REPO_ROOT / "api/v1/abm.py",
        gates.REPO_ROOT / "api/v1/kpis.py",
        gates.REPO_ROOT / "api/v1/departments.py",
        gates.REPO_ROOT / "api/v1/feature_flags.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    by_method_path = {
        (route.methods[0], route.path): route
        for route in routes
        if len(route.methods) == 1
    }

    sensitive_routes = {
        ("GET", "/api/v1/sales/pipeline"),
        ("GET", "/api/v1/abm/accounts"),
        ("GET", "/api/v1/kpis/cfo"),
        ("GET", "/api/v1/departments"),
        ("GET", "/api/v1/feature-flags"),
    }
    external_action_routes = {
        ("POST", "/api/v1/sales/pipeline/process-lead"),
        ("POST", "/api/v1/sales/run-followups"),
        ("POST", "/api/v1/sales/process-inbox"),
        ("GET", "/api/v1/abm/accounts/{account_id}/intent"),
        ("POST", "/api/v1/abm/accounts/{account_id}/campaign"),
    }
    runtime_control_routes = {
        ("POST", "/api/v1/feature-flags"),
        ("GET", "/api/v1/feature-flags/{flag_key}/evaluate"),
        ("DELETE", "/api/v1/feature-flags/{flag_key}"),
    }

    assert sensitive_routes <= set(by_method_path)
    assert external_action_routes <= set(by_method_path)
    assert runtime_control_routes <= set(by_method_path)
    assert all(
        "sensitive" in by_method_path[route_key].scope
        for route_key in sensitive_routes
    )
    assert all(
        "external" in by_method_path[route_key].scope
        for route_key in external_action_routes
    )
    assert all(
        "runtime_control" in by_method_path[route_key].scope
        for route_key in runtime_control_routes
    )


def test_route_metadata_debt_reduced_by_business_control_slice() -> None:
    routes = gates.scan_routes(gates.production_python_files(gates.REPO_ROOT), gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)
    missing_count = sum(
        finding.category == "route_missing_metadata" for finding in findings
    )

    assert missing_count <= 78


def test_platform_target_routes_have_metadata() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/packs.py",
        gates.REPO_ROOT / "api/v1/a2a.py",
        gates.REPO_ROOT / "api/v1/health.py",
        gates.REPO_ROOT / "api/v1/branding.py",
        gates.REPO_ROOT / "api/v1/compliance.py",
        gates.REPO_ROOT / "api/v1/sop.py",
        gates.REPO_ROOT / "api/v1/push.py",
        gates.REPO_ROOT / "api/v1/schemas.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 35
    assert findings == []
    assert all(route.metadata_present for route in routes)
    assert all(route.scope for route in routes)
    assert all(route.rate_limit for route in routes)
    assert all(route.idempotency for route in routes)
    assert all(route.audit_event for route in routes)


def test_platform_public_routes_include_public_reason() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/a2a.py",
        gates.REPO_ROOT / "api/v1/health.py",
        gates.REPO_ROOT / "api/v1/branding.py",
        gates.REPO_ROOT / "api/v1/push.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    public_routes = [route for route in routes if route.auth_required is False]

    assert {(route.methods[0], route.path) for route in public_routes} == {
        ("GET", "/api/v1/a2a/.well-known/agent.json"),
        ("GET", "/api/v1/a2a/agent-card"),
        ("GET", "/api/v1/a2a/agents"),
        ("GET", "/api/v1/branding"),
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/health/liveness"),
        ("GET", "/api/v1/push/vapid-key"),
    }
    assert all(route.public_exempt_reason for route in public_routes)


def test_platform_mutating_routes_include_audit_and_idempotency() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/packs.py",
        gates.REPO_ROOT / "api/v1/a2a.py",
        gates.REPO_ROOT / "api/v1/branding.py",
        gates.REPO_ROOT / "api/v1/compliance.py",
        gates.REPO_ROOT / "api/v1/sop.py",
        gates.REPO_ROOT / "api/v1/push.py",
        gates.REPO_ROOT / "api/v1/schemas.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    mutating_routes = [
        route
        for route in routes
        if any(method in gates.MUTATING_METHODS for method in route.methods)
    ]

    assert len(mutating_routes) == 16
    assert all(route.audit_event for route in mutating_routes)
    assert all(route.idempotency for route in mutating_routes)


def test_platform_sensitive_and_external_routes_are_marked() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/packs.py",
        gates.REPO_ROOT / "api/v1/a2a.py",
        gates.REPO_ROOT / "api/v1/health.py",
        gates.REPO_ROOT / "api/v1/compliance.py",
        gates.REPO_ROOT / "api/v1/sop.py",
        gates.REPO_ROOT / "api/v1/push.py",
        gates.REPO_ROOT / "api/v1/schemas.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    by_method_path = {
        (route.methods[0], route.path): route
        for route in routes
        if len(route.methods) == 1
    }

    sensitive_routes = {
        ("GET", "/api/v1/compliance/evidence-package"),
        ("GET", "/api/v1/health/diagnostics"),
        ("GET", "/api/v1/schemas"),
        ("GET", "/api/v1/schemas/{name}"),
    }
    external_action_routes = {
        ("POST", "/api/v1/a2a/tasks"),
        ("POST", "/api/v1/push/test"),
        ("POST", "/api/v1/sop/upload"),
        ("POST", "/api/v1/sop/parse-text"),
    }
    high_risk_routes = {
        ("POST", "/api/v1/packs/{name}/install"),
        ("DELETE", "/api/v1/packs/{name}"),
        ("POST", "/api/v1/sop/deploy"),
    }

    assert sensitive_routes <= set(by_method_path)
    assert external_action_routes <= set(by_method_path)
    assert high_risk_routes <= set(by_method_path)
    assert all(
        "sensitive" in by_method_path[route_key].scope
        for route_key in sensitive_routes
    )
    assert all(
        "external" in by_method_path[route_key].scope
        for route_key in external_action_routes
    )
    assert all(
        "high_risk" in by_method_path[route_key].scope
        for route_key in high_risk_routes
    )


def test_route_metadata_debt_reduced_by_platform_slice() -> None:
    routes = gates.scan_routes(gates.production_python_files(gates.REPO_ROOT), gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)
    missing_count = sum(
        finding.category == "route_missing_metadata" for finding in findings
    )

    assert missing_count <= 43


def test_final_control_target_routes_have_metadata() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/agent_teams.py",
        gates.REPO_ROOT / "api/v1/delegations.py",
        gates.REPO_ROOT / "api/v1/workflow_variants.py",
        gates.REPO_ROOT / "api/v1/aa_callback.py",
        gates.REPO_ROOT / "api/v1/invoices.py",
        gates.REPO_ROOT / "api/v1/costs.py",
        gates.REPO_ROOT / "api/v1/config.py",
        gates.REPO_ROOT / "api/v1/governance.py",
        gates.REPO_ROOT / "api/v1/chat.py",
        gates.REPO_ROOT / "api/v1/composio.py",
        gates.REPO_ROOT / "api/v1/cron.py",
        gates.REPO_ROOT / "api/v1/evals.py",
        gates.REPO_ROOT / "api/v1/integrations_status.py",
        gates.REPO_ROOT / "api/v1/mcp.py",
        gates.REPO_ROOT / "api/v1/product_facts.py",
        gates.REPO_ROOT / "api/v1/status.py",
        gates.REPO_ROOT / "api/v1/voice.py",
        gates.REPO_ROOT / "api/v1/demo.py",
        gates.REPO_ROOT / "api/v1/content_safety.py",
        gates.REPO_ROOT / "bridge/server_handler.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)

    assert len(routes) == 43
    assert findings == []
    assert all(route.metadata_present for route in routes)
    assert all(route.scope for route in routes)
    assert all(route.rate_limit for route in routes)
    assert all(route.idempotency for route in routes)
    assert all(route.audit_event for route in routes)


def test_final_control_public_routes_include_public_reason() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/aa_callback.py",
        gates.REPO_ROOT / "api/v1/cron.py",
        gates.REPO_ROOT / "api/v1/evals.py",
        gates.REPO_ROOT / "api/v1/mcp.py",
        gates.REPO_ROOT / "api/v1/product_facts.py",
        gates.REPO_ROOT / "api/v1/status.py",
        gates.REPO_ROOT / "api/v1/demo.py",
        gates.REPO_ROOT / "bridge/server_handler.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    public_routes = [route for route in routes if route.auth_required is False]

    assert {(route.methods[0], route.path) for route in public_routes} == {
        ("POST", "/api/v1/aa/consent/callback"),
        ("GET", "/api/v1/cron/schedules"),
        ("POST", "/api/v1/cron/compliance-alerts"),
        ("GET", "/api/v1/evals"),
        ("GET", "/api/v1/evals/agent/{agent_type}"),
        ("GET", "/api/v1/mcp/tools"),
        ("GET", "/api/v1/product-facts"),
        ("GET", "/api/v1/status"),
        ("POST", "/api/v1/demo-request"),
        ("WEBSOCKET", "/api/v1/ws/bridge/{bridge_id}"),
    }
    assert all(route.public_exempt_reason for route in public_routes)


def test_final_control_mutating_routes_include_audit_and_idempotency() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/agent_teams.py",
        gates.REPO_ROOT / "api/v1/delegations.py",
        gates.REPO_ROOT / "api/v1/workflow_variants.py",
        gates.REPO_ROOT / "api/v1/aa_callback.py",
        gates.REPO_ROOT / "api/v1/invoices.py",
        gates.REPO_ROOT / "api/v1/config.py",
        gates.REPO_ROOT / "api/v1/governance.py",
        gates.REPO_ROOT / "api/v1/chat.py",
        gates.REPO_ROOT / "api/v1/cron.py",
        gates.REPO_ROOT / "api/v1/mcp.py",
        gates.REPO_ROOT / "api/v1/voice.py",
        gates.REPO_ROOT / "api/v1/demo.py",
        gates.REPO_ROOT / "api/v1/content_safety.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    mutating_routes = [
        route
        for route in routes
        if any(method in gates.MUTATING_METHODS for method in route.methods)
    ]

    assert len(mutating_routes) == 18
    assert all(route.audit_event for route in mutating_routes)
    assert all(route.idempotency for route in mutating_routes)


def test_final_control_sensitive_and_external_routes_are_marked() -> None:
    target_paths = [
        gates.REPO_ROOT / "api/v1/agent_teams.py",
        gates.REPO_ROOT / "api/v1/delegations.py",
        gates.REPO_ROOT / "api/v1/workflow_variants.py",
        gates.REPO_ROOT / "api/v1/aa_callback.py",
        gates.REPO_ROOT / "api/v1/invoices.py",
        gates.REPO_ROOT / "api/v1/costs.py",
        gates.REPO_ROOT / "api/v1/config.py",
        gates.REPO_ROOT / "api/v1/governance.py",
        gates.REPO_ROOT / "api/v1/chat.py",
        gates.REPO_ROOT / "api/v1/mcp.py",
        gates.REPO_ROOT / "api/v1/voice.py",
        gates.REPO_ROOT / "bridge/server_handler.py",
    ]

    routes = gates.scan_routes(target_paths, gates.REPO_ROOT)
    by_method_path = {
        (route.methods[0], route.path): route
        for route in routes
        if len(route.methods) == 1
    }

    sensitive_routes = {
        ("GET", "/api/v1/agent-teams"),
        ("GET", "/api/v1/billing/invoices"),
        ("GET", "/api/v1/costs/summary"),
        ("GET", "/api/v1/governance/config"),
        ("GET", "/api/v1/chat/history"),
    }
    external_action_routes = {
        ("POST", "/api/v1/aa/consent/callback"),
        ("POST", "/api/v1/chat/query"),
        ("POST", "/api/v1/mcp/call"),
        ("POST", "/api/v1/voice/test-connection"),
    }
    high_risk_routes = {
        ("POST", "/api/v1/workflows/{workflow_id}/variants"),
        ("DELETE", "/api/v1/workflows/{workflow_id}/variants/{variant_name}"),
        ("PUT", "/api/v1/governance/config"),
        ("WEBSOCKET", "/api/v1/ws/bridge/{bridge_id}"),
    }

    assert sensitive_routes <= set(by_method_path)
    assert external_action_routes <= set(by_method_path)
    assert high_risk_routes <= set(by_method_path)
    assert all(
        "sensitive" in by_method_path[route_key].scope
        for route_key in sensitive_routes
    )
    assert all(
        "external" in by_method_path[route_key].scope
        for route_key in external_action_routes
    )
    assert all(
        "high_risk" in by_method_path[route_key].scope
        or "token_protected" in by_method_path[route_key].scope
        or "enterprise_critical" in by_method_path[route_key].scope
        for route_key in high_risk_routes
    )


def test_route_metadata_debt_eliminated_by_final_control_slice() -> None:
    routes = gates.scan_routes(gates.production_python_files(gates.REPO_ROOT), gates.REPO_ROOT)
    findings = gates.route_metadata_findings(routes)

    assert findings == []
