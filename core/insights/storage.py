# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Where Insights export files live: one Google Cloud Storage bucket, read and written by the API and the worker.

The bucket is ``settings.insights_export_bucket``. Files are fetched through
the API for an authenticated user (no signed URL: one could not be revoked on
logout-all). Under ``AGENTICORG_TEST_FAKE_STORAGE`` the in-process fake bucket
stands in (``core.test_doubles.fake_storage``). The calls are synchronous; run
them in a thread from async code.
"""

from __future__ import annotations


class ExportStorageError(RuntimeError):
    """The export bucket is not configured or the object could not be read or written."""


def bucket_name() -> str:
    from core.config import settings

    return str(getattr(settings, "insights_export_bucket", "") or "").strip()


def configured() -> bool:
    return bool(bucket_name())


def _bucket():
    from google.cloud import storage

    name = bucket_name()
    if not name:
        raise ExportStorageError("export_storage_unconfigured")
    return storage.Client().bucket(name)


def put(key: str, data: bytes, content_type: str) -> None:
    from core.test_doubles import fake_storage

    name = bucket_name()
    if not name:
        raise ExportStorageError("export_storage_unconfigured")
    if fake_storage.is_active():
        fake_storage.upload(bucket=name, key=key, data=data, content_type=content_type)
        return
    _bucket().blob(key).upload_from_string(data, content_type=content_type)


def get(key: str) -> bytes:
    from core.test_doubles import fake_storage

    name = bucket_name()
    if not name:
        raise ExportStorageError("export_storage_unconfigured")
    if fake_storage.is_active():
        obj = fake_storage.get(name, key)
        if obj is None:
            raise ExportStorageError("export_object_missing")
        return obj.data
    blob = _bucket().blob(key)
    if not blob.exists():
        raise ExportStorageError("export_object_missing")
    return blob.download_as_bytes()


def delete(key: str) -> None:
    """Delete one object; a missing object is already gone."""
    from core.test_doubles import fake_storage

    name = bucket_name()
    if not name:
        return
    if fake_storage.is_active():
        fake_storage.delete(name, key)
        return
    blob = _bucket().blob(key)
    if blob.exists():
        blob.delete()
