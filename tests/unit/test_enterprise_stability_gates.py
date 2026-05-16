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


def test_gate_detects_process_local_mutable_store(tmp_path: Path) -> None:
    path = _write(tmp_path, "core/cdc/receiver.py", "_event_store: list[dict] = []\n")

    findings = gates.scan_process_local_state([path], tmp_path)

    assert [finding.category for finding in findings] == ["process_local_state"]
    assert "_event_store" in findings[0].code


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
