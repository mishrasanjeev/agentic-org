# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""v6z95: commerce_network_listings with tenant RLS and a SELECT-only platform-context policy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from core.models.commerce_network import LISTING_STATUSES, CommerceNetworkListingRow

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z95_commerce_network_listings.py"


def _module():
    spec = importlib.util.spec_from_file_location("v6_z95_commerce_network_listings", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain() -> None:
    module = _module()
    assert module.revision == "v6z95_network_listings" and len(module.revision) <= 32
    assert module.down_revision == "v6z94_user_totp_mfa"


def test_rls_policies_and_constraints_match_the_orm() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in source and "FORCE ROW LEVEL SECURITY" in source
    assert "FOR SELECT USING ({PLATFORM_CTX_PREDICATE})" in source
    assert "USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})" in source
    statuses = ", ".join(f"'{status}'" for status in LISTING_STATUSES)
    assert f"CHECK (status IN ({statuses}))" in source
    assert "uq_commerce_network_listings_slug ON commerce_network_listings (slug)" in source
    columns = CommerceNetworkListingRow.__table__.columns
    for name in ("slug", "serving_host", "subdomain_host", "terms_version", "opted_in_by", "opted_in_at"):
        assert name in columns
    assert columns["slug"].type.length == 40 and columns["subdomain_host"].nullable is True
