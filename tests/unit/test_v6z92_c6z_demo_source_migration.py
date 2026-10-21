# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""v6z92: the C6Z source checks admit shopify and the synthetic demo source, read-only, and nothing else."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from core.commerce.c6z_runtime_vertical import DEMO_SOURCE_SYSTEM, SHOPIFY_SOURCE_SYSTEM

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z92_c6z_demo_source.py"


def _module():
    spec = importlib.util.spec_from_file_location("v6_z92_c6z_demo_source", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(direction: str) -> list[str]:
    module = _module()
    executed: list[str] = []

    class _Op:
        @staticmethod
        def execute(sql: str) -> None:
            executed.append(sql)

    module.op = _Op()
    getattr(module, direction)()
    return executed


def test_revision_chain_and_id_length() -> None:
    module = _module()
    assert module.revision == "v6z92_c6z_demo_source" and len(module.revision) <= 32
    assert module.down_revision == "v6z91_merchant_custom_domains"


def test_upgrade_replaces_both_checks_with_shopify_or_synthetic_demo_read_only() -> None:
    assert _module().SOURCE_SYSTEMS == (SHOPIFY_SOURCE_SYSTEM, DEMO_SOURCE_SYSTEM)
    sql = _statements("upgrade")
    assert sql == [
        "ALTER TABLE commerce_c6z_seller_onboarding_packets DROP CONSTRAINT IF EXISTS ck_c6z_onboarding_connector;",
        "ALTER TABLE commerce_c6z_seller_onboarding_packets ADD CONSTRAINT ck_c6z_onboarding_connector "
        "CHECK (connector_choice IN ('shopify', 'synthetic_demo') AND connector_mode = 'read_only');",
        "ALTER TABLE commerce_c6z_connector_evidence_records DROP CONSTRAINT IF EXISTS ck_c6z_connector_source;",
        "ALTER TABLE commerce_c6z_connector_evidence_records ADD CONSTRAINT ck_c6z_connector_source "
        "CHECK (source_system IN ('shopify', 'synthetic_demo') AND source_mode = 'read_only');",
    ]


def test_downgrade_restores_the_shopify_only_checks() -> None:
    sql = _statements("downgrade")
    assert len(sql) == 4
    assert all("synthetic_demo" not in statement for statement in sql)
    assert "CHECK (connector_choice IN ('shopify') AND connector_mode = 'read_only');" in sql[1]
    assert "CHECK (source_system IN ('shopify') AND source_mode = 'read_only');" in sql[3]
