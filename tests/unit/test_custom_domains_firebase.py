# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant custom domains through Firebase Hosting: the mode, the agent-only site config, the Hosting API
client, the verified X-Forwarded-Host middleware and its Cache-Control policy, and the release script."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from api.middleware import merchant_host
from api.middleware.merchant_host import MerchantHostMiddleware, is_agent_path
from core.commerce import custom_domains as cd
from core.commerce.ucp import store
from core.config import settings

SITE = "merchant-domains-example"
HOST = "agent.merchant.example"
SERVICE_HOST = "agenticorg-api-example.a.run.app"


@pytest.fixture(autouse=True)
def _firebase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "custom_domains_enabled", True)
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", True)
    monkeypatch.setattr(settings, "custom_domain_firebase_site", SITE)
    monkeypatch.setattr(settings, "custom_domain_cloud_run_project", "example-project")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_service", "agenticorg-api")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_region", "asia-southeast1")


# --- mode ---------------------------------------------------------------------------------------


def test_firebase_mode_needs_the_flag_a_site_and_a_project(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cd.firebase_enabled() is True
    assert cd.current_provider() == "firebase_hosting"
    assert cd.cname_target() == f"{SITE}.web.app"
    for name, value in (
        ("custom_domain_firebase_enabled", False),
        ("custom_domain_firebase_site", ""),
        ("custom_domain_cloud_run_project", ""),
    ):
        with monkeypatch.context() as m:
            m.setattr(settings, name, value)
            m.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
            assert cd.firebase_enabled() is False
            assert cd.current_provider() == "cloud_run_mapping"
            assert cd.cname_target() == "ghs.googlehosted.com"


def test_firebase_domains_need_no_search_console_owner() -> None:
    assert cd.search_console_step(HOST, provider="firebase_hosting")["required"] is False
    assert cd.search_console_step(HOST)["required"] is True


def test_the_site_config_rewrites_only_the_agent_paths() -> None:
    config = cd.firebase_site_config()
    globs = [rule["glob"] for rule in config["rewrites"]]
    assert globs == ["/.well-known/ucp", "/.well-known/ucp/**", "/ucp/**", "/acp/**", "/.well-known/agenticorg-host"]
    assert "**" not in globs and "/**" not in globs  # no catch-all: nothing else of the API is on a merchant host
    assert all(
        rule["run"] == {"serviceId": "agenticorg-api", "region": "asia-southeast1"} for rule in config["rewrites"]
    )
    assert set(config) == {"rewrites"}


@pytest.mark.parametrize(
    ("path", "agent"),
    [
        ("/.well-known/ucp", True),
        ("/.well-known/ucp/2026-04-08", True),
        ("/ucp/catalog/search", True),
        ("/acp/checkout_sessions", True),
        ("/.well-known/agenticorg-host", True),
        ("/.well-known/ucpx", False),
        ("/api/v1/auth/login", False),
        ("/ucp", False),
    ],
)
def test_agent_paths(path: str, agent: bool) -> None:
    assert is_agent_path(path) is agent


# --- Hosting API client ---------------------------------------------------------------------------


def _domain(host_state: str, ownership: str, cert: str | None) -> dict[str, Any]:
    document: dict[str, Any] = {"name": f"x/customDomains/{HOST}", "hostState": host_state, "ownershipState": ownership}
    if cert:
        document["cert"] = {"type": "TEMPORARY", "state": cert}
    return document


def test_parse_firebase_domain() -> None:
    serving = cd.parse_firebase_domain(_domain("HOST_ACTIVE", "OWNERSHIP_ACTIVE", "CERT_PROPAGATING"))
    assert serving.serving and not serving.failed
    assert cd.parse_firebase_domain(_domain("HOST_ACTIVE", "OWNERSHIP_ACTIVE", "CERT_ACTIVE")).serving
    waiting = cd.parse_firebase_domain(_domain("HOST_UNHOSTED", "OWNERSHIP_MISSING", "CERT_VALIDATING"))
    assert not waiting.serving and not waiting.failed
    mismatch = cd.parse_firebase_domain(_domain("HOST_MISMATCH", "OWNERSHIP_MISMATCH", None))
    assert mismatch.failed and "another Firebase project" in (mismatch.message or "")


def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    async def token() -> str:
        return "synthetic-token"

    monkeypatch.setattr(cd, "_access_token", token)


BASE = f"{cd.FIREBASE_API}/projects/example-project/sites/{SITE}/customDomains"


def test_create_posts_the_domain_then_reads_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _token(monkeypatch)
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/1"})
        return httpx.Response(200, json=_domain("HOST_UNHOSTED", "OWNERSHIP_MISSING", "CERT_PREPARING"))

    state = asyncio.run(
        cd.create_firebase_domain(HOST, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    )
    assert calls == [("POST", f"{BASE}?customDomainId={HOST}"), ("GET", f"{BASE}/{HOST}")]
    assert state.exists and not state.failed and not state.serving


def test_create_reads_an_existing_domain_and_reports_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    _token(monkeypatch)

    def exists(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": {"message": "already exists"}})
        return httpx.Response(200, json=_domain("HOST_ACTIVE", "OWNERSHIP_ACTIVE", "CERT_ACTIVE"))

    state = asyncio.run(
        cd.create_firebase_domain(HOST, client=httpx.AsyncClient(transport=httpx.MockTransport(exists)))
    )
    assert state.serving

    def refused(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "The caller does not have permission"}})

    state = asyncio.run(
        cd.create_firebase_domain(HOST, client=httpx.AsyncClient(transport=httpx.MockTransport(refused)))
    )
    assert state.failed and state.message == "The caller does not have permission"


@pytest.mark.parametrize(("status", "expected"), [(200, True), (404, True), (403, False)])
def test_delete_firebase_domain(monkeypatch: pytest.MonkeyPatch, status: int, expected: bool) -> None:
    _token(monkeypatch)
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(status, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert asyncio.run(cd.delete_firebase_domain(HOST, client=client)) is expected
    assert seen == [("DELETE", f"{BASE}/{HOST}")]


def test_get_firebase_domain_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _token(monkeypatch)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(404, json={})))
    assert asyncio.run(cd.get_firebase_domain(HOST, client=client)).exists is False


# --- middleware -------------------------------------------------------------------------------------


async def _echo(request: Request) -> JSONResponse:
    return JSONResponse(
        {"host": request.headers.get("host"), "original": request.scope.get("agenticorg.original_host")}
    )


async def _cached(request: Request) -> JSONResponse:
    return JSONResponse({"host": request.headers.get("host")}, headers={"Cache-Control": "public, max-age=300"})


def _app() -> Any:
    app = Starlette(
        routes=[
            Route("/.well-known/agenticorg-host", _echo),
            Route("/.well-known/ucp", _cached),
            Route("/api/v1/thing", _echo),
        ]
    )
    return MerchantHostMiddleware(app)


def _get(path: str, *, forwarded: str | None = HOST) -> httpx.Response:
    headers = {"host": SERVICE_HOST}
    if forwarded is not None:
        headers["x-forwarded-host"] = forwarded

    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url=f"https://{SERVICE_HOST}") as client:
            return await client.get(path, headers=headers)

    return asyncio.run(run())


@pytest.fixture()
def verified(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    hosts = {HOST}

    async def active(host: str) -> bool:
        return host in hosts

    monkeypatch.setattr(store, "firebase_domain_active", active)
    return hosts


def test_a_verified_forwarded_host_becomes_the_host(verified: set[str]) -> None:
    response = _get("/.well-known/agenticorg-host")
    assert response.json() == {"host": HOST, "original": SERVICE_HOST}
    assert response.headers["cache-control"] == "no-store"


def test_an_unverified_forwarded_host_is_ignored(verified: set[str]) -> None:
    response = _get("/.well-known/agenticorg-host", forwarded="victim.merchant.example")
    assert response.json() == {"host": SERVICE_HOST, "original": None}
    # A comma list uses the first value only, and it must be verified too.
    response = _get("/.well-known/agenticorg-host", forwarded=f"victim.merchant.example, {HOST}")
    assert response.json()["host"] == SERVICE_HOST


def test_only_agent_paths_are_touched(verified: set[str]) -> None:
    response = _get("/api/v1/thing")
    assert response.json() == {"host": SERVICE_HOST, "original": None}
    assert "cache-control" not in response.headers


def test_a_response_cache_policy_is_kept(verified: set[str]) -> None:
    response = _get("/.well-known/ucp")
    assert response.json() == {"host": HOST}
    assert response.headers["cache-control"] == "public, max-age=300"


def test_the_flag_off_changes_nothing(verified: set[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "custom_domain_firebase_enabled", False)
    response = _get("/.well-known/agenticorg-host")
    assert response.json() == {"host": SERVICE_HOST, "original": None}
    assert "cache-control" not in response.headers


def test_a_lookup_failure_keeps_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(_host: str) -> bool:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "firebase_domain_active", broken)
    response = _get("/.well-known/agenticorg-host")
    assert response.json()["host"] == SERVICE_HOST
    assert response.headers["cache-control"] == "no-store"
    assert merchant_host.MerchantHostMiddleware is MerchantHostMiddleware


# --- release script ------------------------------------------------------------------------------------


def test_release_script_dry_run_prints_the_agent_only_config(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.release_merchant_domain_site import main

    assert main(["--project", "example-project", "--site", SITE, "--dry-run"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["site"] == SITE
    assert printed["config"] == cd.firebase_site_config()
