"""SLA telemetry persistence pins (Phase 5 of the 2026-04-30 gap analysis).

Pins the contract Phase 5 introduces over Phase 1's honest stub:

- When ``health_check_history`` has rows in the requested window, the
  endpoint returns ``data_source: "persisted"`` + real items + a
  computed ``uptime_pct`` (uptime endpoint).
- When the table is empty (or its query fails — fresh deploy before the
  first snapshot lands, or dev env without the Celery beat), the
  endpoint falls back to ``data_source: "live_snapshot"`` + an honest
  ``note`` so the UI's existing data-source-aware banner still renders
  the right context.
- Migration ``v498_health_check_history`` is registered.
- The Celery beat schedule includes the periodic recorder.

These pins are intentionally hermetic — no live DB. The endpoint queries
``health_check_history`` via ``async_session_factory``; we monkeypatch
the ``_fetch_history`` helper to control whether "history" is present.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

# ─────────────────────────────────────────────────────────────────
# Migration + beat schedule registration pins
# ─────────────────────────────────────────────────────────────────


def test_health_check_history_migration_exists() -> None:
    """The Alembic migration that creates the table must be present
    and have a revision id ≤ 32 chars (alembic_version.version_num
    is VARCHAR(32))."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    migration = repo / "migrations" / "versions" / "v4_9_8_health_check_history.py"
    assert migration.exists(), "v4_9_8_health_check_history.py is missing"

    src = migration.read_text(encoding="utf-8")
    assert 'revision = "v498_health_check_history"' in src
    # Revision id length budget — VARCHAR(32) cap.
    assert len("v498_health_check_history") <= 32

    # Idempotent CREATE — must use IF NOT EXISTS so re-runs are safe.
    assert "CREATE TABLE IF NOT EXISTS health_check_history" in src
    assert "CREATE INDEX IF NOT EXISTS ix_health_check_history_recorded_at" in src

    # Reversible — downgrade must drop both.
    assert "DROP TABLE IF EXISTS health_check_history" in src
    assert "DROP INDEX IF EXISTS ix_health_check_history_recorded_at" in src


def test_celery_beat_schedule_registers_health_snapshot() -> None:
    """The periodic snapshot must be in the beat schedule, otherwise the
    table never gets populated and the endpoints stay in fallback mode
    forever."""
    from core.tasks.celery_app import app

    schedule = app.conf.beat_schedule
    assert "record-health-snapshot" in schedule
    entry = schedule["record-health-snapshot"]
    assert entry["task"] == "core.tasks.health_snapshot.record_health_snapshot"
    # Must be ≤ 15 minutes — the SLA chart shows 24h, so any longer
    # produces a sparse uptime sample (< 100 points).
    assert entry["schedule"] <= 900.0


# ─────────────────────────────────────────────────────────────────
# /health/checks behavior pins
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_checks_returns_persisted_when_history_has_rows(
    monkeypatch,
) -> None:
    """When the table has real rows, the endpoint returns
    ``data_source: 'persisted'`` and the rows verbatim."""
    from api.v1 import health as health_module

    fake_rows = [
        {
            "timestamp": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            "status": "healthy",
            "checks": {"db": "healthy", "redis": "healthy"},
            "version": "9.9.9",
            "commit": "abc123",
        },
        {
            "timestamp": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
            "status": "unhealthy",
            "checks": {"db": "healthy", "redis": "unhealthy: TimeoutError"},
            "version": "9.9.9",
            "commit": "abc123",
        },
    ]

    async def _fake_fetch(_hours: int):
        return fake_rows

    monkeypatch.setattr(health_module, "_fetch_history", _fake_fetch)

    result = await health_module.health_checks()

    assert result["data_source"] == "persisted"
    assert result["items"] == fake_rows
    assert result["window_hours"] == 24  # default
    # No fallback note when persisted history is present
    assert "note" not in result


@pytest.mark.asyncio
async def test_health_checks_falls_back_to_live_snapshot_when_history_empty(
    monkeypatch,
) -> None:
    """Empty table → ``data_source: 'live_snapshot'`` + an honest note,
    so the UI banner still renders the right explanation."""
    from api.v1 import health as health_module

    async def _empty_fetch(_hours: int):
        return []

    async def _fake_readiness():
        return {
            "status": "healthy",
            "version": "9.9.9",
            "commit": "abc123",
            "checks": {"db": "healthy", "redis": "healthy"},
        }

    monkeypatch.setattr(health_module, "_fetch_history", _empty_fetch)
    monkeypatch.setattr(health_module, "health_readiness", _fake_readiness)

    result = await health_module.health_checks()

    assert result["data_source"] == "live_snapshot"
    assert "note" in result and "No persisted snapshots" in result["note"]
    assert len(result["items"]) == 1
    assert result["items"][0]["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_checks_clamps_hours_to_safe_range(monkeypatch) -> None:
    """``hours`` must be clamped to [1, 168] so a malicious / bug call
    can't ask for an unbounded range."""
    from api.v1 import health as health_module

    captured: list[int] = []

    async def _fake_fetch(hours: int):
        captured.append(hours)
        return []

    async def _fake_readiness():
        return {"status": "healthy", "version": "x", "commit": "y", "checks": {}}

    monkeypatch.setattr(health_module, "_fetch_history", _fake_fetch)
    monkeypatch.setattr(health_module, "health_readiness", _fake_readiness)

    await health_module.health_checks(hours=0)
    await health_module.health_checks(hours=99999)
    assert captured == [1, 7 * 24]


# ─────────────────────────────────────────────────────────────────
# /health/uptime behavior pins
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_uptime_computes_pct_from_persisted_rows(monkeypatch) -> None:
    """Uptime % comes from the ratio of healthy rows in the window.

    UI renders this number directly; the math has to be deterministic.
    """
    from api.v1 import health as health_module

    # 3 healthy + 1 unhealthy → 75.0
    fake_rows = [
        {
            "timestamp": (datetime.now(UTC) - timedelta(minutes=i * 5)).isoformat(),
            "status": "healthy" if i != 2 else "unhealthy",
            "checks": {"db": "healthy", "redis": "healthy"},
            "version": "x",
            "commit": "y",
        }
        for i in range(4)
    ]

    async def _fake_fetch(_hours: int):
        return fake_rows

    async def _fake_readiness():
        return {"status": "healthy", "version": "x", "commit": "y", "checks": {}}

    monkeypatch.setattr(health_module, "_fetch_history", _fake_fetch)
    monkeypatch.setattr(health_module, "health_readiness", _fake_readiness)

    result = await health_module.health_uptime()

    assert result["data_source"] == "persisted"
    assert result["uptime_pct"] == 75.0
    assert result["samples"] == 4
    assert result["current_status"] == "healthy"
    assert len(result["items"]) == 4


@pytest.mark.asyncio
async def test_health_uptime_falls_back_with_null_pct_when_empty(monkeypatch) -> None:
    """No history → uptime_pct is null (NOT 0), samples is 0, with note."""
    from api.v1 import health as health_module

    async def _empty(_hours: int):
        return []

    async def _fake_readiness():
        return {"status": "unhealthy", "version": "x", "commit": "y", "checks": {}}

    monkeypatch.setattr(health_module, "_fetch_history", _empty)
    monkeypatch.setattr(health_module, "health_readiness", _fake_readiness)

    result = await health_module.health_uptime()

    assert result["data_source"] == "live_snapshot"
    assert result["uptime_pct"] is None  # not 0 — null is "unknown"
    assert result["samples"] == 0
    assert result["current_status"] == "unhealthy"
    assert "note" in result


# ─────────────────────────────────────────────────────────────────
# _fetch_history fail-safe — query failure must NOT 500 the endpoint
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_history_returns_empty_on_query_error(monkeypatch) -> None:
    """If the query raises (table missing, DB blip, RLS denial, ...) the
    helper must return [] so the endpoint falls back to live snapshot
    rather than 500-ing the entire SLA Monitor page."""
    from api.v1 import health as health_module

    # Force the session factory to raise inside the helper.
    class _BoomSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, *args, **kwargs):
            raise RuntimeError("simulated db failure")

    def _factory():
        return _BoomSession()

    monkeypatch.setattr(health_module, "async_session_factory", _factory)

    rows = await health_module._fetch_history(24)
    assert rows == []
