"""Aishwarya 18 June 2026 connector and shadow-sample reopen pins."""

from __future__ import annotations

import json
import uuid as _uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest


def test_hubspot_is_first_class_oauth_provider() -> None:
    from api.v1.oauth_connector import _build_authorization_url
    from core.connectors.provider_registry import get_provider, supported_oauth_names

    spec = get_provider("hubspot")
    assert spec is not None
    assert "hubspot" in supported_oauth_names()

    url = _build_authorization_url(
        spec,
        client_id="hubspot-client-id",
        redirect_uri="https://app.agenticorg.ai/api/v1/oauth/callback",
        state="opaque-state",
        extra_config={},
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.netloc == "app.hubspot.com"
    assert parsed.path == "/oauth/authorize"
    assert params["scope"] == [
        "oauth crm.objects.contacts.read crm.objects.deals.read crm.objects.companies.read"
    ]
    assert params["optional_scope"] == ["automation"]
    assert "client_secret" not in params


@pytest.mark.asyncio
async def test_reconnect_can_rebuild_payload_from_existing_encrypted_config(
    monkeypatch,
) -> None:
    from api.v1 import oauth_connector
    from core.connectors.provider_registry import get_provider

    tid = _uuid.UUID("11111111-1111-1111-1111-111111111111")
    connector = SimpleNamespace(
        name="hubspot",
        category="marketing",
        base_url="https://api.hubapi.com",
    )
    config = SimpleNamespace(
        credentials_encrypted={"_encrypted": "ciphertext"},
        config={"base_url": "https://api.hubapi.com"},
    )
    results = [
        SimpleNamespace(scalar_one_or_none=lambda: connector),
        SimpleNamespace(scalar_one_or_none=lambda: config),
    ]
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=results)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(oauth_connector, "get_tenant_session", lambda _: ctx)
    monkeypatch.setattr(
        oauth_connector,
        "decrypt_for_tenant",
        lambda _: json.dumps(
            {
                "client_id": "hubspot-client-id",
                "client_secret": "hubspot-client-secret",
                "refresh_token": "refresh-token",
            }
        ),
    )

    payload, token = await oauth_connector._payload_from_existing_connector_config(
        tenant_id=tid,
        tenant_id_text=str(tid),
        spec=get_provider("hubspot"),
    )

    assert payload["connector_name"] == "hubspot"
    assert payload["user_fields"]["client_id"] == "hubspot-client-id"
    assert payload["user_fields"]["client_secret"] == "hubspot-client-secret"
    assert payload["base_url"] == "https://api.hubapi.com"
    assert token == "refresh-token"


def test_hubspot_crm_read_contract_accepts_healthy_private_app_evidence() -> None:
    from core.marketing.connector_contracts import evaluate_hubspot_crm_read_contract

    contract = evaluate_hubspot_crm_read_contract(
        connector_status="active",
        health_status="healthy",
        tool_functions=["list_contacts", "search_contacts", "list_deals"],
        credentials={"access_token": "private-app-token"},
        config={},
    )

    assert contract.status == "ready"
    assert contract.missing_scopes == ()
    assert "automation" in contract.non_blocking_scope_gaps
    assert "HubSpot CRM read tools registered" in contract.evidence


def test_hubspot_crm_read_contract_blocks_missing_tools() -> None:
    from core.marketing.connector_contracts import evaluate_hubspot_crm_read_contract

    contract = evaluate_hubspot_crm_read_contract(
        connector_status="active",
        health_status="healthy",
        tool_functions=["list_contacts"],
        credentials={
            "scope": "crm.objects.contacts.read crm.objects.deals.read automation"
        },
        config={},
    )

    assert contract.status == "not_ready"
    assert "list_deals" in contract.missing_tools


def test_shadow_sample_counts_valid_unscored_samples() -> None:
    from api.v1.agents import _shadow_metric_update_decision

    decision = _shadow_metric_update_decision(
        agent_status="shadow",
        incoming_action="shadow_sample",
        task_status="completed",
        task_confidence=None,
        tool_calls=[],
    )

    assert decision["sample_counted"] is True
    assert decision["accuracy_updated"] is False
    assert decision["reason"] == "sample_counted_accuracy_pending"


def test_shadow_sample_tool_failures_still_do_not_count() -> None:
    from api.v1.agents import _shadow_metric_update_decision

    decision = _shadow_metric_update_decision(
        agent_status="shadow",
        incoming_action="shadow_sample",
        task_status="completed",
        task_confidence=0.5,
        tool_calls=[{"tool": "list_deals", "status": "error"}],
    )

    assert decision["sample_counted"] is False
    assert decision["accuracy_updated"] is False
    assert decision["reason"] == "tool_failure_not_counted"
