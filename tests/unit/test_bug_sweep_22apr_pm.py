"""Pin the 22-Apr-2026 PM bug sweep fixes.

Source-inspection tests — they do NOT replace live reproduction runs,
but they prevent the specific regressions documented in
``feedback_shallow_fix_autopsy.md`` from silently coming back in a
future edit.
"""

from __future__ import annotations

import inspect


class TestReportScheduleDefensive:
    def test_create_wraps_unexpected_errors(self) -> None:
        """TC_001 REOPEN: the endpoint must never leak a raw HTTP 500
        for an unexpected failure — the route wraps the whole body in
        a try/except that returns a structured 500 detail."""
        from api.v1 import report_schedules

        src = inspect.getsource(report_schedules.create_report_schedule)
        assert "except HTTPException:" in src
        assert "Could not create the report schedule" in src
        assert "raise HTTPException(" in src


class TestChatConfidenceContract:
    def test_no_hardcoded_60_percent_cap(self) -> None:
        """TC_003 REOPEN: the ``min(confidence, 0.6)`` cap on tool-less
        answers is gone. Without this cap, LLM-only reasoning can
        report its actual confidence.

        Allow the pattern to appear in *comments* (the fix intentionally
        quotes the old code to explain what changed) but fail when it
        appears in executable lines.
        """
        from api.v1 import chat

        src = inspect.getsource(chat.chat_query)
        lines = [
            line for line in src.splitlines()
            if "min(confidence, 0.6)" in line and not line.lstrip().startswith("#")
        ]
        assert not lines, (
            f"Executable line still caps confidence at 0.6: {lines!r}"
        )
        # Default-initialisation also can't wedge in a constant.
        default_lines = [
            line for line in src.splitlines()
            if "confidence = 0.6 if domain ==" in line
            and not line.lstrip().startswith("#")
        ]
        assert not default_lines


class TestAbmTierAcceptsSemantic:
    def test_account_create_accepts_strategic(self) -> None:
        from api.v1.abm import AccountCreate

        a = AccountCreate(company_name="Acme", domain="acme.com", tier="Strategic")
        assert a.tier == "1"  # normalized to numeric code

    def test_account_create_accepts_numeric(self) -> None:
        from api.v1.abm import AccountCreate

        a = AccountCreate(company_name="Acme", domain="acme.com", tier="2")
        assert a.tier == "2"

    def test_account_create_rejects_unknown(self) -> None:
        import pytest
        from pydantic import ValidationError

        from api.v1.abm import AccountCreate

        with pytest.raises(ValidationError):
            AccountCreate(company_name="Acme", domain="acme.com", tier="Weird")


class TestAbmLastActivityFallback:
    def test_account_to_dict_falls_back_to_created_at(self) -> None:
        from datetime import UTC, datetime

        from api.v1.abm import _account_to_dict

        created = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)

        class _Stub:
            id = "00000000-0000-0000-0000-000000000001"
            name = "Acme"
            domain = "acme.com"
            industry = None
            revenue = None
            tier = "2"
            intent_score = 55
            metadata_ = None
            created_at = created
            updated_at = None

        out = _account_to_dict(_Stub())
        assert out["updated_at"] == created.isoformat()


class TestFleetLimitsDefaults:
    def test_fleet_limits_default_populates_six_domains(self) -> None:
        """TC_011: empty ``max_agents_per_domain`` hid every input on
        the Settings page. Default must have all canonical domains."""
        from core.schemas.api import FleetLimits

        limits = FleetLimits()
        assert "finance" in limits.max_agents_per_domain
        assert "hr" in limits.max_agents_per_domain
        assert "marketing" in limits.max_agents_per_domain
        assert "ops" in limits.max_agents_per_domain
        assert "backoffice" in limits.max_agents_per_domain
        assert "comms" in limits.max_agents_per_domain


class TestConnectorDeleteEndpoint:
    def test_delete_route_exists(self) -> None:
        from api.v1 import connectors

        assert any(
            getattr(r, "path", "") == "/connectors/{conn_id}"
            and "DELETE" in getattr(r, "methods", set())
            for r in connectors.router.routes
        )


class TestTallyDetectNoRawErrors:
    def test_source_no_longer_interpolates_raw_exception(self) -> None:
        """Uday BUG-005: the old error put ``{exc}`` into ``address``,
        leaking 'Expecting value: line 1 column 1 (char 0)' to users.
        The new code uses a fixed generic_failure_hint."""
        from api.v1 import companies

        src = inspect.getsource(companies.tally_detect)
        assert "generic_failure_hint" in src
        assert "Could not connect to Tally bridge at {body.bridge_url}: {exc}" not in src


class TestBillingConfigurationMessage:
    def test_stripe_missing_returns_503_with_actionable(self) -> None:
        from api.v1 import billing

        src = inspect.getsource(billing.subscribe_stripe)
        assert "STRIPE_SECRET_KEY" in src
        assert "503" in src
        assert "Stripe is not configured" in src

    def test_india_missing_returns_503_with_actionable(self) -> None:
        from api.v1 import billing

        src = inspect.getsource(billing.subscribe_india)
        assert "PINELABS_API_KEY" in src
        assert "503" in src
