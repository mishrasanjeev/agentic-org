#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A labelled, disposable run of the merchant onboarding desk in production, end to end but for the checks.

The run creates its own tenant, named and tagged as a disposable production run, and drives the desk the
way a partner and an applicant would, through the live public API:

1. a signed lead from the partner's form, with the consent it collected (an unsigned post is refused);
2. the contact rules for that lead (decisions only: the tenant has no message senders, so the desk
   cannot send anything, and ``auto_contact`` and ``sms_conversation`` stay off);
3. a signed link to the application, then the applicant's own session: a synthetic certificate is read
   and its values confirmed, a wrong postal code is refused, the remaining answers are saved and the
   application is submitted;
4. no governed case is opened (``auto_open`` off): the checks need the verification partner's credential,
   and the mock provider and the example policies refuse a production runtime by design;
5. the evidence pack is built and checked with the published key, as built and as served;
6. the partner report counts the lead and the application, and no contact attempt was recorded.

At the end the tenant is retired: its webhook source is switched off (a signed post is then refused) and
the tenant is soft-deleted, so nothing it holds is used again; the rows stay for the audit trail. Every
name, number and address is synthetic and every domain is under ``example.com``.

Run it as a one-off Cloud Run job on the API image, with the API's database, secret key, vault keyring
and evidence key (docs/onboarding-desk/production-run.md)::

    python -m scripts.onboarding_desk_production_run --base-url https://app.agenticorg.ai \\
        --confirm onboarding-desk-production-run

It prints one JSON summary, with no secret, token or contact value in it, and exits 0 only when every
step passed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import secrets
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

CONFIRM = "onboarding-desk-production-run"
ACTOR = "script:onboarding_desk_production_run"
DESK = "/api/v1/onboarding-desk"
PARTNER_NAME = "Production run partner (synthetic, not a real partner)"
BUSINESS_NAME = "Production Run Example Bakery Inc."
CORPORATION_NUMBER = "0000001"
BUSINESS_NUMBER = "000000001"
CONSENT_WORDING = "Yes, the production run partner may contact me about accepting card payments. Reply STOP to opt out."
TIMEOUT_SECONDS = 30.0


class RunError(RuntimeError):
    """A step did not behave as the desk promises; the run stops and the tenant is retired."""


@dataclass
class Run:
    started_at: str
    base_url: str
    label: str
    tenant_id: str = ""
    slug: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def passed(self, step: str, **detail: Any) -> None:
        self.steps.append({"step": step, "ok": True, **detail})

    def failed(self, step: str, error: str) -> None:
        self.steps.append({"step": step, "ok": False, "error": error[:500]})

    def summary(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "started_at": self.started_at,
            "base_url": self.base_url,
            "tenant_id": self.tenant_id,
            "tenant_slug": self.slug,
            "ok": bool(self.steps) and all(step["ok"] for step in self.steps),
            "steps": self.steps,
            **self.facts,
        }


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise RunError(message)


# ── Synthetic inputs ─────────────────────────────────────────────────────────


def certificate_pdf(lines: Sequence[str]) -> bytes:
    """A one-page PDF with a text layer, in Helvetica: enough for the desk's document reader."""

    def escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = "BT /F1 12 Tf 72 740 Td 16 TL " + " ".join(f"({escape(line)}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
        "/Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode("latin-1")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1")
    return bytes(out)


CERTIFICATE_LINES = (
    "Certificate of Incorporation",
    "SYNTHETIC DOCUMENT FOR A PRODUCTION RUN OF THE ONBOARDING DESK - NOT A REAL BUSINESS",
    f"Corporate name: {BUSINESS_NAME}",
    f"Corporation number: {CORPORATION_NUMBER}",
    f"Business Number: {BUSINESS_NUMBER} RT0001",
    "Registered office: 100 Example Street, Toronto ON A0A 0A0",
    "Date of incorporation: 2019-03-14",
)


def lead_body(external_ref: str, now: datetime) -> bytes:
    stamp = external_ref.rsplit("-", 1)[-1]
    lead = {
        "business_name": BUSINESS_NAME,
        "contact_name": "Maria Example",
        "email": f"production-run.{stamp}@example.com",
        "phone": "+1 416 555 0142",
        "country": "CA",
        "region": "ON",
        "locale": "en",
        "external_ref": external_ref,
        "consents": [
            {
                "channel": channel,
                "wording": CONSENT_WORDING,
                "method": "web_form",
                "source_ref": "https://production-run.example.com/apply",
                "captured_at": (now - timedelta(minutes=1)).isoformat(),
            }
            for channel in ("sms", "email")
        ],
    }
    return json.dumps(lead, separators=(",", ":")).encode("utf-8")


def webhook_signature(secret: str, body: bytes, now: float) -> str:
    """``t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>." + body>``, as a partner's form signs a lead."""
    t = int(now)
    mac = hmac.new(secret.encode("utf-8"), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


# ── The tenant ───────────────────────────────────────────────────────────────


async def create_tenant(run: Run, now: datetime) -> uuid.UUID:
    """A new tenant, named and tagged as a disposable production run; never one that already exists."""
    from core.database import get_platform_session  # noqa: PLC0415
    from core.models.tenant import Tenant  # noqa: PLC0415

    tenant_id = uuid.uuid4()
    run.slug = f"desk-production-run-{now:%Y%m%d}-{secrets.token_hex(3)}"
    async with get_platform_session() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=run.label,
                slug=run.slug,
                plan="free",
                settings={
                    "disposable": True,
                    "purpose": "onboarding_desk_production_run",
                    "created_by": "scripts/onboarding_desk_production_run.py",
                    "created_at": now.isoformat(),
                },
            )
        )
    run.tenant_id = str(tenant_id)
    return tenant_id


async def configure(tenant_id: uuid.UUID, onboarding_url: str) -> tuple[str, str]:
    """Branding with no senders and nothing automatic, no case on submission, and a webhook lead source."""
    from core.onboarding_desk import cases, store  # noqa: PLC0415

    branding = await store.put_branding(
        tenant_id,
        {
            "partner_name": PARTNER_NAME,
            "onboarding_url": onboarding_url,
            "postal_address": "100 Example Street, Toronto ON A0A 0A0",
            "auto_contact": False,
            "sms_conversation": False,
            "privacy_url": "https://production-run.example.com/privacy",
        },
        actor=ACTOR,
    )
    expect(not branding.auto_contact and not branding.sms_conversation, "branding turned contact on")
    expect(not branding.senders, "the run's branding must have no message senders")
    settings = await cases.put_case_settings(tenant_id, {"auto_open": False, "investigate": False}, actor=ACTOR)
    expect(not settings.auto_open, "case settings would open a case")
    row, secret = await store.create_source(tenant_id, "Production run form", "webhook", actor=ACTOR)
    expect(bool(secret), "the webhook source has no signing secret")
    return str(row.id), str(secret)


async def retire(tenant_id: uuid.UUID, source_id: str | None) -> None:
    """Switch the webhook source off and soft-delete the tenant; the rows stay for the audit trail."""
    from sqlalchemy import update  # noqa: PLC0415

    from core.database import get_platform_session  # noqa: PLC0415
    from core.models.onboarding_desk import OnboardingLeadSource  # noqa: PLC0415
    from core.models.tenant import Tenant  # noqa: PLC0415
    from core.onboarding_desk import store  # noqa: PLC0415

    now = datetime.now(UTC)
    if source_id:
        async with store._session(tenant_id) as session:
            await session.execute(
                update(OnboardingLeadSource)
                .where(OnboardingLeadSource.tenant_id == tenant_id, OnboardingLeadSource.id == uuid.UUID(source_id))
                .values(enabled=False)
            )
    async with get_platform_session() as session:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is not None:
            tenant.deleted_at = now
            tenant.settings = {**(tenant.settings or {}), "retired_at": now.isoformat()}


# ── The run ──────────────────────────────────────────────────────────────────


def _ok(response: httpx.Response, what: str) -> dict[str, Any]:
    expect(response.status_code < 300, f"{what} answered {response.status_code}: {response.text[:300]}")
    return dict(response.json())


async def drive(run: Run, client: httpx.AsyncClient, tenant_id: uuid.UUID, source_id: str, secret: str) -> None:
    from core.onboarding_desk import applications, evidence, reports, store, tokens  # noqa: PLC0415

    now = datetime.now(UTC)
    external_ref = f"production-run-{int(now.timestamp())}"
    webhook = f"{DESK}/webhooks/leads/{tenant_id}/{source_id}"
    body = lead_body(external_ref, now)
    headers = {"content-type": "application/json"}

    # 1. The partner's form posts the lead, signed; an unsigned post is refused.
    unsigned = await client.post(webhook, content=body, headers=headers)
    expect(unsigned.status_code == 401, f"an unsigned lead answered {unsigned.status_code}")
    signed = {**headers, "x-agenticorg-signature": webhook_signature(secret, body, time.time())}
    created = _ok(await client.post(webhook, content=body, headers=signed), "the signed lead")
    expect(created == {"created": 1, "existing": 0}, f"the signed lead gave {created}")
    again = _ok(
        await client.post(
            webhook,
            content=body,
            headers={**headers, "x-agenticorg-signature": webhook_signature(secret, body, time.time())},
        ),
        "the repeated lead",
    )
    expect(again == {"created": 0, "existing": 1}, f"the repeated lead gave {again}")
    leads = await store.list_leads(tenant_id)
    expect(len(leads) == 1 and leads[0].external_ref == external_ref, "the lead is not stored once")
    lead = leads[0]
    _, consents = await store.get_lead(tenant_id, lead.id)
    granted = sorted(f"{c.channel}:{c.method}:{c.status}" for c in consents)
    expect(granted == ["email:web_form:granted", "sms:web_form:granted"], f"consents {granted}")
    run.passed("signed_lead_with_consent", unsigned_status=401, repeated="existing", consents=granted)

    # 2. The contact rules: decisions only; with no sender configured nothing can be sent.
    decisions = {}
    for channel in ("sms", "email", "whatsapp"):
        decision = await store.contact_decision(tenant_id, lead.id, channel)
        decisions[channel] = {"allowed": decision.allowed, "reason": decision.reason}
    expect(decisions["whatsapp"] == {"allowed": False, "reason": "no_consent"}, f"whatsapp {decisions['whatsapp']}")
    expect(decisions["sms"]["reason"] in ("allowed", "outside_hours"), f"sms {decisions['sms']}")
    run.passed("contact_rules", decisions=decisions)

    # 3. A signed link, and the applicant's own session through the public API.
    session_body = _ok(
        await client.post(f"{DESK}/apply/session", json={"t": tokens.sign("apply", tenant_id, lead.id)}), "the session"
    )
    expect(session_body["partner_name"] == PARTNER_NAME, "the applicant page shows another partner")
    applicant = {"x-applicant-session": session_body["session"]}
    application_id = session_body["application"]["id"]
    run.facts["application_id"] = application_id

    upload = _ok(
        await client.post(
            f"{DESK}/apply/documents",
            headers=applicant,
            data={"item": "formation_document"},
            files={"file": ("certificate-of-incorporation.pdf", certificate_pdf(CERTIFICATE_LINES), "application/pdf")},
        ),
        "the certificate upload",
    )
    suggested = {s["item"]: s["value"] for s in upload["suggestions"]}
    expect(
        suggested
        == {"legal_name": BUSINESS_NAME, "registration_number": CORPORATION_NUMBER, "tax_id": BUSINESS_NUMBER},
        f"the certificate suggested {sorted(suggested)}",
    )
    confirmed = _ok(
        await client.post(
            f"{DESK}/apply/documents/{upload['id']}/confirm", headers=applicant, json={"items": sorted(suggested)}
        ),
        "confirming the suggestions",
    )
    expect(not confirmed["errors"], f"confirming gave {confirmed['errors']}")
    run.passed("document_read_and_confirmed", suggested=sorted(suggested))

    address = {"lines": ["100 Example Street"], "locality": "Toronto", "region": "ON", "postal_code": "90210"}
    refused = _ok(
        await client.put(f"{DESK}/apply/answers", headers=applicant, json={"answers": {"registered_address": address}}),
        "the wrong postal code",
    )
    expect("registered_address" in refused["errors"], "a US postal code was accepted for a Canadian address")
    answers = {
        "business_structure": "corporation",
        "activity": "food_service",
        "registered_address": {**address, "postal_code": "A0A 0A0"},
        "incorporated_in": "ON",
        "owners": [
            {"name": "Maria Example", "role": "owner_and_controller", "ownership_pct": 100, "date_of_birth": "1980-04"}
        ],
        "website": "https://production-run-bakery.example.com",
    }
    saved = _ok(await client.put(f"{DESK}/apply/answers", headers=applicant, json={"answers": answers}), "the answers")
    expect(not saved["errors"], f"the answers gave {saved['errors']}")
    submitted = _ok(await client.post(f"{DESK}/apply/submit", headers=applicant), "the submission")
    expect(submitted["status"] == "submitted", f"the application is {submitted['status']}")
    run.passed(
        "application_submitted",
        refused_postal_code=refused["errors"]["registered_address"]["code"],
        answered=sorted(set(answers) | set(suggested) | {"formation_document"}),
    )

    # 4. No case: auto_open is off, and the checks cannot run here without the partner's credential.
    row = await applications.get_application(tenant_id, uuid.UUID(application_id))
    expect(row.status == "submitted" and row.case_ref is None, "a case was opened")
    run.passed("no_case_opened", reason="auto_open off; checks need the verification partner's credential")

    # 5. The evidence pack, signed with the published key, as built and as the API serves it.
    key = evidence.public_key()
    expect(key is not None, "evidence packs are not signed: the evidence key is not set")
    assert key is not None
    published = _ok(await client.get(f"{DESK}/evidence-key"), "the published evidence key")
    expect(published == key, "the published evidence key differs from the run's")
    pack = await evidence.build_pack(tenant_id, uuid.UUID(application_id))
    served = json.loads(json.dumps(pack, indent=2, ensure_ascii=False, default=str))
    problems = evidence.verify(pack, key["public_key"]) + evidence.verify(served, key["public_key"])
    expect(not problems, f"the evidence pack does not verify: {problems}")
    run.facts["evidence"] = {"key_id": key["key_id"], "sections": sorted(pack["manifest"]["sections"])}
    run.passed("evidence_pack_signed_and_verified", key_id=key["key_id"])

    # 6. The partner report, and proof that nothing was sent.
    report = await reports.summary(tenant_id, reports.period_from(None, None))
    funnel = report["funnel"]
    expect(funnel["leads"] == 1 and funnel["applications"] == 1, f"the report funnel is {funnel}")
    expect(funnel["applications_by_status"] == {"submitted": 1}, f"applications {funnel['applications_by_status']}")
    expect(report["consent_coverage"]["any_channel"] == 1.0, f"consent coverage {report['consent_coverage']}")
    attempts = await store.list_attempts(tenant_id, lead.id)
    expect(not attempts, f"{len(attempts)} contact attempts were recorded")
    run.facts["report"] = {"funnel": funnel, "consent_coverage": report["consent_coverage"]}
    run.passed("report_and_nothing_sent", contact_attempts=0)


async def main_async(base_url: str, keep: bool, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Run:
    from core.onboarding_desk import store  # noqa: PLC0415

    started = now()
    run = Run(
        started_at=started.isoformat(),
        base_url=base_url,
        label=f"Onboarding desk production run {started:%Y-%m-%d %H:%M} UTC (disposable, synthetic data)",
    )
    if not store.enabled():
        run.failed("preflight", "the onboarding desk is off in this runtime")
        return run
    tenant_id: uuid.UUID | None = None
    source_id: str | None = None
    secret = ""
    async with httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        try:
            health = _ok(await client.get("/api/v1/health"), "health")
            run.facts["api_commit"] = health.get("commit")
            tenant_id = await create_tenant(run, started)
            source_id, secret = await configure(tenant_id, f"{base_url}/onboarding-desk/apply")
            run.passed("disposable_tenant_configured", senders=0, auto_contact=False, auto_open=False)
            await drive(run, client, tenant_id, source_id, secret)
        except (RunError, httpx.HTTPError, KeyError, ValueError) as exc:
            run.failed("run", f"{type(exc).__name__}: {exc}")
        finally:
            if tenant_id is not None and not keep:
                await retire(tenant_id, source_id)
                if source_id and secret:
                    body = lead_body(f"production-run-retired-{int(time.time())}", now())
                    late = await client.post(
                        f"{DESK}/webhooks/leads/{tenant_id}/{source_id}",
                        content=body,
                        headers={
                            "content-type": "application/json",
                            "x-agenticorg-signature": webhook_signature(secret, body, time.time()),
                        },
                    )
                    if late.status_code == 401:
                        run.passed("tenant_retired", signed_lead_after_retirement=401)
                    else:
                        run.failed("tenant_retired", f"a signed lead after retirement answered {late.status_code}")
    return run


def check_base_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") or parts.query:
        raise argparse.ArgumentTypeError("--base-url must be the API's https origin, e.g. https://app.agenticorg.ai")
    return f"https://{parts.netloc}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A labelled, disposable production run of the onboarding desk.")
    parser.add_argument("--base-url", required=True, type=check_base_url, help="the API's public https origin")
    parser.add_argument("--confirm", required=True, help=f"must be {CONFIRM!r}: this writes to the runtime's database")
    parser.add_argument("--keep", action="store_true", help="leave the tenant and its webhook source active")
    args = parser.parse_args(argv)
    if args.confirm != CONFIRM:
        print(f"onboarding_desk_production_run: pass --confirm {CONFIRM} to run", file=sys.stderr)
        return 2
    run = asyncio.run(main_async(args.base_url, args.keep))
    summary = run.summary()
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
