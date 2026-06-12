from api.v1 import connectors


def test_cmo_vendor_sandbox_config_sets_preflight_metadata() -> None:
    config = connectors._cmo_vendor_sandbox_config(
        "Ads",
        "google_ads",
        {"owner": "qa@example.test", "developer_token": "must-not-store-here"},
        {"customer_id": "1234567890", "client_secret": "secret"},
    )

    assert config["cmo_category"] == "Ads"
    assert config["connector_provider"] == "google_ads"
    assert config["proof_scope"] == "vendor_sandbox"
    assert config["environment_type"] == "vendor_sandbox"
    assert config["local_test_only"] is False
    assert config["mock_or_test_double"] is False
    assert config["customer_id"] == "1234567890"
    assert "developer_token" not in config
    assert "client_secret" not in config


def test_cmo_vendor_sandbox_provider_aliases_are_canonical() -> None:
    canonical, provider = connectors._cmo_provider_for("CRM", "hubspot_sandbox")

    assert canonical == "hubspot"
    assert provider["required_credentials"] == ("access_token",)


def test_cmo_vendor_sandbox_rejects_placeholder_values() -> None:
    assert connectors._cmo_looks_placeholder("REAL_GOOGLE_ADS_REFRESH_TOKEN")
    assert connectors._cmo_looks_placeholder("verified-sender@example.com")
    assert not connectors._cmo_looks_placeholder("246292667")
