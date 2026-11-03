# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights background exports (part B): what a job may ask for, the caller snapshot, PII masking, storage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from core.insights import export_jobs, pii, storage
from core.ownership import Caller


def _caller(role: str, *, admin: bool = False, domains: list[str] | None = None) -> Caller:
    return Caller(user_id=uuid.uuid4(), role=role, domains=domains, is_admin=admin, is_machine=False)


ADMIN = _caller("admin", admin=True)
CFO = _caller("cfo", domains=["finance"])
AUDITOR = _caller("auditor")
MERCHANT = _caller("merchant", domains=["commerce"])


def _refused(kind: str, fmt: str, params: dict, caller: Caller) -> export_jobs.ExportJobError:
    with pytest.raises(export_jobs.ExportJobError) as exc:
        export_jobs.validate(kind, fmt, params, caller)
    return exc.value


def test_a_metric_job_keeps_only_what_the_caller_may_read() -> None:
    clean = export_jobs.validate(
        "metric",
        "xlsx",
        {"key": "cost.spend", "from": "2026-01-01", "to": "2026-10-31", "compare": "previous", "evil": "x"},
        CFO,
    )
    assert clean == {"from": "2026-01-01", "to": "2026-10-31", "key": "cost.spend", "compare": "previous"}
    assert _refused("metric", "csv", {"key": "cost.spend"}, MERCHANT).status == 403
    assert _refused("metric", "csv", {"key": "no.such"}, ADMIN).error == "unknown_metric"
    assert _refused("metric", "pdf", {"key": "cost.spend"}, ADMIN).error == "invalid_format"
    assert _refused("metric", "csv", {"key": "cost.spend", "group_by": "tenant_id"}, ADMIN).error == (
        "group_by_not_allowed"
    )
    assert _refused("metric", "csv", {"key": "cost.spend", "from": "yesterday"}, ADMIN).error == "invalid_period"
    assert _refused("metric", "csv", {"key": "cost.spend", "company_id": "acme"}, ADMIN).error == "invalid_company"


def test_an_explorer_job_checks_the_explorer_and_its_filters() -> None:
    clean = export_jobs.validate(
        "explorer", "csv", {"key": "runs", "query": {"filter[status]": "failed", "sort": "started_at"}}, AUDITOR
    )
    assert clean["query"] == {"filter[status]": "failed", "sort": "started_at"}
    assert _refused("explorer", "csv", {"key": "audit"}, CFO).status == 403
    assert _refused("explorer", "csv", {"key": "runs", "query": {"filter[nope]": "x"}}, ADMIN).status == 422
    assert _refused("explorer", "csv", {"key": "nothing"}, ADMIN).status == 404


def test_a_run_bundle_job_and_the_kinds_not_yet_available() -> None:
    clean = export_jobs.validate(
        "run_bundle", "json", {"fact_id": str(uuid.uuid4()), "at": "2026-11-01T03:00:00+00:00"}, CFO
    )
    assert clean["at"] == "2026-11-01T03:00:00+00:00"
    assert _refused("run_bundle", "json", {"fact_id": "x", "at": "2026-11-01T03:00:00+00:00"}, CFO).error == (
        "invalid_run"
    )
    assert _refused("run_bundle", "json", {"fact_id": str(uuid.uuid4()), "at": "2026-11-01"}, CFO).error == (
        "invalid_at"
    )
    assert _refused("run_bundle", "json", {"fact_id": str(uuid.uuid4())}, MERCHANT).status == 403
    assert _refused("teapot", "csv", {}, ADMIN).error == "unknown_kind"


def test_the_caller_snapshot_round_trips_and_is_never_taken_from_the_request() -> None:
    snap = export_jobs.caller_snapshot(CFO)
    assert snap == {"user_id": str(CFO.user_id), "role": "cfo", "domains": ["finance"], "is_admin": False}
    again = export_jobs.caller_from_snapshot(snap)
    assert (again.user_id, again.role, again.domains, again.is_admin, again.is_machine) == (
        CFO.user_id,
        "cfo",
        ["finance"],
        False,
        False,
    )
    clean = export_jobs.validate("metric", "csv", {"key": "cost.spend", "caller": {"is_admin": True}}, CFO)
    assert "caller" not in clean


class _Session:
    def __init__(self, config) -> None:
        self.config = config
        self.added: list = []

    async def get(self, _model, _key):
        return self.config

    def add(self, obj) -> None:
        self.added.append(obj)


async def test_pii_is_masked_by_the_tenant_setting_unless_an_admin_or_auditor_asks() -> None:
    rows = [{"agent": "Desk 9876543210", "email": "pay@vendor.example", "runs": 12345678}]
    tenant = uuid.uuid4()
    masked, did = await pii.apply(_Session(None), tenant, rows, include_pii=False, role="cfo", is_admin=False)
    assert did and "9876543210" not in str(masked) and "pay@vendor.example" not in str(masked)
    assert masked[0]["runs"] == 12345678  # numbers are not text: never masked
    # Ids and digests stay whole (a UUID may start with eight digits, which reads as an account number).
    ids = [{"id": "72950245-7edf-433c-8c81-badd186a2c77", "sha": "1234567890abcdef" * 4, "note": "acct 123456789012"}]
    kept, _did = await pii.apply(_Session(None), tenant, ids, include_pii=False, role="cfo", is_admin=False)
    assert kept[0]["id"] == ids[0]["id"] and kept[0]["sha"] == ids[0]["sha"]
    assert "123456789012" not in kept[0]["note"]
    off = type("Config", (), {"pii_masking": False})()
    assert await pii.apply(_Session(off), tenant, rows, include_pii=False, role="cfo", is_admin=False) == (rows, False)
    assert await pii.apply(_Session(None), tenant, rows, include_pii=True, role="auditor", is_admin=False) == (
        rows,
        False,
    )
    with pytest.raises(pii.IncludePiiForbiddenError):
        await pii.apply(_Session(None), tenant, rows, include_pii=True, role="cfo", is_admin=False)


async def test_a_job_is_queued_for_seven_days_and_raw_values_need_the_role() -> None:
    session = _Session(None)
    now = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)
    job = await export_jobs.create(
        session,
        uuid.uuid4(),
        CFO,
        kind="metric",
        fmt="csv",
        params={"key": "cost.spend"},
        include_pii=False,
        now=now,
    )
    assert job.status == "queued" and job.expires_at - now == export_jobs.RETENTION
    assert job.params["caller"]["role"] == "cfo" and session.added == [job]
    out = export_jobs.serialize(job)
    assert out["download"] is None and out["key"] == "cost.spend"
    with pytest.raises(export_jobs.ExportJobError) as refused:
        await export_jobs.create(
            session,
            uuid.uuid4(),
            CFO,
            kind="metric",
            fmt="csv",
            params={"key": "cost.spend"},
            include_pii=True,
            now=now,
        )
    assert refused.value.error == "include_pii_forbidden" and refused.value.status == 403


def test_storage_round_trip_in_the_fake_bucket(monkeypatch) -> None:
    from core.config import settings
    from core.test_doubles import fake_storage

    monkeypatch.setenv("AGENTICORG_TEST_FAKE_STORAGE", "1")
    monkeypatch.setattr(settings, "insights_export_bucket", "")
    assert not storage.configured()
    with pytest.raises(storage.ExportStorageError):
        storage.put("k", b"x", "text/csv")
    monkeypatch.setattr(settings, "insights_export_bucket", "exports-test")
    fake_storage.reset()
    key = export_jobs.storage_key(uuid.uuid4(), uuid.uuid4(), "a.csv")
    storage.put(key, b"a,b\n1,2\n", "text/csv")
    assert storage.get(key) == b"a,b\n1,2\n"
    storage.delete(key)
    with pytest.raises(storage.ExportStorageError):
        storage.get(key)
    storage.delete(key)  # already gone: no error


def test_the_migration_follows_v6z109_and_matches_the_model() -> None:
    import importlib.util
    import re
    from pathlib import Path

    from core.models.insights import EXPORT_FORMATS, EXPORT_KINDS, EXPORT_STATUSES

    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z110_insights_exports.py"
    spec = importlib.util.spec_from_file_location("_v6_z110_insights_exports_unit", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "v6z110_insights_exports" and len(migration.revision) <= 32
    assert migration.down_revision == "v6z109_insights_value_alerts"
    children = [
        p.name
        for p in path.parent.glob("*.py")
        if re.search(r'^down_revision = "v6z109_insights_value_alerts"$', p.read_text(encoding="utf-8"), re.M)
    ]
    assert children == [path.name]
    source = path.read_text(encoding="utf-8")
    v6z110_kinds = tuple(k for k in EXPORT_KINDS if k != "partner_pack")
    for values in (v6z110_kinds, EXPORT_STATUSES, EXPORT_FORMATS):
        assert ",".join(f"'{v}'" for v in values) in source
    # v6z113 widens the kind check to the partner client pack.
    later = (path.parent / "v6_z113_insights_saved_views.py").read_text(encoding="utf-8")
    assert '_KINDS = _KINDS_V6Z110 + ",\'partner_pack\'"' in later
    assert f'_KINDS_V6Z110 = "{",".join(repr(k) for k in v6z110_kinds)}"' in later
    assert set(export_jobs.FORMATS_BY_KIND) == set(EXPORT_KINDS)
    assert "FORCE ROW LEVEL SECURITY" in source


def test_storage_uses_the_configured_bucket_when_no_fake_is_active(monkeypatch) -> None:
    """The real path: one bucket from settings, objects written, read and deleted through the client."""
    from google.cloud import storage as gcs

    from core.config import settings

    objects: dict[str, tuple[bytes, str]] = {}
    buckets: list[str] = []

    class Blob:
        def __init__(self, key: str) -> None:
            self.key = key

        def upload_from_string(self, data: bytes, content_type: str) -> None:
            objects[self.key] = (data, content_type)

        def exists(self) -> bool:
            return self.key in objects

        def download_as_bytes(self) -> bytes:
            return objects[self.key][0]

        def delete(self) -> None:
            del objects[self.key]

    class Client:
        def bucket(self, name: str):
            buckets.append(name)
            return type("Bucket", (), {"blob": staticmethod(Blob)})()

    monkeypatch.delenv("AGENTICORG_TEST_FAKE_STORAGE", raising=False)
    monkeypatch.setattr(gcs, "Client", Client)
    monkeypatch.setattr(settings, "insights_export_bucket", "")
    with pytest.raises(storage.ExportStorageError, match="unconfigured"):
        storage.get("k")
    with pytest.raises(storage.ExportStorageError, match="unconfigured"):
        storage._bucket()
    storage.delete("k")  # nothing configured: nothing to delete

    monkeypatch.setattr(settings, "insights_export_bucket", " exports-real ")
    assert storage.configured() and storage.bucket_name() == "exports-real"
    storage.put("t/a.csv", b"x,y\n", "text/csv")
    assert objects == {"t/a.csv": (b"x,y\n", "text/csv")}
    assert storage.get("t/a.csv") == b"x,y\n"
    storage.delete("t/a.csv")
    assert objects == {}
    with pytest.raises(storage.ExportStorageError, match="missing"):
        storage.get("t/a.csv")
    storage.delete("t/a.csv")  # already gone: no error
    assert set(buckets) == {"exports-real"}


def test_a_metric_job_takes_two_run_groupings_and_filters() -> None:
    clean = export_jobs.validate(
        "metric",
        "csv",
        {"key": "runs.volume", "group_by": ["agent", "status"], "filter": {"domain": "finance,hr"}},
        ADMIN,
    )
    assert clean["group_by"] == ["agent", "status"] and clean["filter"] == {"domain": ["finance", "hr"]}
    # A job stored before two-level grouping (one dimension as a string) still validates.
    assert export_jobs.validate("metric", "csv", {"key": "runs.volume", "group_by": "domain"}, ADMIN)["group_by"] == [
        "domain"
    ]
    assert _refused("metric", "csv", {"key": "cost.spend", "filter": {"domain": "finance"}}, ADMIN).error == (
        "filter_not_allowed"
    )
    assert _refused("metric", "csv", {"key": "runs.volume", "filter": "domain=finance"}, ADMIN).error == (
        "invalid_filter"
    )
