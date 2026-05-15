"""Regression pins for Uday CA Firms 2026-05-15 connector reopening."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]


def test_zoho_region_infers_from_base_url_without_data_center() -> None:
    from api.v1.connectors import _infer_zoho_region

    assert _infer_zoho_region("https://www.zohoapis.in/books/v3", {}) == "in"
    assert _infer_zoho_region("https://www.zohoapis.com/books/v3", {}) == "us"
    assert _infer_zoho_region("https://www.zohoapis.eu/books/v3", {}) == "eu"
    assert _infer_zoho_region("https://www.zohoapis.com.attacker.test/books/v3", {}) == "in"


def test_zoho_registration_ignores_user_supplied_token_endpoint() -> None:
    from api.v1.connectors import _normalise_connector_base_url, _prepare_zoho_books_registration
    from core.schemas.api import ConnectorCreate

    body = ConnectorCreate(
        name="zoho_books",
        category="finance",
        base_url="https://www.zohoapis.in/books/v3",
        auth_type="oauth2",
        auth_config={
            "client_id": "client-id",
            "client_secret": "client-secret",
            "organization_id": "60069102279",
            "refresh_token": "refresh-token",
            "token_url": "https://accounts.zoho.com.attacker.test/oauth/v2/token",
        },
    )

    _, secret_fields, non_secret_config = _prepare_zoho_books_registration(
        body,
        _normalise_connector_base_url(body.name, body.base_url),
    )

    assert secret_fields["token_url"] == "https://accounts.zoho.in/oauth/v2/token"
    assert non_secret_config["token_url"] == "https://accounts.zoho.in/oauth/v2/token"


def test_zoho_normalisation_rejects_lookalike_hosts() -> None:
    from api.v1.connectors import _normalise_connector_base_url

    assert (
        _normalise_connector_base_url(
            "zoho_books",
            "https://www.zohoapis.com.attacker.test/books/v3",
        )
        == "https://www.zohoapis.com/books/v3"
    )


def test_zoho_runtime_uses_fixed_region_endpoints_not_user_token_url() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector(
        {
            "base_url": "https://www.zohoapis.in/books/v3",
            "token_url": "https://accounts.zoho.com.attacker.test/oauth/v2/token",
        }
    )

    assert connector.base_url == "https://books.zoho.in/api/v3"
    assert connector.config["token_url"] == "https://accounts.zoho.in/oauth/v2/token"


def test_zoho_registration_does_not_require_data_center_field() -> None:
    from api.v1.connectors import _normalise_connector_base_url, _prepare_zoho_books_registration
    from core.schemas.api import ConnectorCreate

    body = ConnectorCreate(
        name="zoho_books",
        category="finance",
        base_url="https://www.zohoapis.in/books/v3",
        auth_type="oauth2",
        auth_config={
            "client_id": "client-id",
            "client_secret": "client-secret",
            "organization_id": "60069102279",
            "refresh_token": "refresh-token",
        },
    )

    _, secret_fields, non_secret_config = _prepare_zoho_books_registration(
        body,
        _normalise_connector_base_url(body.name, body.base_url),
    )

    assert secret_fields["region"] == "in"
    assert secret_fields["token_url"] == "https://accounts.zoho.in/oauth/v2/token"
    assert non_secret_config["oauth_refresh_token_present"] is True


def test_zoho_registration_refuses_false_healthy_without_token_material() -> None:
    from api.v1.connectors import _normalise_connector_base_url, _prepare_zoho_books_registration
    from core.schemas.api import ConnectorCreate

    body = ConnectorCreate(
        name="zoho_books",
        category="finance",
        base_url="https://www.zohoapis.in/books/v3",
        auth_type="oauth2",
        auth_config={
            "client_id": "client-id",
            "client_secret": "client-secret",
            "organization_id": "60069102279",
        },
    )

    with pytest.raises(HTTPException) as exc:
        _prepare_zoho_books_registration(
            body,
            _normalise_connector_base_url(body.name, body.base_url),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "zoho_token_material_required"
    assert "Data Center" not in exc.value.detail["message"]


def test_connector_list_reports_encrypted_vault_credentials() -> None:
    from api.v1.connectors import _connector_to_dict
    from core.models.connector import Connector

    connector = Connector(
        tenant_id="11111111-1111-1111-1111-111111111111",
        name="zoho_books",
        category="finance",
        base_url="https://www.zohoapis.in/books/v3",
        auth_type="oauth2",
        auth_config={},
        status="active",
    )

    assert _connector_to_dict(connector, has_encrypted_credentials=True)["has_credentials"] is True


def test_connector_create_ui_has_no_managed_provider_or_authorize_button() -> None:
    src = (ROOT / "ui" / "src" / "pages" / "ConnectorCreate.tsx").read_text(encoding="utf-8")

    assert "Custom / Generic Connector" in src
    assert "Authorize Connector" not in src
    assert "/connectors/oauth/initiate" not in src
    assert "Register Connector" in src


def test_connector_detail_does_not_offer_zoho_oauth_redirect() -> None:
    src = (ROOT / "ui" / "src" / "pages" / "ConnectorDetail.tsx").read_text(encoding="utf-8")

    assert 'const isZohoBooks = connector?.name === "zoho_books"' in src
    assert 'connector.auth_type === "oauth2" && !isZohoBooks' in src
    assert "without opening Zoho OAuth" in src


def test_provider_registry_zoho_region_is_optional_not_data_center() -> None:
    from core.connectors.provider_registry import get_provider

    provider = get_provider("zoho_books")
    assert provider is not None
    region_field = next(field for field in provider.user_fields if field.key == "region")
    assert region_field.required is False
    assert region_field.label == "Zoho Region"
    assert provider.resolve_region({"base_url": "https://www.zohoapis.com.attacker.test/books/v3"}) == "in"


def test_health_checks_update_connector_config_health_gate_source() -> None:
    src = (ROOT / "api" / "v1" / "connectors.py").read_text(encoding="utf-8")

    assert "cc.health_status = str(health.get(\"status\") or \"unknown\")" in src
    assert 'cc.health_status = "unhealthy"' in src
    assert 'cc.sync_error = "missing_encrypted_credentials"' in src


def test_zoho_update_revalidates_before_healthy_status() -> None:
    src = (ROOT / "api" / "v1" / "connectors.py").read_text(encoding="utf-8")

    assert "_validate_zoho_books_readiness(merged_secrets)" in src
    assert 'cc.health_status = (\n                    "healthy"' in src
    assert 'cc.sync_error = "credential_surface_changed"' in src


def test_agent_activation_uses_connector_readiness_gate() -> None:
    src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")

    assert "async def _assert_connectors_ready_for_activation" in src
    assert src.count("_assert_connectors_ready_for_activation(") >= 4
    assert "connector_health_not_healthy" in src
    assert "missing_refresh_token" in src
