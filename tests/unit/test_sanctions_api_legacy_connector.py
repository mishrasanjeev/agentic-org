# SPDX-License-Identifier: Apache-2.0
"""The deprecated ``sanctions_api`` connector keeps the behaviour tenants configured it for.

``sanctions_api`` is a separate legacy connector, not an alias of ``sanctions_screening``: the
same five tools, the same ``api_key`` bearer authentication and, for every tool, the same HTTP
method, path and parameters as before, answered with the provider's JSON unchanged. The expected
requests below are read from the connector's code before the rename: ``screen_entity`` posted its
parameters to ``/search`` after ``params.setdefault("min_score", 80)``, ``screen_transaction`` and
``batch_screen`` posted theirs as given to ``/search/transaction`` and ``/search/batch``,
``get_alert`` fetched ``/alerts/{alert_id}`` and ``generate_report`` fetched
``/reports/{screening_id}`` with only ``format`` (default ``json``) as a query parameter.

Only the provider's address moved out of the code. It is the connector config's ``base_url``,
else the deployment setting ``AGENTICORG_SANCTIONS_API_BASE_URL``; with neither, every call is
refused before a request is sent and the health check reports ``not_configured``.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import structlog

import connectors  # noqa: F401 - registers the native connectors
from connectors.framework.base_connector import BaseConnector
from connectors.registry import ConnectorRegistry
from core import config as core_config
from core.langgraph.tool_adapter import _build_tool_index
from scripts import check_denylist as dl

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_TOOLS = {"screen_entity", "screen_transaction", "get_alert", "batch_screen", "generate_report"}
API_KEY = "placeholder-not-a-key"
CONFIG_URL = "https://screening.example.test/v2"
SETTING_URL = "https://screening-setting.example.test/api"
PARTY = "Nimbus Example Traders"
COUNTERPARTY = "Harrow Lane Supplies Ltd"
# Synthetic provider answer; the connector returns it as it arrives.
ANSWER = {"results": [{"id": "match_0001", "name": COUNTERPARTY, "score": 91}], "total": 1}
PERSON = {"name": COUNTERPARTY, "type": "individual", "date_of_birth": "1980-01-31", "nationality": "NO"}
ENTITIES = [
    {"name": PARTY, "type": "entity"},
    {"name": COUNTERPARTY, "type": "entity", "nationality": "GB"},
]

# tool, its params, then what the connector sent before the rename: method, path under the base
# URL, query parameters and JSON body (None for a GET, which sent no body).
OLD_REQUESTS: dict[str, tuple[str, dict[str, Any], tuple[str, str, dict[str, str], Any]]] = {
    "screen_entity, default min_score": (
        "screen_entity",
        {"name": PARTY, "type": "entity"},
        ("POST", "/search", {}, {"name": PARTY, "type": "entity", "min_score": 80}),
    ),
    "screen_entity, own min_score": (
        "screen_entity",
        {**PERSON, "min_score": 95},
        ("POST", "/search", {}, {**PERSON, "min_score": 95}),
    ),
    "screen_transaction": (
        "screen_transaction",
        {
            "sender_name": PARTY,
            "receiver_name": COUNTERPARTY,
            "sender_country": "GB",
            "receiver_country": "NO",
            "amount": 1250.5,
            "currency": "EUR",
        },
        (
            "POST",
            "/search/transaction",
            {},
            {
                "sender_name": PARTY,
                "receiver_name": COUNTERPARTY,
                "sender_country": "GB",
                "receiver_country": "NO",
                "amount": 1250.5,
                "currency": "EUR",
            },
        ),
    ),
    "get_alert": ("get_alert", {"alert_id": "alrt_0001"}, ("GET", "/alerts/alrt_0001", {}, None)),
    "batch_screen, no min_score added": (
        "batch_screen",
        {"entities": ENTITIES},
        ("POST", "/search/batch", {}, {"entities": ENTITIES}),
    ),
    "batch_screen, own min_score": (
        "batch_screen",
        {"entities": ENTITIES, "min_score": 70},
        ("POST", "/search/batch", {}, {"entities": ENTITIES, "min_score": 70}),
    ),
    "generate_report, default format": (
        "generate_report",
        {"screening_id": "scr_0001"},
        ("GET", "/reports/scr_0001", {"format": "json"}, None),
    ),
    "generate_report, pdf": (
        "generate_report",
        {"screening_id": "scr_0001", "format": "pdf"},
        ("GET", "/reports/scr_0001", {"format": "pdf"}, None),
    ),
}


class FakeTransport:
    """Records every request the connector sends and answers each with ``answer``."""

    def __init__(self, answer: Any = ANSWER, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.answer = answer
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status == 204:
            return httpx.Response(204)
        return httpx.Response(self.status, json=self.answer)


def _legacy() -> type[BaseConnector]:
    cls = ConnectorRegistry.get("sanctions_api")
    assert cls is not None
    return cls


def _set_base_url_setting(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(core_config.settings, "sanctions_api_base_url", value)


async def _connected(config: dict[str, Any], transport: FakeTransport) -> BaseConnector:
    """The legacy connector with its HTTP client on ``transport`` (no DNS: the hosts are fake)."""
    with pytest.warns(DeprecationWarning):
        connector = _legacy()(config=config)
    connector._connector_transport_and_dns = lambda: (httpx.MockTransport(transport), False)
    await connector.connect()
    return connector


def _sent(request: httpx.Request) -> tuple[str, str, dict[str, str], Any]:
    body = json.loads(request.content) if request.content else None
    return request.method, request.url.path, dict(request.url.params), body


# ── The old tools, requests and responses ───────────────────────────────────


def test_the_old_id_is_the_legacy_connector_with_exactly_its_five_tools() -> None:
    cls = _legacy()
    assert cls.name == "sanctions_api"
    assert (cls.category, cls.auth_type, cls.rate_limit_rpm) == ("ops", "api_key", 500)
    assert cls.base_url == "", "the provider's address comes from configuration, never from code"
    with pytest.warns(DeprecationWarning):
        connector = cls(config={"api_key": API_KEY, "base_url": CONFIG_URL})
    assert set(connector._tool_registry) == LEGACY_TOOLS
    # An agent that links the old id is offered those tools and no others.
    assert set(_build_tool_index(connector_names=["sanctions_api"])) == LEGACY_TOOLS


@pytest.mark.parametrize("case", list(OLD_REQUESTS))
async def test_each_tool_sends_the_request_the_old_connector_sent(case: str) -> None:
    tool, params, expected = OLD_REQUESTS[case]
    method, path, query, body = expected
    transport = FakeTransport()
    connector = await _connected({"api_key": API_KEY, "base_url": CONFIG_URL}, transport)
    result = await connector.execute_tool(tool, dict(params))
    [request] = transport.requests
    assert request.url.host == "screening.example.test"
    assert _sent(request) == (method, urlsplit(CONFIG_URL).path + path, query, body)
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert result == ANSWER  # the provider's answer, unchanged


async def test_empty_and_failed_answers_keep_the_old_contract() -> None:
    empty = await _connected({"api_key": API_KEY, "base_url": CONFIG_URL}, FakeTransport(status=204))
    assert await empty.execute_tool("get_alert", {"alert_id": "alrt_0001"}) == {"status": "ok", "http_status": 204}
    failing = await _connected({"api_key": API_KEY, "base_url": CONFIG_URL}, FakeTransport(status=503))
    with pytest.raises(httpx.HTTPStatusError):
        await failing.execute_tool("screen_entity", {"name": PARTY})


@pytest.mark.parametrize(("tool", "missing"), [("get_alert", "alert_id"), ("generate_report", "screening_id")])
async def test_a_missing_id_fails_as_before_without_a_request(tool: str, missing: str) -> None:
    transport = FakeTransport()
    connector = await _connected({"api_key": API_KEY, "base_url": CONFIG_URL}, transport)
    with pytest.raises(KeyError, match=missing):
        await connector.execute_tool(tool, {})
    assert transport.requests == []


async def test_health_probes_the_provider_as_before() -> None:
    transport = FakeTransport()
    connector = await _connected({"api_key": API_KEY, "base_url": CONFIG_URL}, transport)
    assert await connector.health_check() == {"status": "healthy"}
    assert [_sent(request) for request in transport.requests] == [("POST", "/v2/search", {}, {"name": "test"})]
    rejected = await _connected({"api_key": API_KEY, "base_url": CONFIG_URL}, FakeTransport(status=401))
    health = await rejected.health_check()
    assert health["status"] == "unhealthy"
    assert "401" in health["error"]


# ── Where the provider's address comes from ─────────────────────────────────


async def test_the_connector_config_base_url_wins_over_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_url_setting(monkeypatch, SETTING_URL)
    transport = FakeTransport()
    connector = await _connected({"api_key": API_KEY, "base_url": CONFIG_URL}, transport)
    await connector.execute_tool("screen_entity", {"name": PARTY})
    [request] = transport.requests
    assert (request.url.host, request.url.path) == ("screening.example.test", "/v2/search")


@pytest.mark.parametrize("config_base_url", [None, "", "   "], ids=["absent", "empty", "blank"])
async def test_the_setting_is_used_when_the_config_has_no_base_url(
    monkeypatch: pytest.MonkeyPatch, config_base_url: str | None
) -> None:
    _set_base_url_setting(monkeypatch, SETTING_URL)
    config: dict[str, Any] = {"api_key": API_KEY}
    if config_base_url is not None:
        config["base_url"] = config_base_url
    transport = FakeTransport()
    connector = await _connected(config, transport)
    await connector.execute_tool("generate_report", {"screening_id": "scr_0001"})
    [request] = transport.requests
    assert request.url.host == "screening-setting.example.test"
    assert _sent(request) == ("GET", "/api/reports/scr_0001", {"format": "json"}, None)


async def test_without_a_base_url_every_call_is_refused_before_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from connectors.ops.sanctions_api import SanctionsApiNotConfiguredError

    _set_base_url_setting(monkeypatch, "")
    transport = FakeTransport()
    with pytest.warns(DeprecationWarning):
        connector = _legacy()(config={"api_key": API_KEY})
    connector._connector_transport_and_dns = lambda: (httpx.MockTransport(transport), False)
    for tool, params, _ in OLD_REQUESTS.values():  # before connect() too: never "not connected"
        with pytest.raises(SanctionsApiNotConfiguredError, match="AGENTICORG_SANCTIONS_API_BASE_URL"):
            await connector.execute_tool(tool, dict(params))
    with structlog.testing.capture_logs() as logs:
        await connector.connect()  # connects nothing, so the connector test can report why
    assert [entry["event"] for entry in logs] == ["sanctions_api_not_configured"]
    for tool, params, _ in OLD_REQUESTS.values():
        with pytest.raises(SanctionsApiNotConfiguredError, match="no request was sent"):
            await connector.execute_tool(tool, dict(params))
    assert transport.requests == []


async def test_health_reports_not_configured_without_a_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_url_setting(monkeypatch, "")
    transport = FakeTransport()
    connector = await _connected({"api_key": API_KEY, "base_url": "  "}, transport)
    health = await connector.health_check()
    assert health["status"] == "not_configured"
    assert "AGENTICORG_SANCTIONS_API_BASE_URL" in health["reason"]
    assert transport.requests == []


# ── Deprecated, and neutral in the tree ─────────────────────────────────────


def test_creating_it_still_warns_that_the_id_is_deprecated() -> None:
    with structlog.testing.capture_logs() as logs, pytest.warns(DeprecationWarning, match="sanctions_screening"):
        _legacy()(config={"api_key": API_KEY, "base_url": CONFIG_URL})
    [entry] = [entry for entry in logs if entry["event"] == "connector_id_deprecated"]
    assert (entry["connector"], entry["replacement"], entry["log_level"]) == (
        "sanctions_api",
        "sanctions_screening",
        "warning",
    )


def test_the_module_names_no_provider_or_address() -> None:
    module = inspect.getmodule(_legacy())
    assert module is not None
    source = inspect.getsource(module)
    assert not re.search(r"https?://", source), "the provider's address belongs in configuration"
    denylist = dl.load(REPO_ROOT / "config" / "denylist.sha256")
    for number, line in enumerate(source.splitlines(), start=1):
        assert denylist.matches(line) == [], f"{module.__name__}:{number} names a denylisted vendor"


def test_the_setting_is_documented_for_operators() -> None:
    assert core_config.Settings.model_fields["sanctions_api_base_url"].default == ""
    assert "AGENTICORG_SANCTIONS_API_BASE_URL=" in (REPO_ROOT / ".env.example").read_text("utf-8")
