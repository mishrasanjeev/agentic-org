# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""WhatsApp delivery of scheduled reports: the approved template, the 24-hour window, refusals and masked numbers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from core.reports import delivery

CREDS = {"WHATSAPP_TOKEN": "wa-test-token", "WHATSAPP_PHONE_ID": "1234567"}


def _poster(send: httpx.Response | None = None, upload: httpx.Response | None = None):
    calls: list[tuple[str, dict]] = []

    async def post(self, url, **kwargs):
        calls.append((url, kwargs))
        request = httpx.Request("POST", url)
        if url.endswith("/media"):
            return upload or httpx.Response(200, json={"id": "media-1"}, request=request)
        return send or httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}, request=request)

    return post, calls


def _file(tmp_path: Path, name: str = "insights-finance.pdf") -> str:
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 test")
    return str(path)


def test_numbers_are_masked_to_their_last_four_digits() -> None:
    assert delivery.mask_phone("+91 98765 43210") == "***3210"
    assert delivery.mask_phone("") == "***" and delivery.mask_phone(None) == "***"  # type: ignore[arg-type]


def test_the_template_payload_and_the_plain_document(monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "whatsapp_report_template", "insights_report")
    monkeypatch.setattr(settings, "whatsapp_report_template_language", "hi")
    monkeypatch.setattr(settings, "whatsapp_report_template_body_text", True)
    payload = delivery.whatsapp_message("+919876543210", "media-1", "report.pdf", "Insights report: Finance")
    assert payload["type"] == "template"
    template = payload["template"]
    assert template["name"] == "insights_report" and template["language"] == {"code": "hi"}
    assert template["components"][0] == {
        "type": "header",
        "parameters": [{"type": "document", "document": {"id": "media-1", "filename": "report.pdf"}}],
    }
    assert template["components"][1]["parameters"][0]["text"] == "Insights report: Finance"
    monkeypatch.setattr(settings, "whatsapp_report_template_body_text", False)
    assert len(delivery.whatsapp_message("+91", "m", "r.pdf", "x")["template"]["components"]) == 1
    monkeypatch.setattr(settings, "whatsapp_report_template", "")
    plain = delivery.whatsapp_message("+91", "m", "r.pdf", "")
    assert plain["type"] == "document" and plain["document"]["caption"] == "Scheduled report from AgenticOrg"


@pytest.mark.asyncio
async def test_a_template_send_uses_the_configured_api_version(tmp_path, monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "whatsapp_report_template", "insights_report")
    monkeypatch.setattr(settings, "whatsapp_graph_api_version", "v23.0")
    post, calls = _poster()
    with patch.dict(os.environ, CREDS), patch.object(httpx.AsyncClient, "post", post):
        result = await delivery.deliver_whatsapp(_file(tmp_path), "+919876543210", "Insights report: Finance")
    assert result["status"] == "sent" and result["mode"] == "template" and result["media_id"] == "media-1"
    assert [url for url, _ in calls] == [
        "https://graph.facebook.com/v23.0/1234567/media",
        "https://graph.facebook.com/v23.0/1234567/messages",
    ]
    assert calls[1][1]["json"]["type"] == "template"


@pytest.mark.asyncio
async def test_a_plain_document_outside_the_window_fails_with_the_reason(tmp_path, monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "whatsapp_report_template", "")
    refused = httpx.Response(
        400,
        json={"error": {"code": 131047, "title": "Re-engagement message", "message": "outside window"}},
        request=httpx.Request("POST", "https://graph.facebook.com/x"),
    )
    post, _calls = _poster(send=refused)
    with patch.dict(os.environ, CREDS), patch.object(httpx.AsyncClient, "post", post):
        with pytest.raises(RuntimeError, match="24-hour window.*AGENTICORG_WHATSAPP_REPORT_TEMPLATE"):
            await delivery.deliver_whatsapp(_file(tmp_path), "+919876543210")


@pytest.mark.asyncio
async def test_refusals_are_reported_not_counted_as_sent(tmp_path, monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "whatsapp_report_template", "insights_report")
    bad_template = httpx.Response(
        400,
        json={"error": {"code": 132001, "error_data": {"details": "template name does not exist"},
                        "message": "(#132001) Template name does not exist in the translation"}},
        request=httpx.Request("POST", "https://graph.facebook.com/x"),
    )
    post, _ = _poster(send=bad_template)
    with patch.dict(os.environ, CREDS), patch.object(httpx.AsyncClient, "post", post):
        with pytest.raises(RuntimeError, match="send failed: 132001 template name does not exist"):
            await delivery.deliver_whatsapp(_file(tmp_path), "+919876543210")
    # A 200 without a message id is not a delivery either.
    empty = httpx.Response(200, json={}, request=httpx.Request("POST", "https://graph.facebook.com/x"))
    post, _ = _poster(send=empty)
    with patch.dict(os.environ, CREDS), patch.object(httpx.AsyncClient, "post", post):
        with pytest.raises(RuntimeError, match="send failed: HTTP 200"):
            await delivery.deliver_whatsapp(_file(tmp_path), "+919876543210")
    rejected_upload = httpx.Response(
        401, json={"error": {"code": 190, "message": "Invalid OAuth access token"}},
        request=httpx.Request("POST", "https://graph.facebook.com/x"),
    )
    post, calls = _poster(upload=rejected_upload)
    with patch.dict(os.environ, CREDS), patch.object(httpx.AsyncClient, "post", post):
        with pytest.raises(RuntimeError, match="media upload failed: 190 Invalid OAuth access token"):
            await delivery.deliver_whatsapp(_file(tmp_path), "+919876543210")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_zip_pack_is_skipped_without_calling_whatsapp(tmp_path) -> None:
    post, calls = _poster()
    with patch.dict(os.environ, CREDS), patch.object(httpx.AsyncClient, "post", post):
        result = await delivery.deliver_whatsapp(_file(tmp_path, "finance-pack.zip"), "+919876543210")
    assert result["status"] == "skipped" and ".zip" in result["reason"] and calls == []


@pytest.mark.asyncio
async def test_the_dispatcher_records_the_reason_for_a_failed_channel(tmp_path, monkeypatch) -> None:
    async def failing(report_path, phone_number, caption=""):
        raise RuntimeError("WhatsApp send failed: 131026 Message undeliverable")

    monkeypatch.setattr(delivery, "deliver_whatsapp", failing)
    results = await delivery.deliver(_file(tmp_path), [{"type": "whatsapp", "target": "+919876543210"}])
    assert results[0]["status"] == "failed" and results[0]["reason"].startswith("WhatsApp send failed: 131026")


def test_the_settings_refuse_malformed_values() -> None:
    from pydantic import ValidationError

    from core.config import Settings

    for field, value in (
        ("whatsapp_graph_api_version", "latest"),
        ("whatsapp_report_template", "Insights Report"),
        ("whatsapp_report_template_language", "english"),
    ):
        with pytest.raises(ValidationError):
            Settings(**{field: value})
    assert Settings().whatsapp_graph_api_version == "v23.0"
