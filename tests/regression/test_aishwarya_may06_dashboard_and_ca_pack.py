from __future__ import annotations

import inspect


def test_partner_dashboard_excludes_inactive_clients_from_metrics_and_deadlines() -> None:
    from api.v1.companies import PartnerDashboardOut, get_partner_dashboard

    fields = set(PartnerDashboardOut.model_fields)
    assert {"inactive_clients", "metrics_scope"}.issubset(fields)

    source = inspect.getsource(get_partner_dashboard)
    assert "active_company_ids" in source
    assert "metrics_scope=\"active_clients_only\"" in source
    assert "Company.is_active.is_(True)" in source
    assert '"metrics_included": is_active_client' in source
    assert '"health_score": health_score' in source


def test_partner_dashboard_returns_trial_expiry_metadata_for_subscription_badges() -> None:
    from api.v1.companies import get_partner_dashboard

    source = inspect.getsource(get_partner_dashboard)
    assert "CASubscription" in source
    assert "trial_ends_at" in source
    assert "trial_days_remaining" in source


def test_ca_pack_tds_agent_uses_one_canonical_26q_write_tool() -> None:
    from core.agents.packs.ca import CA_PACK

    tds_agent = next(a for a in CA_PACK["agents"] if a["name"] == "TDS Compliance Agent")
    tools = tds_agent["tools"]

    assert "income_tax_india:file_26q_return" in tools
    assert "income_tax_india:file_form_26q" not in tools
    assert len(tools) == len(set(tools))
