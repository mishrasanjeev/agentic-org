# SPDX-License-Identifier: Apache-2.0
"""v6z91: the merchant custom domain table is tenant-isolated, platform-readable only, and matches the ORM."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from core.models.merchant_custom_domain import (
    CLAIMED_STATUSES,
    CUSTOM_DOMAIN_STATUSES,
    INACTIVE_STATUSES,
    MerchantCustomDomainRow,
)

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z91_merchant_custom_domains.py"


def _module():
    spec = importlib.util.spec_from_file_location("v6_z91_merchant_custom_domains", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_and_id_length() -> None:
    module = _module()
    assert module.revision == "v6z91_merchant_custom_domains" and len(module.revision) <= 32
    assert module.down_revision == "v6z90_mandates_rails"


def test_rls_is_forced_with_a_tenant_policy_and_a_select_only_platform_policy() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE merchant_custom_domains ENABLE ROW LEVEL SECURITY;" in source
    assert "ALTER TABLE merchant_custom_domains FORCE ROW LEVEL SECURITY;" in source
    assert "USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})" in source
    assert "FOR SELECT USING ({PLATFORM_CTX_PREDICATE})" in source
    assert _module().PLATFORM_CTX_PREDICATE == "current_setting('agenticorg.platform_ctx', true) = '1'"


def test_statuses_and_columns_match_the_orm() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for status in CUSTOM_DOMAIN_STATUSES:
        assert f"'{status}'" in source
    for column in MerchantCustomDomainRow.__table__.columns:
        assert f"            {column.name} " in source, column.name
    claimed = ", ".join(f"'{status}'" for status in CLAIMED_STATUSES)
    assert f"WHERE status IN ({claimed})" in source  # failed keeps its claim until removed
    assert "failed" in CLAIMED_STATUSES
    inactive = ", ".join(f"'{status}'" for status in INACTIVE_STATUSES)
    assert f"WHERE status NOT IN ({inactive})" in source
