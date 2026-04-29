"""Foundation #7 PR-C — fake storage hermetic seam regressions.

Pinned behaviors:

- ``is_active()`` reflects the env var.
- ``upload``, ``get``, ``list_in``, ``count``, ``last``, ``reset``
  work as documented.
- The production ``_upload_pdf`` short-circuits to fake_storage
  when the flag is set; no GCS client is constructed.
- Re-uploads to the same key overwrite-by-recency semantics.
- Conftest sets the flag by default; autouse fixture resets the
  bucket between tests (paired bleed-check + parametrize).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from core.test_doubles import fake_storage


def test_is_active_reflects_env_var(monkeypatch) -> None:
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_STORAGE", raising=False)
    assert fake_storage.is_active() is False
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_STORAGE", "1")
    assert fake_storage.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_STORAGE", "true")
    assert fake_storage.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_STORAGE", "no")
    assert fake_storage.is_active() is False


def test_upload_records_object_and_returns_record() -> None:
    rec = fake_storage.upload(
        bucket="b1", key="path/to/x.pdf", data=b"hello", content_type="application/pdf"
    )
    assert rec.bucket == "b1"
    assert rec.key == "path/to/x.pdf"
    assert rec.data == b"hello"
    assert rec.content_type == "application/pdf"
    assert rec.gs_url == "gs://b1/path/to/x.pdf"
    assert fake_storage.count() == 1
    assert fake_storage.last() is rec


def test_get_returns_most_recent_for_overwrite_semantics() -> None:
    fake_storage.upload(bucket="b", key="k", data=b"v1")
    fake_storage.upload(bucket="b", key="k", data=b"v2")
    obj = fake_storage.get("b", "k")
    assert obj is not None
    assert obj.data == b"v2"


def test_get_returns_none_when_not_found() -> None:
    assert fake_storage.get("missing-bucket", "missing-key") is None


def test_list_in_filters_by_bucket_oldest_first() -> None:
    fake_storage.upload(bucket="a", key="k1", data=b"x")
    fake_storage.upload(bucket="b", key="k2", data=b"x")
    fake_storage.upload(bucket="a", key="k3", data=b"x")
    assert fake_storage.list_in("a") == ["k1", "k3"]
    assert fake_storage.list_in("b") == ["k2"]
    assert fake_storage.list_in("nobody") == []


def test_reset_clears_objects() -> None:
    fake_storage.upload(bucket="b", key="k", data=b"x")
    assert fake_storage.count() == 1
    fake_storage.reset()
    assert fake_storage.count() == 0
    assert fake_storage.last() is None


def test_invoice_upload_pdf_captures_under_fake(monkeypatch) -> None:
    """End-to-end: production _upload_pdf path captures the PDF in
    the fake bucket when the flag is on, never constructing a real
    storage.Client."""
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_STORAGE", "1")
    monkeypatch.setenv("AGENTICORG_INVOICE_BUCKET", "test-invoice-bucket")

    from core.billing.invoice_generator import _upload_pdf

    tenant_id = uuid.uuid4()
    url = asyncio.run(_upload_pdf(tenant_id, "INV-2026-0001", b"%PDF-1.4 fake"))
    assert url == f"gs://test-invoice-bucket/invoices/{tenant_id}/INV-2026-0001.pdf"
    assert fake_storage.count() == 1

    obj = fake_storage.get(
        "test-invoice-bucket", f"invoices/{tenant_id}/INV-2026-0001.pdf"
    )
    assert obj is not None
    assert obj.data == b"%PDF-1.4 fake"
    assert obj.content_type == "application/pdf"


def test_invoice_upload_pdf_returns_empty_when_no_bucket(monkeypatch) -> None:
    """No-bucket env var → fake still no-ops cleanly + no capture."""
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_STORAGE", "1")
    monkeypatch.delenv("AGENTICORG_INVOICE_BUCKET", raising=False)

    from core.billing.invoice_generator import _upload_pdf

    url = asyncio.run(_upload_pdf(uuid.uuid4(), "INV-X", b"x"))
    assert url == ""
    assert fake_storage.count() == 0


def test_conftest_default_makes_fake_storage_active() -> None:
    assert os.environ.get("AGENTICORG_TEST_FAKE_STORAGE") == "1"
    assert fake_storage.is_active() is True


def test_autouse_fixture_resets_storage_part_1() -> None:
    fake_storage.upload(bucket="bleed", key="k", data=b"x")
    assert fake_storage.count() == 1


def test_autouse_fixture_resets_storage_part_2() -> None:
    """Second half of the bleed-check pair — must see empty bucket."""
    assert fake_storage.count() == 0


@pytest.mark.parametrize("bucket", ["b1", "b2", "b3"])
def test_each_param_starts_with_empty_storage(bucket) -> None:
    assert fake_storage.count() == 0
    fake_storage.upload(bucket=bucket, key="k", data=b"x")
    assert fake_storage.count() == 1
