# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Phase 4 (G55, G56, G58, G61) guard rails that need no database.

* The new runtime routes are authenticated tenant-admin writes with their ledger events.
* No new commerce module waits, sleeps or reaches for the review queue.
* The migration gives both payment record tables forced row-level security and an
  append-only trigger, and admits the ``refund`` arbiter action.
* The UPI AutoPay extension is advertised only to a merchant that offers it.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "versions" / "v6_z90_mandates_rails.py"
NEW_MODULES = (
    "core/commerce/india_rails.py",
    "core/commerce/payment_records.py",
    "core/commerce/order_refunds.py",
    "core/commerce/executors/plural.py",
    "core/commerce/fulfillment_tracking.py",
    "core/commerce/source_precedence.py",
)


def test_new_runtime_routes_are_authenticated_ledgered_writes() -> None:
    from fastapi.routing import APIRoute

    from api.route_metadata import ROUTE_METADATA_ATTR
    from api.v1 import commerce_ucp

    expected = {
        "/commerce/runtime/ucp/orders/{order_id}/refunds": "commerce.ucp.order.refund",
        "/commerce/runtime/ucp/orders/{order_id}/shopify-fulfillments": "commerce.ucp.order.update",
        "/commerce/runtime/ucp/plural/events": "commerce.ucp.plural.event",
    }
    routes = {r.path: r for r in commerce_ucp.runtime_router.routes if isinstance(r, APIRoute)}
    for path, event in expected.items():
        route = routes[path]
        meta = getattr(route.endpoint, ROUTE_METADATA_ATTR)
        assert route.methods == {"POST"}
        assert meta["auth_required"] is True and meta["tenant_required"] is True
        assert meta["scope"] == "commerce.runtime.ucp.orders.write"
        assert meta["audit_event"] == event
    # The runtime router requires a tenant administrator for every route.
    assert commerce_ucp.runtime_router.dependencies


def test_no_new_commerce_module_waits_or_touches_the_review_queue() -> None:
    for relative in NEW_MODULES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            assert not isinstance(node, ast.While), relative
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"sleep", "wait_for", "wait"}, (relative, node.attr)
            if isinstance(node, ast.Name):
                assert node.id not in {"sleep"}, relative
        assert "hitl" not in source.lower(), relative


def test_migration_forces_rls_and_append_only_on_payment_records() -> None:
    spec = importlib.util.spec_from_file_location("v6z90", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.revision == "v6z90_mandates_rails" and len(module.revision) <= 32
    assert module.down_revision == "v6z89_commerce_sessions_orders"
    assert set(module.PAYMENT_RECORD_TABLES) == {"commerce_payment_operations", "commerce_afa_evidence"}
    source = MIGRATION.read_text(encoding="utf-8")
    for clause in (
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))",
        "BEFORE UPDATE OR DELETE ON {table}",
        "commerce_payment_record_reject_mutation()",
        "'session_complete', 'refund'",
        "GRANT SELECT, INSERT ON commerce_payment_operations TO agenticorg_app",
        "REVOKE UPDATE, DELETE ON commerce_afa_evidence FROM agenticorg_app",
    ):
        assert clause in source, clause


def test_models_declare_the_migration_constraints() -> None:
    from core.models.commerce_payments import CommerceAfaEvidenceRow, CommercePaymentOperationRow

    names = {c.name for c in CommercePaymentOperationRow.__table__.constraints}
    assert {"uq_commerce_payment_operations", "ck_commerce_payment_operations_phase"} <= names
    names = {c.name for c in CommerceAfaEvidenceRow.__table__.constraints}
    assert {"uq_commerce_afa_evidence_phase", "ck_commerce_afa_evidence_ref"} <= names


def test_upi_mandate_extension_is_advertised_only_with_upi_autopay() -> None:
    from core.commerce.india_rails import UCP_UPI_MANDATE_EXTENSION, UCP_UPI_MANDATE_SCHEMA
    from core.commerce.ucp.capabilities import DEFAULT_REGISTRY, RegistryContext
    from tests import ucp_txn_fixtures as uf

    tenant = "11111111-1111-4111-8111-111111111111"
    names = [e.name for e in DEFAULT_REGISTRY.capabilities(uf.merchant(tenant), "2026-08-25", RegistryContext())]
    assert UCP_UPI_MANDATE_EXTENSION not in names
    capability = {
        **uf.ucp_settings()["mandate_capability"],
        "instrument_types": ["card", "upi_autopay"],
        "upi_autopay": {
            "amount_rule": "max",
            "max_amount_minor_units": 500000,
            "frequency": "monthly",
            "validity_days": 365,
            "authorization_url": "https://payments.example/upi/{mandate_reference}",
        },
    }
    merchant = uf.merchant(tenant, mandate_capability=capability)
    entries = {e.name: e for e in DEFAULT_REGISTRY.capabilities(merchant, "2026-08-25", RegistryContext())}
    assert UCP_UPI_MANDATE_EXTENSION in entries
    assert entries[UCP_UPI_MANDATE_EXTENSION].profile_entry(merchant, "2026-08-25")["schema"] == UCP_UPI_MANDATE_SCHEMA
    assert UCP_UPI_MANDATE_EXTENSION not in [
        e.name for e in DEFAULT_REGISTRY.capabilities(merchant, "2026-04-08", RegistryContext())
    ]


def test_billing_webhook_still_verifies_through_the_shared_signature_function() -> None:
    import base64
    import hashlib
    import hmac

    from core.billing import pinelabs_client

    secret = base64.b64encode(b"synthetic-billing-secret").decode()
    body = b'{"data":{"order_id":"v1-1"}}'
    signature = base64.b64encode(
        hmac.new(base64.b64decode(secret), b"wh_1.1700000000." + body, hashlib.sha256).digest()
    ).decode()
    assert pinelabs_client.plural_signature_valid(secret, body, "wh_1", "1700000000", f"v1,{signature}")
    assert not pinelabs_client.plural_signature_valid(secret, body, "wh_1", "1700000000", "v1,AAAA")
    assert not pinelabs_client.plural_signature_valid("", body, "wh_1", "1700000000", f"v1,{signature}")
