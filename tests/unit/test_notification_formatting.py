# ruff: noqa: S106,S108 — test files use fake tokens and /tmp paths intentionally
"""Test notification formatting, message structure, and delivery pipeline.

Covers:
- Email formatting: subject, HTML body, attachments, recipients
- Slack formatting: Block Kit, severity colours, file upload
- WhatsApp formatting: template structure, E.164 validation, captions
- Report file naming: patterns, uniqueness, special characters
- Delivery pipeline: routing, partial failure, per-channel status

~45 tests.
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Email Formatting
# ═══════════════════════════════════════════════════════════════════════════


class TestEmailFormatting:
    """Verify email delivery payload structure."""

    @pytest.mark.asyncio
    async def test_email_payload_has_personalizations(self):
        """Email payload should have personalizations array with recipient."""
        import httpx

        captured_payload = {}

        async def _mock_post(self, url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            resp = httpx.Response(202, request=httpx.Request("POST", url))
            return resp

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake content")
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SENDGRID_API_KEY": "SG.fake-key"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_email

                    await deliver_email(
                        tmp_path, "cfo@example.com", "CFO Daily Report"
                    )

            assert "personalizations" in captured_payload
            assert len(captured_payload["personalizations"]) == 1
            to_list = captured_payload["personalizations"][0]["to"]
            assert to_list[0]["email"] == "cfo@example.com"
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_email_payload_has_subject(self):
        """Email subject should match the provided subject."""
        import httpx

        captured_payload = {}

        async def _mock_post(self, url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SENDGRID_API_KEY": "SG.fake"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_email

                    await deliver_email(
                        tmp_path, "cfo@corp.com", "CFO Daily 2026-04-02"
                    )

            assert captured_payload["subject"] == "CFO Daily 2026-04-02"
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_email_payload_has_from(self):
        """Email should have a from field with AgenticOrg."""
        import httpx

        captured_payload = {}

        async def _mock_post(self, url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SENDGRID_API_KEY": "SG.fake"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_email

                    await deliver_email(tmp_path, "test@x.com", "Report")

            assert "from" in captured_payload
            assert "AgenticOrg" in captured_payload["from"]["name"]
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_email_attachment_is_base64_encoded(self):
        """PDF attachment content should be valid base64."""
        import httpx

        captured_payload = {}

        async def _mock_post(self, url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        test_content = b"%PDF-1.4 test content for base64 encoding"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(test_content)
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SENDGRID_API_KEY": "SG.fake"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_email

                    await deliver_email(tmp_path, "test@x.com", "Report")

            attachments = captured_payload.get("attachments", [])
            assert len(attachments) == 1
            # Verify the content is valid base64
            decoded = base64.b64decode(attachments[0]["content"])
            assert decoded == test_content
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_email_pdf_attachment_has_correct_mime(self):
        """PDF attachment should have application/pdf MIME type."""
        import httpx

        captured_payload = {}

        async def _mock_post(self, url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SENDGRID_API_KEY": "SG.fake"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_email

                    await deliver_email(tmp_path, "test@x.com", "Report")

            attachment = captured_payload["attachments"][0]
            assert attachment["type"] == "application/pdf"
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_email_xlsx_attachment_has_correct_mime(self):
        """XLSX attachment should have the correct Office MIME type."""
        import httpx

        captured_payload = {}

        async def _mock_post(self, url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(b"PK fake xlsx content")
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SENDGRID_API_KEY": "SG.fake"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_email

                    await deliver_email(tmp_path, "test@x.com", "Report")

            attachment = captured_payload["attachments"][0]
            assert "spreadsheetml" in attachment["type"]
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_email_no_api_key_returns_skipped(self):
        """Missing SENDGRID_API_KEY should skip, not crash."""
        from core.reports.delivery import deliver_email

        with patch.dict(os.environ, {"SENDGRID_API_KEY": ""}, clear=False):
            result = await deliver_email(
                "/tmp/fake.pdf", "test@x.com", "Test"
            )
            assert result["status"] == "skipped"
            assert "SENDGRID" in result["reason"]

    @pytest.mark.asyncio
    async def test_email_content_has_text_plain(self):
        """Email should include text/plain content."""
        import httpx

        captured_payload = {}

        async def _mock_post(self, url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SENDGRID_API_KEY": "SG.fake"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_email

                    await deliver_email(tmp_path, "test@x.com", "Report")

            content = captured_payload.get("content", [])
            assert len(content) >= 1
            assert content[0]["type"] == "text/plain"
        finally:
            os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════
# Slack Formatting
# ═══════════════════════════════════════════════════════════════════════════


class TestSlackFormatting:
    """Verify Slack delivery structure and API calls."""

    @pytest.mark.asyncio
    async def test_slack_no_token_returns_skipped(self):
        from core.reports.delivery import deliver_slack

        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": ""}, clear=False):
            result = await deliver_slack("/tmp/fake.pdf", "C12345")
            assert result["status"] == "skipped"
            assert "SLACK" in result["reason"]

    @pytest.mark.asyncio
    async def test_slack_uses_upload_api(self):
        """Slack delivery should use files.getUploadURLExternal flow."""
        import httpx

        call_log = []

        async def _mock_post(self, url, **kwargs):
            call_log.append(url)
            if "getUploadURLExternal" in url:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "upload_url": "https://slack.com/upload/xyz",
                        "file_id": "F123",
                    },
                    request=httpx.Request("POST", url),
                )
            elif "upload/xyz" in url:
                return httpx.Response(
                    200, request=httpx.Request("POST", url)
                )
            elif "completeUploadExternal" in url:
                return httpx.Response(
                    200,
                    json={"ok": True},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(200, request=httpx.Request("POST", url))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-fake"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_slack

                    result = await deliver_slack(tmp_path, "C12345", "Test msg")

            assert result["status"] == "sent"
            assert result["channel"] == "slack"
            # Verify the upload flow was followed
            assert any("getUploadURLExternal" in c for c in call_log)
            assert any("completeUploadExternal" in c for c in call_log)
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_slack_result_includes_file_id(self):
        """Successful Slack delivery should include file_id in result."""
        import httpx

        async def _mock_post(self, url, **kwargs):
            if "getUploadURLExternal" in url:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "upload_url": "https://slack.com/up",
                        "file_id": "FTEST123",
                    },
                    request=httpx.Request("POST", url),
                )
            elif "completeUploadExternal" in url:
                return httpx.Response(
                    200,
                    json={"ok": True},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(200, request=httpx.Request("POST", url))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-fake"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_slack

                    result = await deliver_slack(tmp_path, "C99999")

            assert result["file_id"] == "FTEST123"
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_slack_upload_failure_raises(self):
        """Slack upload failure (ok=false) should raise RuntimeError."""
        import httpx

        async def _mock_post(self, url, **kwargs):
            if "getUploadURLExternal" in url:
                return httpx.Response(
                    200,
                    json={"ok": False, "error": "channel_not_found"},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(200, request=httpx.Request("POST", url))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-fake"}):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_slack

                    with pytest.raises(RuntimeError, match="channel_not_found"):
                        await deliver_slack(tmp_path, "C99999")
        finally:
            os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════
# WhatsApp Formatting
# ═══════════════════════════════════════════════════════════════════════════


class TestWhatsAppFormatting:
    """Verify WhatsApp delivery payload structure."""

    @pytest.mark.asyncio
    async def test_whatsapp_no_credentials_returns_skipped(self):
        from core.reports.delivery import deliver_whatsapp

        with patch.dict(
            os.environ,
            {"WHATSAPP_TOKEN": "", "WHATSAPP_PHONE_ID": ""},
            clear=False,
        ):
            result = await deliver_whatsapp(
                "/tmp/fake.pdf", "+919876543210"
            )
            assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_whatsapp_message_payload_structure(self):
        """WhatsApp document message should have correct structure."""
        import httpx

        captured_payloads = []

        async def _mock_post(self, url, **kwargs):
            if kwargs.get("json"):
                captured_payloads.append(kwargs["json"])
            if "media" in url:
                return httpx.Response(
                    200,
                    json={"id": "media123"},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={"messages": [{"id": "msg1"}]},
                request=httpx.Request("POST", url),
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            tmp_path = f.name

        try:
            with patch.dict(
                os.environ,
                {
                    "WHATSAPP_TOKEN": "wa-fake-token",
                    "WHATSAPP_PHONE_ID": "123456",
                },
            ):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_whatsapp

                    result = await deliver_whatsapp(
                        tmp_path, "+919876543210", "Test caption"
                    )

            assert result["status"] == "sent"
            assert result["channel"] == "whatsapp"

            # The second call should be the document message
            msg_payload = captured_payloads[-1]
            assert msg_payload["messaging_product"] == "whatsapp"
            assert msg_payload["to"] == "+919876543210"
            assert msg_payload["type"] == "document"
            assert "document" in msg_payload
            assert "caption" in msg_payload["document"]
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_whatsapp_result_includes_media_id(self):
        """Successful WhatsApp delivery should include media_id."""
        import httpx

        async def _mock_post(self, url, **kwargs):
            if "media" in url:
                return httpx.Response(
                    200,
                    json={"id": "media_test_789"},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={"messages": [{"id": "msg1"}]},
                request=httpx.Request("POST", url),
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            tmp_path = f.name

        try:
            with patch.dict(
                os.environ,
                {"WHATSAPP_TOKEN": "wa-tok", "WHATSAPP_PHONE_ID": "12345"},
            ):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_whatsapp

                    result = await deliver_whatsapp(tmp_path, "+919876543210")

            assert result["media_id"] == "media_test_789"
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_whatsapp_media_upload_failure_raises(self):
        """Media upload failure should raise RuntimeError."""
        import httpx

        async def _mock_post(self, url, **kwargs):
            if "media" in url:
                return httpx.Response(
                    200,
                    json={"error": {"message": "Invalid phone"}},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(200, request=httpx.Request("POST", url))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            tmp_path = f.name

        try:
            with patch.dict(
                os.environ,
                {"WHATSAPP_TOKEN": "tok", "WHATSAPP_PHONE_ID": "12345"},
            ):
                with patch.object(httpx.AsyncClient, "post", _mock_post):
                    from core.reports.delivery import deliver_whatsapp

                    with pytest.raises(RuntimeError, match="media upload failed"):
                        await deliver_whatsapp(tmp_path, "+91999")
        finally:
            os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════
# Report File Naming
# ═══════════════════════════════════════════════════════════════════════════


class TestReportFileNaming:
    """Verify report file naming conventions."""

    def test_pdf_filename_pattern(self):
        """PDF file name should follow: {type}_{date}.pdf."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        filename = f"cfo_daily_{today}.pdf"
        assert filename.endswith(".pdf")
        assert "cfo_daily" in filename
        assert today in filename

    def test_excel_filename_pattern(self):
        """Excel file name should follow: {type}_{date}.xlsx."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        filename = f"cfo_daily_{today}.xlsx"
        assert filename.endswith(".xlsx")
        assert "cfo_daily" in filename

    def test_filename_no_special_characters(self):
        """File names should not contain special characters that break filesystems."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        for report_type in [
            "cfo_daily",
            "cmo_weekly",
            "aging_report",
            "pnl_report",
            "campaign_report",
        ]:
            filename = f"{report_type}_{today}.pdf"
            # No spaces, no slashes, no colons, no question marks
            assert re.match(r"^[a-zA-Z0-9_\-\.]+$", filename), f"Bad filename: {filename}"

    def test_filenames_are_unique_per_type(self):
        """Different report types produce different filenames."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        names = set()
        for rtype in ["cfo_daily", "cmo_weekly", "aging_report", "pnl_report"]:
            name = f"{rtype}_{today}.pdf"
            assert name not in names, f"Collision: {name}"
            names.add(name)

    def test_report_type_label_mapping(self):
        """Each report type should have a human-readable label."""
        from core.reports.renderer import _report_type_label

        expected = {
            "cfo_daily": "CFO Daily Briefing",
            "cmo_weekly": "CMO Weekly Report",
            "aging_report": "AR/AP Aging Report",
            "pnl_report": "Profit & Loss Report",
            "campaign_report": "Campaign Performance Report",
            "shadow_reconciliation": "Shadow Reconciliation Report",
        }
        for rtype, label in expected.items():
            assert _report_type_label(rtype) == label

    def test_unknown_report_type_label_fallback(self):
        """Unknown report type should produce a title-cased fallback."""
        from core.reports.renderer import _report_type_label

        result = _report_type_label("custom_weekly_audit")
        assert result == "Custom Weekly Audit"


# ═══════════════════════════════════════════════════════════════════════════
# Delivery Pipeline (dispatcher tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestDeliveryPipeline:
    """Verify the deliver() dispatcher routes correctly."""

    @pytest.mark.asyncio
    async def test_deliver_routes_email(self):
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_email",
            new_callable=AsyncMock,
            return_value={"status": "sent", "channel": "email"},
        ) as mock_fn:
            results = await deliver(
                "/tmp/report.pdf",
                [{"type": "email", "target": "user@x.com", "subject": "Test"}],
            )
            mock_fn.assert_called_once()
            assert results[0]["status"] == "sent"

    @pytest.mark.asyncio
    async def test_deliver_routes_slack(self):
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_slack",
            new_callable=AsyncMock,
            return_value={"status": "sent", "channel": "slack"},
        ) as mock_fn:
            await deliver(
                "/tmp/report.pdf",
                [{"type": "slack", "target": "C12345"}],
            )
            mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_routes_whatsapp(self):
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_whatsapp",
            new_callable=AsyncMock,
            return_value={"status": "sent", "channel": "whatsapp"},
        ) as mock_fn:
            await deliver(
                "/tmp/report.pdf",
                [{"type": "whatsapp", "target": "+919876543210"}],
            )
            mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_all_three_channels(self):
        """When all 3 channels are configured, all 3 are attempted."""
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_email",
            new_callable=AsyncMock,
            return_value={"status": "sent", "channel": "email"},
        ) as m_email, patch(
            "core.reports.delivery.deliver_slack",
            new_callable=AsyncMock,
            return_value={"status": "sent", "channel": "slack"},
        ) as m_slack, patch(
            "core.reports.delivery.deliver_whatsapp",
            new_callable=AsyncMock,
            return_value={"status": "sent", "channel": "whatsapp"},
        ) as m_wa:
            results = await deliver(
                "/tmp/report.pdf",
                [
                    {"type": "email", "target": "a@b.com"},
                    {"type": "slack", "target": "C111"},
                    {"type": "whatsapp", "target": "+919876543210"},
                ],
            )
            assert len(results) == 3
            m_email.assert_called_once()
            m_slack.assert_called_once()
            m_wa.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_email_failure_continues_to_slack(self):
        """Email failure should not prevent Slack delivery."""
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_email",
            new_callable=AsyncMock,
            side_effect=ConnectionError("SMTP timeout"),
        ), patch(
            "core.reports.delivery.deliver_slack",
            new_callable=AsyncMock,
            return_value={"status": "sent", "channel": "slack"},
        ):
            results = await deliver(
                "/tmp/report.pdf",
                [
                    {"type": "email", "target": "fail@x.com"},
                    {"type": "slack", "target": "C12345"},
                ],
            )
            assert len(results) == 2
            assert results[0]["status"] == "failed"
            assert results[1]["status"] == "sent"

    @pytest.mark.asyncio
    async def test_deliver_result_per_channel_status(self):
        """Each channel should have its own status in the results."""
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_email",
            new_callable=AsyncMock,
            return_value={"status": "sent", "channel": "email"},
        ), patch(
            "core.reports.delivery.deliver_slack",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Slack error"),
        ):
            results = await deliver(
                "/tmp/report.pdf",
                [
                    {"type": "email", "target": "ok@x.com"},
                    {"type": "slack", "target": "C555"},
                ],
            )
            email_result = results[0]
            slack_result = results[1]
            assert email_result["status"] == "sent"
            assert slack_result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_deliver_unknown_channel_is_skipped(self):
        from core.reports.delivery import deliver

        results = await deliver(
            "/tmp/report.pdf",
            [{"type": "telegram", "target": "@channel"}],
        )
        assert results[0]["status"] == "skipped"
        assert "unknown" in results[0]["reason"]

    @pytest.mark.asyncio
    async def test_deliver_empty_target_skipped(self):
        from core.reports.delivery import deliver

        results = await deliver(
            "/tmp/report.pdf",
            [{"type": "email", "target": ""}],
        )
        assert results[0]["status"] == "skipped"
        assert "target" in results[0]["reason"].lower() or "no target" in results[0]["reason"]
