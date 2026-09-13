# ruff: noqa: S108 - test files use /tmp paths intentionally
"""Test report generator, PDF/Excel renderer, and delivery pipeline."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_ARTIFACT_DIR = Path.cwd() / "codex-pytest-artifacts"
_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def _artifact_path(name: str) -> Path:
    return _ARTIFACT_DIR / f"{uuid.uuid4().hex}-{name}"


class TestReportGenerator:
    """Verify ReportGenerator.generate() returns ReportOutput for each type."""

    _REPORT_TYPES = [
        "cfo_daily",
        "cmo_weekly",
        "aging_report",
        "pnl_report",
        "campaign_report",
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
        assert len(output.content_html) > 100
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

    def test_shadow_reconciliation_fails_closed_without_measured_evidence(self):
        from core.reports.generator import ReportEvidenceUnavailableError

        gen = self._make_generator()
        with pytest.raises(
            ReportEvidenceUnavailableError,
            match="no tenant-scoped measured evidence source",
        ):
            gen.generate(report_type="shadow_reconciliation", params={})

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


class TestPDFRenderer:
    """Verify render_pdf() creates a valid PDF file."""

    def _generate_report(self, report_type="cfo_daily"):
        from core.reports.generator import ReportGenerator

        return ReportGenerator().generate(report_type=report_type, params={})

    def test_render_pdf_creates_file(self):
        from core.reports.renderer import render_pdf

        report = self._generate_report()
        path = _artifact_path("test_report.pdf")
        result = render_pdf(report, str(path))
        assert result == str(path)
        assert path.exists()
        assert path.stat().st_size > 0

    def test_render_pdf_file_starts_with_pdf_header(self):
        from core.reports.renderer import render_pdf

        report = self._generate_report()
        path = _artifact_path("test_report.pdf")
        render_pdf(report, str(path))
        with open(path, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-"

    def test_render_pdf_for_cmo_report(self):
        from core.reports.renderer import render_pdf

        report = self._generate_report("cmo_weekly")
        path = _artifact_path("cmo_report.pdf")
        render_pdf(report, str(path))
        assert path.stat().st_size > 0

    def test_render_pdf_for_aging_report(self):
        from core.reports.renderer import render_pdf

        report = self._generate_report("aging_report")
        path = _artifact_path("aging.pdf")
        render_pdf(report, str(path))
        assert path.stat().st_size > 0


class TestExcelRenderer:
    """Verify render_excel() creates a valid .xlsx file."""

    def _generate_report(self, report_type="cfo_daily"):
        from core.reports.generator import ReportGenerator

        return ReportGenerator().generate(report_type=report_type, params={})

    def test_render_excel_creates_file(self):
        from core.reports.renderer import render_excel

        report = self._generate_report()
        path = _artifact_path("test_report.xlsx")
        result = render_excel(report, str(path))
        assert result == str(path)
        assert path.exists()
        assert path.stat().st_size > 0

    def test_render_excel_is_valid_xlsx(self):
        from core.reports.renderer import render_excel

        report = self._generate_report()
        path = _artifact_path("test_report.xlsx")
        render_excel(report, str(path))
        with open(path, "rb") as f:
            header = f.read(2)
        assert header == b"PK"

    def test_render_excel_for_pnl_report(self):
        from core.reports.renderer import render_excel

        report = self._generate_report("pnl_report")
        path = _artifact_path("pnl.xlsx")
        render_excel(report, str(path))
        assert path.stat().st_size > 0

    def test_render_excel_for_campaign_report(self):
        from core.reports.renderer import render_excel

        report = self._generate_report("campaign_report")
        path = _artifact_path("campaign.xlsx")
        render_excel(report, str(path))
        assert path.stat().st_size > 0


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


class TestCeleryTasks:
    """Verify task registration in Celery app."""

    def test_celery_app_exists(self):
        from core.tasks.celery_app import app

        assert app.main == "agenticorg"

    def test_generate_scheduled_reports_task_registered(self):
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


# ── Scheduled report poller reads report_schedules from the DB ───────────


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return _FakeTxn()

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self.rows)


class TestScheduledReportPoller:
    def _row(self, **overrides):
        from datetime import UTC, datetime, timedelta
        from types import SimpleNamespace

        base = {
            "id": uuid.uuid4(),
            "tenant_id": uuid.uuid4(),
            "company_id": uuid.uuid4(),
            "report_type": "cfo_daily",
            "cron_expression": "daily",
            "recipients": [{"type": "email", "target": "cfo@corp.in"}],
            "format": "pdf",
            "enabled": True,
            "last_run_at": None,
            "next_run_at": datetime.now(UTC) - timedelta(minutes=1),
            "config": {"params": {"x": 1}},
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    @pytest.mark.asyncio
    async def test_claim_due_schedules_uses_skip_locked_and_advances_next_run(self, monkeypatch):
        from datetime import UTC, datetime

        from core.tasks import report_tasks as rt

        row = self._row()
        session = _FakeSession([row])
        monkeypatch.setattr("core.database.async_session_factory", lambda: session)

        before = datetime.now(UTC)
        claimed = await rt._claim_due_schedules()

        from sqlalchemy.dialects import postgresql

        sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "report_schedules.enabled IS true" in sql
        assert "next_run_at" in sql
        assert row.last_run_at >= before
        assert row.next_run_at > before  # advanced in the same txn as the claim
        assert claimed == [
            {
                "report_type": "cfo_daily",
                "params": {"x": 1},
                "company_id": str(row.company_id),
                "tenant_id": str(row.tenant_id),
                "delivery_channels": [{"type": "email", "target": "cfo@corp.in"}],
                "format": "pdf",
                "schedule_id": str(row.id),
            }
        ]

    def test_generate_scheduled_reports_fans_out_db_rows(self, monkeypatch):
        from core.tasks import report_tasks as rt

        cfg = {"report_type": "cfo_daily", "schedule_id": "s1", "tenant_id": "t"}

        async def _claim():
            return [cfg]

        monkeypatch.setattr(rt, "_claim_due_schedules", _claim)
        # run_async is imported lazily inside the task; provide a loop-local
        # stand-in so the test does not depend on the worker runner module.
        import asyncio
        import sys
        import types

        fake_runner = types.ModuleType("core.tasks.async_runner")
        fake_runner.run_async = lambda coro: asyncio.new_event_loop().run_until_complete(coro)
        monkeypatch.setitem(sys.modules, "core.tasks.async_runner", fake_runner)
        sent = []
        monkeypatch.setattr(rt.generate_report, "delay", lambda c: sent.append(c))

        result = rt.generate_scheduled_reports.run()

        assert sent == [cfg]
        assert result == {"fired": ["s1"], "errors": [], "checked": 1}

    def test_next_run_after_keywords_and_cron(self):
        from datetime import UTC, datetime, timedelta

        from core.tasks import report_tasks as rt

        now = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
        assert rt._next_run_after("hourly", now) == now + timedelta(hours=1)
        assert rt._next_run_after("bogus expr", now) == now + timedelta(days=1)
        nxt = rt._next_run_after("0 6 * * *", now)
        assert nxt > now
        if pytest.importorskip("croniter", reason="croniter optional"):
            assert nxt.hour == 6


class TestDemoContentNeverDelivered:
    def test_is_demo_or_fallback(self):
        from core.tasks.report_tasks import _is_demo_or_fallback as f

        assert f({"demo": True}) is True
        assert f({"source": "report_generator_fallback"}) is True
        assert f({"demo": False, "source": "computed", "agent_count": 3}) is False

    def test_generate_report_blocks_delivery_for_fallback_content(self, monkeypatch, tmp_path):
        from core.reports.generator import ReportGenerator
        from core.tasks import report_tasks as rt

        monkeypatch.setattr(
            ReportGenerator,
            "_fetch_cfo_kpis",
            staticmethod(lambda company_id, tenant_id="default": {"demo": True, "source": "report_generator_fallback"}),
        )
        delivered = []
        monkeypatch.setattr(rt.deliver_report, "delay", lambda **kw: delivered.append(kw))
        monkeypatch.setattr(rt, "_REPORTS_DIR", tmp_path)

        result = rt.generate_report.run(
            {
                "report_type": "cfo_daily",
                "tenant_id": str(uuid.uuid4()),
                "company_id": "default",
                "delivery_channels": [{"type": "email", "target": "cfo@corp.in"}],
                "format": "pdf",
            }
        )

        assert result["status"] == "failed"
        assert result["reason"] == "report_content_is_demo_or_fallback"
        assert result["paths"] == []
        assert delivered == []

    def test_fetch_kpis_uses_in_process_builder_for_tenant(self, monkeypatch):
        from core.reports.generator import ReportGenerator

        tenant = str(uuid.uuid4())
        seen = {}

        async def _fake_builder(tenant_id, role, company_id):
            seen.update(tenant_id=tenant_id, role=role, company_id=company_id)
            return {"agent_count": 4, "total_tasks_30d": 12, "success_rate": 90.0, "demo": False, "source": "computed"}

        monkeypatch.setattr("api.v1.kpis._build_kpi_response", _fake_builder)
        import asyncio
        import sys
        import types

        fake_runner = types.ModuleType("core.tasks.async_runner")
        fake_runner.run_async = lambda coro: asyncio.new_event_loop().run_until_complete(coro)
        monkeypatch.setitem(sys.modules, "core.tasks.async_runner", fake_runner)
        data = ReportGenerator._fetch_cfo_kpis("comp-1", tenant)

        assert seen == {"tenant_id": tenant, "role": "cfo", "company_id": "comp-1"}
        assert data["agent_count"] == 4 and data["demo"] is False

    def test_fetch_kpis_without_tenant_scope_is_fallback(self):
        from core.reports.generator import ReportGenerator

        data = ReportGenerator._fetch_cmo_kpis("default")
        assert data["demo"] is True and data["source"] == "report_generator_fallback"
