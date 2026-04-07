"""E2E smoke tests — run against a deployed staging/production environment.

Includes retry logic for transient 502/503 errors during deploy rollover.
"""

import os
import time

import httpx
import pytest

BASE_URL = os.getenv("AGENTICORG_E2E_BASE_URL", "http://localhost:8000")
TOKEN = os.getenv("AGENTICORG_E2E_TOKEN", "")

MAX_RETRIES = 5
RETRY_DELAY = 10  # seconds


def _request_with_retry(client_or_url, path, *, method="GET", expect_status=200):
    """Make HTTP request with retry on transient errors (deploy rollover)."""
    resp = None
    for attempt in range(MAX_RETRIES):
        try:
            if isinstance(client_or_url, str):
                resp = httpx.request(method, f"{client_or_url}{path}", timeout=30)
            else:
                resp = client_or_url.request(method, path)

            if resp.status_code not in (502, 503):
                return resp
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout,
                httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError):
            pass

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)

    if resp is not None:
        return resp
    pytest.fail(f"All {MAX_RETRIES} attempts failed with connection/timeout errors on {path}")


@pytest.fixture
def client():
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    return httpx.Client(base_url=BASE_URL, headers=headers, timeout=30)


class TestSmoke:
    """Basic smoke tests to verify a deployed environment is functional."""

    def test_health_endpoint(self, client):
        """Verify health endpoint returns 200 with healthy/degraded status."""
        resp = _request_with_retry(client, "/api/v1/health")
        assert resp.status_code == 200, f"Health returned {resp.status_code}"
        data = resp.json()
        assert data["status"] in ("healthy", "degraded", "ok")
        assert "version" in data

    def test_liveness_probe(self, client):
        """Verify liveness probe responds."""
        resp = _request_with_retry(client, "/api/v1/health/liveness")
        assert resp.status_code == 200, f"Liveness returned {resp.status_code}"

    def test_openapi_docs_accessible(self, client):
        """Verify OpenAPI docs are served."""
        resp = _request_with_retry(client, "/docs")
        assert resp.status_code == 200

    def test_agents_list_requires_auth(self):
        """Verify unauthenticated requests are rejected."""
        resp = _request_with_retry(BASE_URL, "/api/v1/agents", expect_status=401)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_agents_list_with_auth(self, client):
        """Verify authenticated agents list returns paginated response."""
        if not TOKEN:
            pytest.skip("No E2E token configured")
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_workflows_list_with_auth(self, client):
        """Verify authenticated workflows list returns paginated response."""
        if not TOKEN:
            pytest.skip("No E2E token configured")
        resp = client.get("/api/v1/workflows")
        assert resp.status_code == 200

    def test_schemas_list_with_auth(self, client):
        """Verify schema registry returns response."""
        if not TOKEN:
            pytest.skip("No E2E token configured")
        resp = client.get("/api/v1/schemas")
        assert resp.status_code == 200
