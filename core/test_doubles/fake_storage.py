"""Hermetic fake object storage (Foundation #7 PR-C).

Replaces the GCS upload in
``core.billing.invoice_generator._upload_pdf`` (and any future
GCS upload sites) with an in-process bucket so tests can assert
that an object *would have been* uploaded — what the bucket,
key, content-type, and bytes were — without needing GCP creds,
LocalStack, or fake-gcs.

Activation: ``AGENTICORG_TEST_FAKE_STORAGE=1``. The conftest sets
this by default for every test run.

Inspection::

    from core.test_doubles import fake_storage

    fake_storage.objects()                      # all uploads
    fake_storage.last()                         # most-recent
    fake_storage.get(bucket, key)               # one object's bytes
    fake_storage.list_in(bucket)                # keys in a bucket
    fake_storage.reset()                        # clear between tests

The autouse fixture in tests/conftest.py calls ``reset()`` before
each test so uploads don't leak across cases.

Why one global flag, not dependency injection: the GCS upload
sites are scattered across billing, RAG ingestion, knowledge,
and whatever ships next. Threading a storage client through every
constructor would touch dozens of files for one infra concern.
The flag gives us one seam at the boundary.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


@dataclass
class StoredObject:
    """One captured object upload."""

    bucket: str
    key: str
    data: bytes
    content_type: str = "application/octet-stream"
    timestamp: float = field(default_factory=time.time)

    @property
    def gs_url(self) -> str:
        return f"gs://{self.bucket}/{self.key}"


_OBJECTS: list[StoredObject] = []


def is_active() -> bool:
    """True iff ``AGENTICORG_TEST_FAKE_STORAGE`` is truthy."""
    return os.getenv("AGENTICORG_TEST_FAKE_STORAGE", "").lower() in (
        "1",
        "true",
        "yes",
    )


def upload(
    *,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> StoredObject:
    """Capture one object upload and return the record."""
    rec = StoredObject(bucket=bucket, key=key, data=data, content_type=content_type)
    _OBJECTS.append(rec)
    return rec


def objects() -> list[StoredObject]:
    """Return a copy of all captured uploads, oldest first."""
    return list(_OBJECTS)


def last() -> StoredObject | None:
    """Return the most-recent captured upload, or None if empty."""
    return _OBJECTS[-1] if _OBJECTS else None


def count() -> int:
    """Total objects captured since the last reset."""
    return len(_OBJECTS)


def get(bucket: str, key: str) -> StoredObject | None:
    """Return the most-recent object at (bucket, key), or None.

    Most recent wins because re-uploads to the same key overwrite
    in real GCS.
    """
    for obj in reversed(_OBJECTS):
        if obj.bucket == bucket and obj.key == key:
            return obj
    return None


def list_in(bucket: str) -> list[str]:
    """Return all keys captured in ``bucket``, oldest first."""
    return [obj.key for obj in _OBJECTS if obj.bucket == bucket]


def reset() -> None:
    """Clear the captured object list. Call from a test fixture
    to keep uploads isolated between cases."""
    _OBJECTS.clear()
