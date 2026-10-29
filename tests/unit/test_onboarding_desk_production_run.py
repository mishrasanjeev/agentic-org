# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The onboarding desk production run (scripts/onboarding_desk_production_run.py), against a fake API and desk."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from api.v1.onboarding_desk import verify_signature
from core.onboarding_desk import applications, cases, documents, evidence, reports, store
from scripts import onboarding_desk_production_run as prod

BASE = "https://app.example.com"
APPLICATION = str(uuid.UUID(int=21))
DOCUMENT = str(uuid.UUID(int=22))
KEY = {"alg": "Ed25519", "key_id": "0123456789abcdef", "public_key": "cHVibGlj"}


def test_the_synthetic_certificate_is_read_and_suggests_the_three_values() -> None:
    pdf = prod.certificate_pdf(prod.CERTIFICATE_LINES)
    assert documents.sniff(pdf) == "application/pdf"
    text, status = documents._read_text(pdf, "application/pdf")
    assert status == "read"
    suggested = {s["item"]: s["value"] for s in documents.suggest(text, items=documents_items(), country="CA")}
    assert suggested == {
        "legal_name": prod.BUSINESS_NAME,
        "registration_number": prod.CORPORATION_NUMBER,
        "tax_id": prod.BUSINESS_NUMBER,
    }


def documents_items() -> tuple[str, ...]:
    return ("legal_name", "registration_number", "tax_id")


def test_the_lead_is_signed_as_the_desk_verifies_and_is_plainly_synthetic() -> None:
    from datetime import UTC, datetime

    body = prod.lead_body("production-run-1700000000", datetime(2026, 10, 28, tzinfo=UTC))
    assert verify_signature("secret", prod.webhook_signature("secret", body, __import__("time").time()), body)
    assert not verify_signature("other", prod.webhook_signature("secret", body, __import__("time").time()), body)
    lead = json.loads(body)
    assert lead["email"].endswith("@example.com")
    assert "555-01" in lead["phone"].replace(" ", "-")
    assert {c["channel"] for c in lead["consents"]} == {"sms", "email"}


@pytest.mark.parametrize("value", ["http://app.example.com", "https://app.example.com/api", "https://", "ftp://x"])
def test_only_an_https_origin_is_accepted(value: str) -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        prod.check_base_url(value)
    assert prod.check_base_url("https://app.example.com/") == "https://app.example.com"


def test_nothing_runs_without_the_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    assert prod.main(["--base-url", BASE, "--confirm", "yes"]) == 2
    assert prod.CONFIRM in capsys.readouterr().err


class FakeApi:
    """The public API the run drives: the webhook, the applicant routes, health and the evidence key."""

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.leads: dict[str, int] = {}
        self.retired = False
        self.answers: list[dict[str, Any]] = []
        self.external_ref = ""

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/health":
            return httpx.Response(200, json={"status": "healthy", "commit": "abc123"})
        if path.startswith(f"{prod.DESK}/webhooks/leads/"):
            signature = request.headers.get("x-agenticorg-signature", "")
            if self.retired or not verify_signature(self.secret, signature, request.content):
                return httpx.Response(401, json={"detail": {"error": "signature_invalid"}})
            ref = json.loads(request.content)["external_ref"]
            self.external_ref = ref
            seen = self.leads.get(ref, 0)
            self.leads[ref] = seen + 1
            return httpx.Response(200, json={"created": int(not seen), "existing": int(bool(seen))})
        if path == f"{prod.DESK}/apply/session":
            return httpx.Response(
                200, json={"session": "s", "partner_name": prod.PARTNER_NAME, "application": {"id": APPLICATION}}
            )
        assert request.headers.get("x-applicant-session") == "s" or path == f"{prod.DESK}/evidence-key"
        if path == f"{prod.DESK}/apply/documents":
            suggestions = [
                {"item": "legal_name", "value": prod.BUSINESS_NAME},
                {"item": "registration_number", "value": prod.CORPORATION_NUMBER},
                {"item": "tax_id", "value": prod.BUSINESS_NUMBER},
            ]
            return httpx.Response(200, json={"id": DOCUMENT, "suggestions": suggestions})
        if path == f"{prod.DESK}/apply/documents/{DOCUMENT}/confirm":
            return httpx.Response(200, json={"application": {}, "errors": {}})
        if path == f"{prod.DESK}/apply/answers":
            answers = json.loads(request.content)["answers"]
            self.answers.append(answers)
            if (answers.get("registered_address") or {}).get("postal_code") == "90210":
                return httpx.Response(200, json={"errors": {"registered_address": {"code": "postal_code_invalid"}}})
            return httpx.Response(200, json={"errors": {}})
        if path == f"{prod.DESK}/apply/submit":
            return httpx.Response(200, json={"status": "submitted"})
        if path == f"{prod.DESK}/evidence-key":
            return httpx.Response(200, json=KEY)
        return httpx.Response(404)


@pytest.fixture
def desk(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The desk, database and transport the run uses, faked; ``state`` records what the run did."""
    state: dict[str, Any] = {"tenants": [], "retired": [], "case_ref": None, "attempts": [], "problems": []}
    api = FakeApi("whsec")
    state["api"] = api
    lead_id = uuid.UUID(int=31)

    class _PlatformSession:
        async def get(self, _model: Any, tenant_id: uuid.UUID) -> Any:
            return state["tenant_rows"].get(tenant_id)

        def add(self, row: Any) -> None:
            state["tenants"].append(row)
            state.setdefault("tenant_rows", {})[row.id] = row

    @asynccontextmanager
    async def _platform_session() -> Any:
        yield _PlatformSession()

    class _TenantSession:
        async def execute(self, statement: Any) -> None:
            state["retired"].append(str(statement).split()[0])

    @asynccontextmanager
    async def _tenant_session(_tenant_id: uuid.UUID) -> Any:
        yield _TenantSession()

    async def _branding(_tenant_id: uuid.UUID, raw: dict[str, Any], *, actor: str) -> Any:
        state["branding"] = raw
        return SimpleNamespace(auto_contact=raw["auto_contact"], sms_conversation=raw["sms_conversation"], senders={})

    async def _case_settings(_tenant_id: uuid.UUID, raw: dict[str, Any], *, actor: str) -> Any:
        return SimpleNamespace(auto_open=raw["auto_open"])

    async def _source(_tenant_id: uuid.UUID, _name: str, kind: str, *, actor: str) -> Any:
        assert kind == "webhook"
        return SimpleNamespace(id=uuid.UUID(int=41)), "whsec"

    async def _list_leads(_tenant_id: uuid.UUID) -> list[Any]:
        return [SimpleNamespace(id=lead_id, external_ref=api.external_ref)]

    async def _get_lead(_tenant_id: uuid.UUID, _lead_id: uuid.UUID) -> Any:
        consents = [SimpleNamespace(channel=c, method="web_form", status="granted") for c in ("sms", "email")]
        return SimpleNamespace(id=lead_id), consents

    async def _decision(_tenant_id: uuid.UUID, _lead_id: uuid.UUID, channel: str) -> Any:
        if channel == "whatsapp":
            return SimpleNamespace(allowed=False, reason="no_consent")
        return SimpleNamespace(allowed=True, reason="allowed")

    async def _attempts(_tenant_id: uuid.UUID, _lead_id: uuid.UUID) -> list[Any]:
        return state["attempts"]

    async def _application(_tenant_id: uuid.UUID, _application_id: uuid.UUID) -> Any:
        return SimpleNamespace(status="submitted", case_ref=state["case_ref"])

    async def _pack(_tenant_id: uuid.UUID, _application_id: uuid.UUID) -> dict[str, Any]:
        return {
            "manifest": {"sections": {"application": "sha256:x", "lead": "sha256:y"}},
            "signature": {},
            "sections": {},
        }

    async def _summary(_tenant_id: uuid.UUID, _period: Any) -> dict[str, Any]:
        return {
            "funnel": {"leads": 1, "applications": 1, "applications_by_status": {"submitted": 1}},
            "consent_coverage": {"any_channel": 1.0},
        }

    monkeypatch.setattr("core.database.get_platform_session", _platform_session)
    monkeypatch.setattr(store, "_session", _tenant_session)
    monkeypatch.setattr(store, "enabled", lambda: True)
    monkeypatch.setattr(store, "put_branding", _branding)
    monkeypatch.setattr(store, "create_source", _source)
    monkeypatch.setattr(store, "list_leads", _list_leads)
    monkeypatch.setattr(store, "get_lead", _get_lead)
    monkeypatch.setattr(store, "contact_decision", _decision)
    monkeypatch.setattr(store, "list_attempts", _attempts)
    monkeypatch.setattr(cases, "put_case_settings", _case_settings)
    monkeypatch.setattr(applications, "get_application", _application)
    monkeypatch.setattr(evidence, "public_key", lambda: dict(KEY))
    monkeypatch.setattr(evidence, "build_pack", _pack)
    monkeypatch.setattr(evidence, "verify", lambda _pack, _key: list(state["problems"]))
    monkeypatch.setattr(reports, "summary", _summary)
    monkeypatch.setattr("core.onboarding_desk.tokens.sign", lambda *_args, **_kwargs: "apply-token")

    real_client = httpx.AsyncClient

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            response = api.handle(request)
            return response

        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(prod.httpx, "AsyncClient", _client)

    original_retire = prod.retire

    async def _retire(tenant_id: uuid.UUID, source_id: str | None) -> None:
        await original_retire(tenant_id, source_id)
        api.retired = True

    monkeypatch.setattr(prod, "retire", _retire)
    return state


async def test_a_full_run_passes_every_step_and_retires_its_tenant(desk: dict[str, Any]) -> None:
    run = await prod.main_async(BASE, keep=False)
    summary = run.summary()

    assert summary["ok"], summary["steps"]
    assert [step["step"] for step in summary["steps"]] == [
        "disposable_tenant_configured",
        "signed_lead_with_consent",
        "contact_rules",
        "document_read_and_confirmed",
        "application_submitted",
        "no_case_opened",
        "evidence_pack_signed_and_verified",
        "report_and_nothing_sent",
        "tenant_retired",
    ]
    (tenant,) = desk["tenants"]
    assert tenant.settings["disposable"] is True
    assert "disposable" in tenant.name and tenant.slug.startswith("desk-production-run-")
    assert tenant.deleted_at is not None and "retired_at" in tenant.settings
    assert desk["retired"] == ["UPDATE"]
    assert desk["branding"]["auto_contact"] is False and "senders" not in desk["branding"]
    assert summary["application_id"] == APPLICATION
    assert summary["evidence"] == {"key_id": KEY["key_id"], "sections": ["application", "lead"]}
    assert summary["api_commit"] == "abc123"
    # The wrong postal code went first and was refused; the fixed address followed.
    assert desk["api"].answers[0]["registered_address"]["postal_code"] == "90210"
    assert desk["api"].answers[1]["registered_address"]["postal_code"] == "A0A 0A0"
    assert "whsec" not in json.dumps(summary) and "apply-token" not in json.dumps(summary)


async def test_keep_leaves_the_tenant_active(desk: dict[str, Any]) -> None:
    run = await prod.main_async(BASE, keep=True)
    assert run.summary()["ok"]
    assert desk["tenants"][0].deleted_at is None
    assert desk["retired"] == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"case_ref": "case_unexpected"}, "a case was opened"),
        ({"attempts": [object()]}, "contact attempts were recorded"),
        ({"problems": ["section application does not match its digest"]}, "does not verify"),
    ],
)
async def test_a_broken_promise_fails_the_run_and_still_retires_the_tenant(
    desk: dict[str, Any], change: dict[str, Any], message: str
) -> None:
    desk.update(change)
    summary = (await prod.main_async(BASE, keep=False)).summary()

    assert not summary["ok"]
    failed = [step for step in summary["steps"] if not step["ok"]]
    assert failed[0]["step"] == "run" and message in failed[0]["error"]
    assert summary["steps"][-1] == {"step": "tenant_retired", "ok": True, "signed_lead_after_retirement": 401}


async def test_a_desk_that_is_off_creates_nothing(desk: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "enabled", lambda: False)
    summary = (await prod.main_async(BASE, keep=False)).summary()
    assert summary["steps"] == [
        {"step": "preflight", "ok": False, "error": "the onboarding desk is off in this runtime"}
    ]
    assert desk["tenants"] == []


def test_main_prints_the_summary_and_exits_by_its_outcome(
    desk: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert prod.main(["--base-url", BASE, "--confirm", prod.CONFIRM]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
