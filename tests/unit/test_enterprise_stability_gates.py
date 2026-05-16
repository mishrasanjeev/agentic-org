from __future__ import annotations

from pathlib import Path

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
