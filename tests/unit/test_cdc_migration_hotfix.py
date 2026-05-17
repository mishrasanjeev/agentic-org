"""Regression coverage for CDC durability migration on legacy stamped DBs."""

from __future__ import annotations

from pathlib import Path


def test_cdc_migration_recreates_legacy_events_table_before_alter() -> None:
    migration = Path("migrations/versions/v4_9_13_cdc_webhook_durability.py").read_text()

    create_pos = migration.index("CREATE TABLE IF NOT EXISTS cdc_events")
    alter_pos = migration.index("ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS provider_event_id")

    assert create_pos < alter_pos
    assert "ix_cdc_events_tenant_id" in migration
    assert "ix_cdc_events_connector" in migration
    assert "ix_cdc_events_event_hash" in migration
