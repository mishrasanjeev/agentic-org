# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant custom domains: host rules, the records and commands shown, DNS checks, the certificate probe,
the Cloud Run Admin API client and the merchant view (no database; see the Postgres integration test)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import dns.exception
import dns.resolver
import httpx
import pytest
from fastapi import HTTPException

from api.v1 import merchant_custom_domains as mcd
from core.commerce import custom_domains as cd
from core.config import settings


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "custom_domains_enabled", True)
    monkeypatch.setattr(settings, "custom_domain_cname_target", "ghs.googlehosted.com")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_project", "example-project")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_region", "asia-southeast1")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_service", "agenticorg-api")
    monkeypatch.setattr(settings, "custom_domain_mapping_account", "mapper@platform.example")
    monkeypatch.setattr(settings, "custom_domain_cloud_run_automap_enabled", False)


# --- host rules, records and commands ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Agent.Merchant.Example.", "agent.merchant.example"),
        ("agent.merchant.example:443", "agent.merchant.example"),
        ("https://agent.merchant.example", None),
        ("agent.merchant.example/ucp", None),
        ("192.0.2.10", None),
        ("localhost", None),
        ("_agenticorg-ucp.merchant.example", None),
        ("a" * 50 + ".merchant.example", None),  # longer than the 64-character Cloud Run limit
        ("bücher.merchant.example", "xn--bcher-kva.merchant.example"),
    ],
)
def test_normalize_custom_host(value: str, expected: str | None) -> None:
    assert cd.normalize_custom_host(value) == expected


def test_dns_records_are_exactly_what_a_provider_asks_for() -> None:
    challenge = {
        "host": "agent.merchant.example",
        "record_name": "_agenticorg-ucp.agent.merchant.example",
        "record_value": "agenticorg-ucp-verify=synthetic",
    }
    records = cd.dns_records("agent.merchant.example", challenge)
    assert [(r["type"], r["name"], r["value"]) for r in records] == [
        ("CNAME", "agent.merchant.example", "ghs.googlehosted.com."),
        ("TXT", "_agenticorg-ucp.agent.merchant.example", "agenticorg-ucp-verify=synthetic"),
    ]
    assert "DNS only" in records[0]["note"]
    assert [r["type"] for r in cd.dns_records("agent.merchant.example", None)] == ["CNAME"]


def test_search_console_step_names_the_mapping_account_and_base_domain() -> None:
    step = cd.search_console_step("agent.merchant.example")
    assert step["account"] == "mapper@platform.example"
    assert step["property"] == "merchant.example"
    assert "Verified owners" in step["instructions"]


def test_mapping_commands_are_exact_and_shell_quoted(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = cd.mapping_commands("agent.merchant.example")
    assert commands == {
        "create": "gcloud beta run domain-mappings create --service=agenticorg-api --domain=agent.merchant.example "
        "--project=example-project --region=asia-southeast1",
        "describe": "gcloud beta run domain-mappings describe --domain=agent.merchant.example "
        "--project=example-project --region=asia-southeast1",
        "delete": "gcloud beta run domain-mappings delete --domain=agent.merchant.example "
        "--project=example-project --region=asia-southeast1 --quiet",
    }
    monkeypatch.setattr(settings, "custom_domain_cloud_run_project", "")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(settings, "custom_domain_cloud_run_service", "svc; rm -rf /")
    unsafe = cd.mapping_commands("agent.merchant.example")["create"]
    assert "--project=PROJECT_ID" in unsafe and "--service='svc; rm -rf /'" in unsafe


def test_automap_needs_the_flag_and_a_project(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cd.automap_enabled() is False
    monkeypatch.setattr(settings, "custom_domain_cloud_run_automap_enabled", True)
    assert cd.automap_enabled() is True
    monkeypatch.setattr(settings, "custom_domain_cloud_run_project", "")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    assert cd.automap_enabled() is False


def test_every_error_code_has_a_merchant_message() -> None:
    for code in (
        "invalid_host",
        "apex_domain_needs_subdomain",
        "ownership_txt_missing",
        "cname_missing",
        "cname_wrong_target",
        "dns_lookup_failed",
        "search_console_owner_not_confirmed",
        "host_bound_to_another_tenant",
        "host_in_use",
        "mapping_failed",
        "certificate_pending",
        "host_not_routed",
    ):
        assert cd.error_message(code)
    assert cd.error_message(None) is None


# --- DNS ------------------------------------------------------------------------------------------------


class _FakeResolver:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.lifetime = self.timeout = None

    async def resolve(self, _name: str, _rdtype: str, **_kwargs: Any) -> Any:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _with_resolver(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    monkeypatch.setattr(cd, "_resolver", lambda: _FakeResolver(outcome))


def test_resolve_cname(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_resolver(monkeypatch, [SimpleNamespace(target="GHS.googlehosted.com.")])
    assert asyncio.run(cd.resolve_cname("agent.merchant.example")) == "ghs.googlehosted.com"
    _with_resolver(monkeypatch, dns.resolver.NoAnswer())
    assert asyncio.run(cd.resolve_cname("agent.merchant.example")) is None
    _with_resolver(monkeypatch, dns.exception.Timeout())
    with pytest.raises(cd.DnsLookupError):
        asyncio.run(cd.resolve_cname("agent.merchant.example"))


def test_zone_apex_is_an_soa_at_the_name_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_resolver(monkeypatch, SimpleNamespace(rrset=SimpleNamespace(name="merchant.example.")))
    assert asyncio.run(cd.is_zone_apex("merchant.example")) is True
    # Following a CNAME lands on another zone's SOA: not this name's apex.
    _with_resolver(monkeypatch, SimpleNamespace(rrset=SimpleNamespace(name="googlehosted.com.")))
    assert asyncio.run(cd.is_zone_apex("agent.merchant.example")) is False
    _with_resolver(monkeypatch, SimpleNamespace(rrset=None))
    assert asyncio.run(cd.is_zone_apex("agent.merchant.example")) is False
    _with_resolver(monkeypatch, dns.exception.Timeout())
    assert asyncio.run(cd.is_zone_apex("agent.merchant.example")) is False


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("ghs.googlehosted.com", (True, "ghs.googlehosted.com", None)),
        ("shops.example", (False, "shops.example", "cname_wrong_target")),
        (None, (False, None, "cname_missing")),
        (cd.DnsLookupError("timeout"), (False, None, "dns_lookup_failed")),
    ],
)
def test_check_routing(answer: Any, expected: tuple) -> None:
    async def resolver(_host: str) -> str | None:
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert asyncio.run(cd.check_routing("agent.merchant.example", resolver=resolver)) == expected


# --- certificate probe ------------------------------------------------------------------------------------


def _factory(handler: Any) -> Any:
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_probe_is_live_only_when_this_service_answers_for_the_host() -> None:
    seen: list[str] = []

    def live(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"service": "agenticorg", "host": "agent.merchant.example"})

    assert asyncio.run(cd.probe_host("agent.merchant.example", client_factory=_factory(live))).state == "live"
    assert seen == ["https://agent.merchant.example/.well-known/agenticorg-host"]

    def other_host(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"service": "agenticorg", "host": "app.platform.example"})

    def not_found(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    def not_json(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>")

    def tls_fails(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("certificate verify failed", request=request)

    assert asyncio.run(cd.probe_host("agent.merchant.example", client_factory=_factory(other_host))).state == (
        "host_not_routed"
    )
    assert asyncio.run(cd.probe_host("agent.merchant.example", client_factory=_factory(not_found))).detail == "HTTP 404"
    assert asyncio.run(cd.probe_host("agent.merchant.example", client_factory=_factory(not_json))).state == (
        "host_not_routed"
    )
    assert asyncio.run(cd.probe_host("agent.merchant.example", client_factory=_factory(tls_fails))).state == (
        "certificate_pending"
    )


def test_probe_refuses_private_addresses() -> None:
    from core.security.egress import EgressValidationError

    def factory() -> Any:
        class _Client:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *_exc: Any) -> None:
                return None

            async def get(self, _url: str) -> Any:
                raise EgressValidationError("blocked_ip", "169.254.169.254")

        return _Client()

    result = asyncio.run(cd.probe_host("agent.merchant.example", client_factory=factory))
    assert result.state == "host_not_routed" and "blocked_ip" in (result.detail or "")


def test_default_probe_client_pins_dns_and_never_follows_redirects() -> None:
    from core.security.egress import PinnedDnsAsyncHTTPTransport

    client = cd._probe_client()
    try:
        assert isinstance(client._transport, PinnedDnsAsyncHTTPTransport)
        assert client.follow_redirects is False
    finally:
        asyncio.run(client.aclose())


# --- Cloud Run Admin API ------------------------------------------------------------------------------------


def _mapping(generation: int, observed: int, ready: str | None, message: str | None = None) -> dict[str, Any]:
    conditions = [] if ready is None else [{"type": "Ready", "status": ready, "message": message}]
    return {
        "metadata": {"generation": generation},
        "status": {"observedGeneration": observed, "conditions": conditions},
    }


def test_parse_mapping_state() -> None:
    assert cd.parse_mapping_state(_mapping(1, 1, "True")).ready is True
    failed = cd.parse_mapping_state(_mapping(2, 2, "False", "Caller is not authorized to administer the domain"))
    assert failed.failed is True and "not authorized" in (failed.message or "")
    assert cd.parse_mapping_state(_mapping(2, 1, "True")).ready is False  # not caught up yet
    assert cd.parse_mapping_state(_mapping(1, 1, None)) == cd.MappingState(True, False, False, None)


def test_create_domain_mapping_posts_the_spec_and_reads_an_existing_one(monkeypatch: pytest.MonkeyPatch) -> None:
    async def token() -> str:
        return "synthetic-token"

    monkeypatch.setattr(cd, "_access_token", token)
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, str(request.url), body))
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        if request.method == "POST":
            return httpx.Response(409, json={"error": {"message": "already exists"}})
        return httpx.Response(200, json=_mapping(1, 1, "Unknown"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    state = asyncio.run(cd.create_domain_mapping("agent.merchant.example", client=client))
    base = "https://asia-southeast1-run.googleapis.com/apis/domains.cloudrun.com/v1/namespaces/example-project"
    assert calls[0][:2] == ("POST", f"{base}/domainmappings")
    assert calls[0][2]["metadata"] == {"name": "agent.merchant.example", "namespace": "example-project"}
    assert calls[0][2]["spec"] == {"routeName": "agenticorg-api", "certificateMode": "AUTOMATIC"}
    assert calls[1][:2] == ("GET", f"{base}/domainmappings/agent.merchant.example")
    assert state.exists is True and state.ready is False and state.failed is False


def test_create_domain_mapping_reports_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def token() -> str:
        return "synthetic-token"

    monkeypatch.setattr(cd, "_access_token", token)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "domain not verified for this caller"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    state = asyncio.run(cd.create_domain_mapping("agent.merchant.example", client=client))
    assert state == cd.MappingState(False, False, True, "domain not verified for this caller")


def test_get_domain_mapping_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def token() -> str:
        return "synthetic-token"

    monkeypatch.setattr(cd, "_access_token", token)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(404, json={})))
    assert asyncio.run(cd.get_domain_mapping("agent.merchant.example", client=client)).exists is False


# --- merchant view and access ---------------------------------------------------------------------------------


def _row(**overrides: Any) -> Any:
    now = datetime(2026, 10, 21, tzinfo=UTC)
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "merchant_id": "merchant-1",
        "seller_agent_id": "seller-1",
        "host": "agent.merchant.example",
        "status": "pending_dns",
        "cname_target": "ghs.googlehosted.com",
        "mapping_source": None,
        "search_console_owner_confirmed_at": None,
        "last_error_code": None,
        "last_error_detail": None,
        "check_count": 0,
        "last_checked_at": None,
        "verified_at": None,
        "mapping_requested_at": None,
        "mapping_created_at": None,
        "live_at": None,
        "created_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _states(row: Any) -> list[str]:
    return [step["state"] for step in mcd._steps(row)]


def test_steps_follow_the_status() -> None:
    now = datetime(2026, 10, 21, tzinfo=UTC)
    assert _states(_row()) == ["current", "current", "pending", "pending", "pending"]
    both = _row(last_error_code="ownership_txt_missing", last_error_detail="_x (also: cname_missing)")
    assert mcd.error_codes(both) == ["ownership_txt_missing", "cname_missing"]
    assert _states(both) == ["error", "error", "pending", "pending", "pending"]
    assert _states(_row(verified_at=now, last_error_code="cname_wrong_target")) == [
        "error",
        "done",
        "pending",
        "pending",
        "pending",
    ]
    assert _states(_row(status="pending_mapping", verified_at=now)) == ["done", "done", "current", "pending", "pending"]
    assert _states(_row(status="pending_certificate", verified_at=now, mapping_created_at=now)) == [
        "done",
        "done",
        "done",
        "current",
        "pending",
    ]
    assert _states(_row(status="failed", verified_at=now)) == ["done", "done", "error", "pending", "pending"]
    assert _states(_row(status="failed", verified_at=now, mapping_created_at=now)) == [
        "done",
        "done",
        "done",
        "error",
        "pending",
    ]
    assert _states(_row(status="live")) == ["done"] * 5


def test_domain_view_shows_the_attested_ownership_record_without_its_value() -> None:
    ucp = {
        "serving_host": "agent.merchant.example",
        "attestation": {
            "host": "agent.merchant.example",
            "record_name": "_agenticorg-ucp.agent.merchant.example",
            "verified_at": "2026-10-21T00:00:00Z",
        },
    }
    view = mcd.domain_view(_row(status="pending_mapping"), ucp)
    assert view["ownership_verified"] is True
    txt = next(r for r in view["records"] if r["type"] == "TXT")
    assert txt["value"] == "" and "Verified" in txt["note"]
    assert view["profile_url"] == "https://agent.merchant.example/.well-known/ucp"
    # A challenge pending for some other host is never shown for this one.
    other = {"challenge": {"host": "shop.merchant.example", "record_name": "_agenticorg-ucp.shop.merchant.example"}}
    assert [r["type"] for r in mcd.domain_view(_row(), other)["records"]] == ["CNAME"]


def test_operator_queue_is_hidden_unless_the_caller_is_the_operator_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    operator = str(uuid.uuid4())
    monkeypatch.setattr(settings, "custom_domain_operator_tenant_id", "")
    with pytest.raises(HTTPException) as unset:
        mcd._require_operator(operator)
    assert unset.value.status_code == 404
    monkeypatch.setattr(settings, "custom_domain_operator_tenant_id", operator)
    mcd._require_operator(operator)
    with pytest.raises(HTTPException):
        mcd._require_operator(str(uuid.uuid4()))


def test_host_check_echoes_the_request_host_and_respects_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    request = SimpleNamespace(headers={"host": "Agent.Merchant.Example:443"})
    response = asyncio.run(mcd.host_check(request))
    assert json.loads(response.body) == {"service": "agenticorg", "host": "agent.merchant.example"}
    assert response.headers["cache-control"] == "no-store"
    monkeypatch.setattr(settings, "custom_domains_enabled", False)
    with pytest.raises(HTTPException) as off:
        asyncio.run(mcd.host_check(request))
    assert off.value.status_code == 404


def test_operator_email_escapes_and_needs_a_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr("core.email.send_email", lambda to, subject, html: sent.append((to, subject, html)) or True)
    monkeypatch.setattr(settings, "custom_domain_operator_email", "")
    assert mcd._send_operator_email(subject="s", lines=["x"]) is False
    monkeypatch.setattr(settings, "custom_domain_operator_email", "ops@platform.example")
    assert mcd._send_operator_email(subject="s", lines=["<b>host</b>", "gcloud beta run x"]) is True
    assert sent[0][0] == "ops@platform.example"
    assert "&lt;b&gt;host&lt;/b&gt;" in sent[0][2] and "<code>gcloud beta run x</code>" in sent[0][2]


def test_check_routing_uses_the_domain_s_own_target(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolver(_host: str) -> str:
        return "ghs.googlehosted.com"

    monkeypatch.setattr(settings, "custom_domain_cname_target", "new-target.platform.example")
    assert asyncio.run(cd.check_routing("agent.merchant.example", target="ghs.googlehosted.com.", resolver=resolver))[0]
    assert asyncio.run(cd.check_routing("agent.merchant.example", resolver=resolver))[2] == "cname_wrong_target"


@pytest.mark.parametrize(("status", "expected"), [(200, True), (404, True), (403, False), (500, False)])
def test_delete_domain_mapping(monkeypatch: pytest.MonkeyPatch, status: int, expected: bool) -> None:
    async def token() -> str:
        return "synthetic-token"

    monkeypatch.setattr(cd, "_access_token", token)
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(status, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert asyncio.run(cd.delete_domain_mapping("agent.merchant.example", client=client)) is expected
    assert seen == [
        (
            "DELETE",
            "https://asia-southeast1-run.googleapis.com/apis/domains.cloudrun.com/v1/namespaces/example-project"
            "/domainmappings/agent.merchant.example",
        )
    ]
