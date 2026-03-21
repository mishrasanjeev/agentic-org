"""E2E smoke tests — run against a deployed staging/production environment."""
import os
import pytest
import httpx

BASE_URL = os.getenv("AGENTICORG_E2E_BASE_URL", "http://localhost:8000")
TOKEN = os.getenv("AGENTICORG_E2E_TOKEN", "")


@pytest.fixture
def client():
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    return httpx.Client(base_url=BASE_URL, headers=headers, timeout=30)


class TestSmoke:
    """Basic smoke tests to verify a deployed environment is functional."""

    def test_health_endpoint(self, client):
        """Verify health endpoint returns 200 with healthy status."""
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")
        assert "version" in data

    def test_liveness_probe(self, client):
        """Verify liveness probe responds."""
        resp = client.get("/api/v1/health/liveness")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_openapi_docs_accessible(self, client):
        """Verify OpenAPI docs are served."""
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_agents_list_requires_auth(self):
        """Verify unauthenticated requests are rejected."""
        resp = httpx.get(f"{BASE_URL}/api/v1/agents", timeout=10)
        assert resp.status_code == 401

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
