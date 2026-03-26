"""Fixtures for connector test harness — mock server lifecycle + connector factory."""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mock_server_url():
    """Start the mock server on a random port for the test session."""
    from tests.connector_harness.mock_server import app

    port = _get_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    for _ in range(50):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            s.close()
            break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)

    url = f"http://127.0.0.1:{port}"
    yield url

    server.should_exit = True


# All possible secret keys that any connector might look for
MOCK_SECRETS = {
    "api_key": "mock-api-key",
    "bot_token": "mock-bot-token",
    "personal_access_token": "mock-pat",
    "client_id": "mock-client-id",
    "client_secret": "mock-client-secret",
    "email": "mock@test.test",
    "api_token": "mock-api-token",
    "username": "mock-user",
    "password": "mock-pass",
    "key_id": "mock-key-id",
    "key_secret": "mock-key-secret",
    "dsc_path": "/mock/dsc",
    "dsc_password": "mock-dsc-pass",
    "access_key": "mock-ak",
    "secret_key": "mock-sk",
    "gcs_service_account_key": "/mock/sa.json",
    "account_sid": "mock-sid",
    "auth_token": "mock-auth-token",
    "phone_from": "+15555555555",
    "phone_number_id": "mock-phone-id",
    "meta_business_token": "mock-meta-token",
    "aa_client_id": "mock-aa-id",
    "aa_client_secret": "mock-aa-secret",
    "gsp_api_key": "mock-gsp-key",
    "signing_key": "mock-signing-key",
}


async def make_connector(name: str, mock_url: str):
    """Create a connector instance pointed at the mock server."""
    import connectors  # noqa: F401 — trigger auto-registration
    from connectors.registry import ConnectorRegistry

    cls = ConnectorRegistry.get(name)
    if cls is None:
        raise ValueError(f"Connector '{name}' not found in registry")

    config = {
        **MOCK_SECRETS,
        "token_url": f"{mock_url}/oauth2/token",
        "storage_endpoint": mock_url,
    }

    instance = cls(config=config)
    instance.base_url = mock_url
    try:
        await instance.connect()
    except Exception:
        # Some connectors may fail connect() due to auth quirks
        # but we can still test tool execution
        pass

    # Ensure base_url is still mock (some connectors override in connect)
    instance.base_url = mock_url
    if hasattr(instance, "_client") and instance._client is not None:
        # Recreate client with mock URL
        import httpx
        instance._client = httpx.AsyncClient(
            base_url=mock_url,
            timeout=10.0,
            headers=getattr(instance, "_auth_headers", {}),
        )

    return instance


def get_all_connector_names() -> list[str]:
    """Return sorted list of all 42 registered connector names."""
    import connectors  # noqa: F401
    from connectors.registry import ConnectorRegistry
    return sorted(ConnectorRegistry.all_names())
