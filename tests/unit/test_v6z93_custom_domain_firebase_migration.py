# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""v6z93: merchant custom domains record their provider; Firebase Hosting is an allowed mapping source."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from core.models.merchant_custom_domain import PROVIDERS, MerchantCustomDomainRow

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z93_custom_domain_firebase.py"


def _module():
    spec = importlib.util.spec_from_file_location("v6_z93_custom_domain_firebase", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain() -> None:
    module = _module()
    assert module.revision == "v6z93_custom_domain_firebase" and len(module.revision) <= 32
    assert module.down_revision == "v6z92_c6z_demo_source"


def test_provider_column_and_checks_match_the_orm() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS provider VARCHAR(24) NOT NULL DEFAULT 'cloud_run_mapping'" in source
    allowed = ", ".join(f"'{provider}'" for provider in PROVIDERS)
    assert f"CHECK (provider IN ({allowed}))" in source
    assert "mapping_source IN ('operator', 'cloud_run_api', 'firebase_hosting')" in source
    column = MerchantCustomDomainRow.__table__.columns["provider"]
    assert column.type.length == 24 and column.nullable is False
    assert MerchantCustomDomainRow.__table__.columns["mapping_source"].type.length >= len("firebase_hosting")
