"""Regression tests for the generic RPA framework + RBI scraper +
quality gate (feat/rpa-framework-rbi)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Registry: every script with SCRIPT_META + run() is discoverable
# ---------------------------------------------------------------------------


def test_registry_discovers_rbi_script() -> None:
    import sys

    sys.path.insert(0, str(REPO))
    from rpa.scripts._registry import discover_scripts

    scripts = discover_scripts()
    assert "rbi_org_scraper" in scripts, (
        "RBI scraper must be discovered by the generic registry "
        "without requiring a hardcoded entry in api/v1/rpa.py"
    )
    meta = scripts["rbi_org_scraper"]
    assert meta["target_quality"] == 4.8
    assert meta["produces_chunks"] is True
    assert meta["http_only"] is True


def test_registry_skips_files_without_meta() -> None:
    """A stray .py file without SCRIPT_META must be silently skipped
    so the registry doesn't blow up on utility modules."""
    import sys

    sys.path.insert(0, str(REPO))
    from rpa.scripts._registry import discover_scripts

    scripts = discover_scripts()
    # _registry.py itself has no SCRIPT_META — it must NOT appear
    assert "_registry" not in scripts


def test_registry_backfills_params_schema_from_required() -> None:
    """Older scripts that only declared ``required_params`` must still
    surface a ``params_schema`` to the UI."""
    import sys

    sys.path.insert(0, str(REPO))
    from rpa.scripts._registry import discover_scripts

    meta = discover_scripts()["epfo_ecr_download"]
    assert "params_schema" in meta
    # epfo declared required_params=["establishment_id", "wage_month", "wage_year"]
    for key in ("establishment_id", "wage_month", "wage_year"):
        assert key in meta["params_schema"]


# ---------------------------------------------------------------------------
# Quality gate: rubric behaves correctly and enforces the 4.8 target
# ---------------------------------------------------------------------------


def test_quality_gate_rejects_boilerplate() -> None:
    import sys

    sys.path.insert(0, str(REPO))
    from core.rpa.quality import score_chunk

    boilerplate = "Home › Press Release. " + ("x" * 300) + "."
    q = score_chunk(boilerplate)
    # Even though the chunk has the right length and ends with ".",
    # the boilerplate prefix must flag it.
    assert q["dimensions"]["non_boilerplate"] == 0.0
    assert not q["passes"]


def test_quality_gate_accepts_clean_chunk() -> None:
    import sys

    sys.path.insert(0, str(REPO))
    from core.rpa.quality import score_chunk

    clean = (
        "The Reserve Bank of India announced a 25 basis point hike in "
        "the repo rate effective 2026-04-23. Deposit rates across major "
        "banks have risen by 15 basis points in response. The Monetary "
        "Policy Committee cited persistent food inflation as the primary "
        "driver. Markets had largely priced in the hike."
    )
    q = score_chunk(clean)
    assert q["score"] >= 4.8
    assert q["passes"] is True


def test_filter_chunks_partitions_by_threshold() -> None:
    import sys

    sys.path.insert(0, str(REPO))
    from core.rpa.quality import filter_chunks

    good = (
        "The Reserve Bank of India announced new rules on 2026-04-23. "
        "Banks must now disclose unclaimed deposits within 30 days. "
        "The measure is aimed at reducing dormant-account fraud and "
        "improving data quality across the sector. Large lenders have "
        "already begun publishing quarterly reports with this metric."
    )
    short = "Too short."
    flagged_missing_punct = (
        "RBI said on 2026-04-23 that banks must publish quarterly "
        "unclaimed-deposits data. This is a meaningful, informative "
        "chunk about public regulatory actions with Reserve Bank "
        "context "
    )
    published, flagged, rejected = filter_chunks(
        [{"content": good}, {"content": short}, {"content": flagged_missing_punct}]
    )
    assert len(published) == 1
    # Short chunk fails length + no terminal punct + informative → reject
    assert len(rejected) >= 1


# ---------------------------------------------------------------------------
# RBI script: contract shape is correct
# ---------------------------------------------------------------------------


def test_rbi_script_meta_shape() -> None:
    src = _read("rpa/scripts/rbi_org_scraper.py")
    assert "SCRIPT_META" in src
    assert "async def run" in src
    assert "rbi.org.in" in src.lower()
    # Polite delay + identifying User-Agent are non-negotiable
    assert "_POLITE_DELAY_S" in src
    assert "agenticorg-rpa" in src


# ---------------------------------------------------------------------------
# Scheduling API: create → run-now wiring uses Celery
# ---------------------------------------------------------------------------


def test_rpa_schedules_api_registered() -> None:
    src = _read("api/main.py")
    assert "rpa_schedules" in src, (
        "api/main.py must import + mount the new rpa_schedules router"
    )
    assert "rpa_schedules.router" in src


def test_rpa_task_queue_wired_in_celery() -> None:
    src = _read("core/tasks/celery_app.py")
    assert "core.tasks.rpa_tasks" in src, (
        "core/tasks/celery_app.py must route rpa_tasks.* to the rpa queue"
    )
    assert "dispatch_due_rpa_schedules" in src, (
        "beat_schedule must include the RPA dispatcher sweeper"
    )


def test_rpa_schedule_model_has_quality_column() -> None:
    import sys

    sys.path.insert(0, str(REPO))
    from core.models.rpa_schedule import RPASchedule

    columns = {c.name for c in RPASchedule.__table__.columns}
    for required in (
        "script_key",
        "cron_expression",
        "enabled",
        "params",
        "config",
        "last_quality_avg",
        "last_run_chunks_published",
        "last_run_chunks_rejected",
    ):
        assert required in columns, f"RPASchedule missing {required!r}"
