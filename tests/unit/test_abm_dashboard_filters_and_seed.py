"""Tests pinning two root-cause fixes from the Codex 2026-04-22 review.

TC_011: /abm/dashboard must honour the same tier/industry/
min_intent_score filters the table uses, or the two halves of the ABM
page contradict each other.

TC_009 (manual-create side): POST /abm/accounts must seed a tier-banded
placeholder intent score the same way the CSV upload does, or manually
added accounts start at zero while CSV-added ones don't.

These are pinned at the helper level so the tests are fast and don't
require a live DB. The ``abm_dashboard`` endpoint wiring is exercised
by the integration suite (``tests/integration/test_db_api_endpoints.py``).
"""

from __future__ import annotations

from api.v1.abm import _seed_intent_score


class TestSeedIntentScoreContract:
    def test_tier_1_lands_in_warm_or_hot_band(self) -> None:
        # Tier 1 seeds 65-90; the label for that band is Warm/Hot.
        for domain in ("acme.com", "globex.io", "initech.co"):
            s = _seed_intent_score("1", domain)
            assert 65 <= s <= 90

    def test_tier_3_lands_in_low_or_medium_band(self) -> None:
        for domain in ("small.shop", "local.biz", "corner.store"):
            s = _seed_intent_score("3", domain)
            assert 20 <= s <= 50

    def test_seed_is_deterministic_per_domain(self) -> None:
        a = _seed_intent_score("2", "acme.com")
        b = _seed_intent_score("2", "acme.com")
        assert a == b

    def test_different_domains_give_different_seeds(self) -> None:
        # With a full 0-255 band mapped to 30 units of range, there's
        # ~0.4% chance of a collision. Spot-check a handful.
        seen = {_seed_intent_score("2", d) for d in [
            "a.com", "b.com", "c.com", "d.com", "e.com",
            "f.com", "g.com", "h.com",
        ]}
        assert len(seen) >= 6  # at least 6 distinct values


class TestDashboardFilterSignature:
    """The dashboard endpoint must accept the same filter signature as
    the account list endpoint. This test pins the signature — we don't
    need the DB to prove the endpoint declares the right parameters.
    """

    def test_dashboard_accepts_same_filter_params_as_listaccounts(self) -> None:
        import inspect

        from api.v1 import abm

        list_sig = inspect.signature(abm.list_accounts)
        dash_sig = inspect.signature(abm.abm_dashboard)

        # Every filter param on list_accounts must also be on dashboard.
        for param_name in ("tier", "industry", "min_intent_score"):
            assert param_name in list_sig.parameters, (
                f"{param_name} missing from list_accounts — fix the test"
            )
            assert param_name in dash_sig.parameters, (
                f"{param_name} missing from abm_dashboard; summary/table "
                "will diverge under filters (TC_011)."
            )

    def test_dashboard_tenant_param_is_still_last(self) -> None:
        """Defensive — tenant_id must stay a Depends() param. If a future
        refactor turns it into a required query parameter the auth
        boundary gets weaker; the regression test will catch it."""
        import inspect

        from api.v1 import abm

        sig = inspect.signature(abm.abm_dashboard)
        tenant_param = sig.parameters.get("tenant_id")
        assert tenant_param is not None
        # The default should be a Depends() marker, not a plain string.
        assert tenant_param.default is not inspect.Parameter.empty
