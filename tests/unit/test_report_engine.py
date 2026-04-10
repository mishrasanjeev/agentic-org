# ruff: noqa: S108 — test files use /tmp paths intentionally
"""Test report generator, PDF/Excel renderer, and delivery pipeline.

Covers:
- ReportGenerator: generate() returns ReportOutput for each report type
- PDF renderer: render_pdf() creates a valid PDF file
- Excel renderer: render_excel() creates a valid .xlsx file
- Delivery dispatcher: deliver() dispatches to correct channel
- Celery task registration
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Report Generator
# ═══════════════════════════════════════════════════════════════════════════


class TestReportGenerator:
    """Verify ReportGenerator.generate() returns ReportOutput for each type."""

    _REPORT_TYPES = [
        "cfo_daily",
        "cmo_weekly",
        "aging_report",
        "pnl_report",
        "campaign_report",
        "shadow_reconciliation",
    ]

    def _make_generator(self):
        from core.reports.generator import ReportGenerator
        return ReportGenerator()

    @pytest.mark.parametrize("report_type", _REPORT_TYPES)
    def test_generate_returns_report_output(self, report_type):
        from core.reports.generator import ReportOutput
        gen = self._make_generator()
        output = gen.generate(report_type=report_type, params={})
        assert isinstance(output, ReportOutput)

    @pytest.mark.parametrize("report_type", _REPORT_TYPES)
    def test_generate_has_content_html(self, report_type):
        gen = self._make_generator()
        output = gen.generate(report_type=report_type, params={})
        assert isinstance(output.content_html, str)
        assert len(output.content_html) > 100  # Non-trivial HTML
        assert "<html" in output.content_html.lower()

    @pytest.mark.parametrize("report_type", _REPORT_TYPES)
    def test_generate_has_content_data(self, report_type):
        gen = self._make_generator()
        output = gen.generate(report_type=report_type, params={})
        assert isinstance(output.content_data, dict)
        assert len(output.content_data) > 0

    @pytest.mark.parametrize("report_type", _REPORT_TYPES)
    def test_generate_has_correct_type(self, report_type):
        gen = self._make_generator()
        output = gen.generate(report_type=report_type, params={})
        assert output.report_type == report_type

    @pytest.mark.parametrize("report_type", _REPORT_TYPES)
    def test_generate_has_generated_at(self, report_type):
        gen = self._make_generator()
        output = gen.generate(report_type=report_type, params={})
        assert isinstance(output.generated_at, str)
        assert len(output.generated_at) > 0

    def test_generate_unknown_type_raises(self):
        gen = self._make_generator()
        with pytest.raises(ValueError, match="Unknown report type"):
            gen.generate(report_type="nonexistent_report", params={})

    def test_cfo_daily_has_expected_kpi_data(self):
        gen = self._make_generator()
        output = gen.generate(report_type="cfo_daily", params={})
        data = output.content_data
        assert "agent_count" in data
        assert "total_tasks_30d" in data
        assert "success_rate" in data

    def test_cmo_weekly_has_expected_kpi_data(self):
        gen = self._make_generator()
        output = gen.generate(report_type="cmo_weekly", params={})
        data = output.content_data
        assert "agent_count" in data
        assert "total_tasks_30d" in data
        assert "success_rate" in data

    def test_html_contains_agenticorg_branding(self):
        gen = self._make_generator()
        output = gen.generate(report_type="cfo_daily", params={})
        assert "AgenticOrg" in output.content_html


# ═══════════════════════════════════════════════════════════════════════════
# PDF Renderer
# ═══════════════════════════════════════════════════════════════════════════


class TestPDFRenderer:
    """Verify render_pdf() creates a valid PDF file."""

    def _generate_report(self, report_type="cfo_daily"):
        from core.reports.generator import ReportGenerator
        return ReportGenerator().generate(report_type=report_type, params={})

    def test_render_pdf_creates_file(self):
        from core.reports.renderer import render_pdf
        report = self._generate_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_report.pdf")
            result = render_pdf(report, path)
            assert result == path
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0

    def test_render_pdf_file_starts_with_pdf_header(self):
        from core.reports.renderer import render_pdf
        report = self._generate_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_report.pdf")
            render_pdf(report, path)
            with open(path, "rb") as f:
                header = f.read(5)
            assert header == b"%PDF-"

    def test_render_pdf_for_cmo_report(self):
        from core.reports.renderer import render_pdf
        report = self._generate_report("cmo_weekly")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "cmo_report.pdf")
            render_pdf(report, path)
            assert os.path.getsize(path) > 0

    def test_render_pdf_for_aging_report(self):
        from core.reports.renderer import render_pdf
        report = self._generate_report("aging_report")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "aging.pdf")
            render_pdf(report, path)
            assert os.path.getsize(path) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Excel Renderer
# ═══════════════════════════════════════════════════════════════════════════


class TestExcelRenderer:
    """Verify render_excel() creates a valid .xlsx file."""

    def _generate_report(self, report_type="cfo_daily"):
        from core.reports.generator import ReportGenerator
        return ReportGenerator().generate(report_type=report_type, params={})

    def test_render_excel_creates_file(self):
        from core.reports.renderer import render_excel
        report = self._generate_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_report.xlsx")
            result = render_excel(report, path)
            assert result == path
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0

    def test_render_excel_is_valid_xlsx(self):
        from core.reports.renderer import render_excel
        report = self._generate_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_report.xlsx")
            render_excel(report, path)
            # XLSX files are ZIP archives — check magic bytes PK
            with open(path, "rb") as f:
                header = f.read(2)
            assert header == b"PK"

    def test_render_excel_for_pnl_report(self):
        from core.reports.renderer import render_excel
        report = self._generate_report("pnl_report")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "pnl.xlsx")
            render_excel(report, path)
            assert os.path.getsize(path) > 0

    def test_render_excel_for_campaign_report(self):
        from core.reports.renderer import render_excel
        report = self._generate_report("campaign_report")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "campaign.xlsx")
            render_excel(report, path)
            assert os.path.getsize(path) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Delivery Pipeline
# ═══════════════════════════════════════════════════════════════════════════


class TestDelivery:
    """Verify deliver() dispatches to correct channel based on config."""

    @pytest.mark.asyncio
    async def test_deliver_dispatches_email(self):
        from core.reports.delivery import deliver
        with patch("core.reports.delivery.deliver_email", new_callable=AsyncMock) as mock_email:
            mock_email.return_value = {"status": "sent", "channel": "email"}
            results = await deliver(
                "/tmp/fake_report.pdf",
                [{"type": "email", "target": "cfo@example.com", "subject": "Test"}],
            )
            assert len(results) == 1
            mock_email.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_dispatches_slack(self):
        from core.reports.delivery import deliver
        with patch("core.reports.delivery.deliver_slack", new_callable=AsyncMock) as mock_slack:
            mock_slack.return_value = {"status": "sent", "channel": "slack"}
            results = await deliver(
                "/tmp/fake_report.pdf",
                [{"type": "slack", "target": "C12345"}],
            )
            assert len(results) == 1
            mock_slack.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_dispatches_whatsapp(self):
        from core.reports.delivery import deliver
        with patch("core.reports.delivery.deliver_whatsapp", new_callable=AsyncMock) as mock_wa:
            mock_wa.return_value = {"status": "sent", "channel": "whatsapp"}
            results = await deliver(
                "/tmp/fake_report.pdf",
                [{"type": "whatsapp", "target": "+919876543210"}],
            )
            assert len(results) == 1
            mock_wa.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_unknown_channel_skips(self):
        from core.reports.delivery import deliver
        results = await deliver(
            "/tmp/fake_report.pdf",
            [{"type": "fax", "target": "12345"}],
        )
        assert len(results) == 1
        assert results[0]["status"] == "skipped"
        assert results[0]["reason"] == "unknown channel type"

    @pytest.mark.asyncio
    async def test_deliver_no_target_skips(self):
        from core.reports.delivery import deliver
        results = await deliver(
            "/tmp/fake_report.pdf",
            [{"type": "email", "target": ""}],
        )
        assert len(results) == 1
        assert results[0]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_deliver_multiple_channels(self):
        from core.reports.delivery import deliver
        with (
            patch("core.reports.delivery.deliver_email", new_callable=AsyncMock) as mock_email,
            patch("core.reports.delivery.deliver_slack", new_callable=AsyncMock) as mock_slack,
        ):
            mock_email.return_value = {"status": "sent", "channel": "email"}
            mock_slack.return_value = {"status": "sent", "channel": "slack"}
            results = await deliver(
                "/tmp/fake_report.pdf",
                [
                    {"type": "email", "target": "cfo@example.com"},
                    {"type": "slack", "target": "C12345"},
                ],
            )
            assert len(results) == 2
            mock_email.assert_called_once()
            mock_slack.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# Celery Task Registration
# ═══════════════════════════════════════════════════════════════════════════


class TestCeleryTasks:
    """Verify task registration in Celery app."""

    def test_celery_app_exists(self):
        from core.tasks.celery_app import app
        assert app.main == "agenticorg"

    def test_generate_scheduled_reports_task_registered(self):
        # Force task discovery
        from core.tasks import report_tasks  # noqa: F401
        from core.tasks.celery_app import app
        assert "core.tasks.report_tasks.generate_scheduled_reports" in app.tasks

    def test_generate_report_task_registered(self):
        from core.tasks import report_tasks  # noqa: F401
        from core.tasks.celery_app import app
        assert "core.tasks.report_tasks.generate_report" in app.tasks

    def test_deliver_report_task_registered(self):
        from core.tasks import report_tasks  # noqa: F401
        from core.tasks.celery_app import app
        assert "core.tasks.report_tasks.deliver_report" in app.tasks

    def test_cleanup_old_reports_task_registered(self):
        from core.tasks import report_tasks  # noqa: F401
        from core.tasks.celery_app import app
        assert "core.tasks.report_tasks.cleanup_old_reports" in app.tasks

    def test_beat_schedule_has_scheduled_reports(self):
        from core.tasks.celery_app import app
        assert "generate-scheduled-reports" in app.conf.beat_schedule

    def test_beat_schedule_has_cleanup(self):
        from core.tasks.celery_app import app
        assert "cleanup-old-reports" in app.conf.beat_schedule
