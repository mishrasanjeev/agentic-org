# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The browser knowledge-upload test must allow for the product's own retry schedule.

Production E2E run 39477578804 (2026-10-20, release 6fdb3af2): the hosted
embedding service was scaled to zero and took about 80 s to start. Each upload's
own embedding call gave up after the 5 s embedding read timeout, so the upload
was stored as ``processing`` with ``embedding_temporarily_unavailable`` and the
maintenance worker indexed it on a later sweep, 79 s and 86 s after upload.
``ui/e2e/knowledge-upload-recovery.spec.ts`` waited inside the suite's 60 s test
timeout, so both attempts timed out; the cleanup in the test body's ``finally``
then ran against a closed request context and the documents stayed behind.

These tests replay that timeline through ``core.rag.upload_recovery.process``
(the code the upload request and the worker sweep run) with a simulated clock,
an embedding service that is cold for a given time, and the beat period the
worker really uses, then hold the spec's budget to the result.
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.embeddings import DEFAULT_TEI_READ_TIMEOUT_SECONDS
from core.rag import upload_recovery as recovery
from core.rag.extractors import ExtractedContent, ExtractedSpan
from core.rag.ingest import IngestResult
from core.tasks.celery_app import app as celery_app

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ui" / "e2e" / "helpers" / "knowledge.ts"
SPEC = ROOT / "ui" / "e2e" / "knowledge-upload-recovery.spec.ts"

# The suite timeout the spec ran under (ui/e2e/regression.config.ts).
OLD_TEST_TIMEOUT_S = 60
# Cold starts seen on 2026-10-20: first embedding request to first answer, about 80 s twice.
OBSERVED_COLD_START_S = 80
# Allowance on top: worker run time and the knowledge page's 5 s status poll.
ROW_UPDATE_MARGIN_S = 15

UPLOADED_AT = datetime(2026, 10, 20, 19, 55, 49, tzinfo=UTC)


def _budget_seconds() -> int:
    match = re.search(r"export const KNOWLEDGE_INDEX_BUDGET_MS = ([\d_]+);", HELPER.read_text(encoding="utf-8"))
    assert match, "ui/e2e/helpers/knowledge.ts must export KNOWLEDGE_INDEX_BUDGET_MS"
    return int(match.group(1).replace("_", "")) // 1000


def _sweep_period_seconds() -> int:
    return int(celery_app.conf.beat_schedule["knowledge-upload-recovery"]["schedule"])


@pytest.fixture
def pending_upload(monkeypatch):
    """One stored upload waiting for indexing, behind a fake tenant session."""
    tenant = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant,
        filename="synthetic-browser.txt",
        name="synthetic-browser.txt",
        content_type="text/plain",
    )
    _reset(row)
    session = SimpleNamespace(scalar=AsyncMock(return_value=row), execute=AsyncMock())

    @asynccontextmanager
    async def scoped(tid):
        assert tid == tenant
        yield session

    monkeypatch.setattr(recovery, "get_tenant_session", scoped)
    monkeypatch.setattr(recovery.settings, "knowledge_upload_recovery_enabled", True)
    return tenant, row


async def _seconds_until_indexed(
    monkeypatch, tenant: uuid.UUID, row: SimpleNamespace, *, cold_start_s: int, sweep_phase_s: int, jitter_s: tuple
) -> int:
    """Upload at t=0 with a cold embedding service; return when the row is indexed.

    The upload request makes the first attempt at once. The worker then tries the
    row at each sweep (every beat period, starting ``sweep_phase_s`` after the
    upload), reaching it ``jitter_s[k]`` seconds into sweep ``k``. An embedding
    call succeeds only if the service is ready before the read timeout expires.
    """
    clock = {"t": 0}

    async def embed_or_time_out(**_kwargs):
        if clock["t"] + DEFAULT_TEI_READ_TIMEOUT_SECONDS >= cold_start_s:
            return IngestResult("", 1, 0, 5, "text/plain", "plaintext", "tei/bge-m3", 1024)
        return IngestResult("", 0, 1, 0, "text/plain", "plaintext", "tei/bge-m3", 1024, retryable=True)

    monkeypatch.setattr(recovery, "ingest_document", AsyncMock(side_effect=embed_or_time_out))

    attempts = [0] + [sweep_phase_s + k * _sweep_period_seconds() + jitter_s[k % len(jitter_s)] for k in range(12)]
    for at in attempts:
        clock["t"] = at
        status = await recovery.process(tenant, row.id, now=UPLOADED_AT + timedelta(seconds=at))
        assert status in {"processing", "indexed", "skipped"}, f"unexpected terminal state {status!r} at t={at}s"
        if row.status == "indexed":
            return at
    raise AssertionError("never indexed within twelve sweeps")


@pytest.mark.asyncio
async def test_old_60s_budget_could_never_hold_the_observed_cold_start(pending_upload, monkeypatch):
    tenant, row = pending_upload
    earliest = None
    for phase in range(_sweep_period_seconds()):
        _reset(row)
        indexed_at = await _seconds_until_indexed(
            monkeypatch, tenant, row, cold_start_s=OBSERVED_COLD_START_S, sweep_phase_s=phase, jitter_s=(0,)
        )
        earliest = indexed_at if earliest is None else min(earliest, indexed_at)
    # Even the luckiest sweep phase indexes after the old test had already timed out.
    assert earliest > OLD_TEST_TIMEOUT_S


@pytest.mark.parametrize("jitter_s", [(0,), (2, 0)], ids=["steady-sweeps", "next-sweep-reaches-row-early"])
@pytest.mark.asyncio
async def test_budget_holds_the_observed_cold_start_in_every_sweep_phase(pending_upload, monkeypatch, jitter_s):
    tenant, row = pending_upload
    budget = _budget_seconds()
    worst = 0
    for phase in range(_sweep_period_seconds()):
        _reset(row)
        indexed_at = await _seconds_until_indexed(
            monkeypatch, tenant, row, cold_start_s=OBSERVED_COLD_START_S + 5, sweep_phase_s=phase, jitter_s=jitter_s
        )
        worst = max(worst, indexed_at)
    assert worst + ROW_UPDATE_MARGIN_S <= budget, (
        f"a cold start can take {worst}s to index; KNOWLEDGE_INDEX_BUDGET_MS allows {budget}s"
    )


def test_spec_waits_on_the_budget_and_cleans_up_after_a_timeout():
    helper = HELPER.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")
    assert "KNOWLEDGE_INDEX_TEST_TIMEOUT_MS = KNOWLEDGE_INDEX_BUDGET_MS + 60_000" in helper
    assert "timeout: KNOWLEDGE_INDEX_BUDGET_MS" in helper
    # Both indexing tests run under the derived timeout, never the suite default.
    assert spec.count("test.setTimeout(KNOWLEDGE_INDEX_TEST_TIMEOUT_MS)") == 2
    assert "expectRowIndexed(row, accepted)" in spec
    # Cleanup in afterEach still runs after a timeout; a finally in the test body does not.
    assert "test.afterEach(async ({ request })" in spec
    assert "} finally {" not in spec


def _reset(row: SimpleNamespace) -> None:
    """A freshly stored upload: processing, first attempt due at upload time."""
    content = ExtractedContent(
        spans=[ExtractedSpan(text="synthetic-browser review code 40721.")],
        mime_type="text/plain",
        extraction_method="plaintext",
        total_chars=36,
    )
    row.status = "processing"
    row.metadata_ = {recovery.KEY: {**recovery.prepare(content), "next_attempt_at": UPLOADED_AT.isoformat()}}
