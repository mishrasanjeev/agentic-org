# SPDX-License-Identifier: Apache-2.0
"""Long-term memory: entries with retention, recalled into runs, remembered from outputs, erased per subject."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import memory as api
from core.config import settings
from core.memory import long_term

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


class _Result:
    def __init__(self, rows, rowcount=0):
        self.rows = rows
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.statements = []
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        answer = self.answers.pop(0) if self.answers else []
        if isinstance(answer, int):
            return _Result([], rowcount=answer)
        return _Result(answer)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "agent_id": None,
        "subject": "cust-42",
        "kind": "preference",
        "content": "Prefers email over calls.",
        "importance": 4,
        "source": "api",
        "retention_days": 365,
        "created_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=364),
        "recall_count": 0,
        "last_recalled_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestShape:
    def test_subjects_are_bounded_identifiers_and_the_run_names_one_in_its_context(self):
        assert long_term.normalise_subject(" Customer 42 ") == "customer-42"
        assert long_term.normalise_subject("user@example.test") == "user@example.test"
        assert (
            long_term.normalise_subject("x" * 200) == "x" * long_term.MAX_SUBJECT
            and long_term.normalise_subject(None) == ""
        )
        assert long_term.subject_of({"context": {"subject": "Cust 42"}}) == "cust-42"
        assert (
            long_term.subject_of({"subject": "a"}) == "a"
            and long_term.subject_of("nope") == ""
            and long_term.subject_of({}) == ""
        )

    def test_retention_follows_the_kind_unless_asked_within_the_bound(self):
        assert long_term.retention_for("fact") == 365 and long_term.retention_for("event") == 30
        assert long_term.retention_for("summary", 10) == 10
        with pytest.raises(long_term.MemoryStoreError):
            long_term.retention_for("fact", 0)
        with pytest.raises(long_term.MemoryStoreError):
            long_term.retention_for("fact", long_term.MAX_RETENTION_DAYS + 1)
        policy = long_term.policy()
        assert policy["kinds"] == ["fact", "preference", "summary", "event"] and policy["max_retention_days"] == 730
        assert settings.runtime_memory_enabled is False and long_term.enabled() is False

    def test_a_prompt_block_is_bounded_and_empty_without_entries(self):
        assert long_term.prompt_block([]) == ""
        block = long_term.prompt_block([_row(), _row(kind="fact", content="Account opened in 2021.")])
        assert (
            block.startswith("What is remembered about this subject")
            and "- (preference) Prefers email over calls." in block
        )
        many = long_term.prompt_block([_row(content="x" * 400) for _ in range(10)])
        assert len(many) <= long_term.MAX_PROMPT_CHARS + 200

    def test_memories_in_an_output_are_shaped_and_bounded(self):
        output = {
            "remember": [
                "Likes Tuesday calls",
                {"content": "Has two accounts", "kind": "fact", "importance": 5},
                {"content": "", "kind": "x"},
                {"content": "odd kind", "kind": "weird", "importance": 9},
            ]
            + ["more"] * 10
        }
        kept = long_term.memories_in_output(output)
        assert len(kept) == long_term.MAX_REMEMBER_PER_RUN - 1
        assert kept[0] == {"content": "Likes Tuesday calls", "kind": "fact", "importance": 3}
        assert kept[1] == {"content": "Has two accounts", "kind": "fact", "importance": 5}
        assert kept[2] == {"content": "odd kind", "kind": "fact", "importance": 3}
        assert (
            long_term.memories_in_output({"remember": "not a list"}) == [] and long_term.memories_in_output(None) == []
        )


class TestStore:
    @pytest.mark.asyncio
    async def test_remember_stores_a_new_entry_with_its_expiry_and_refreshes_a_duplicate(self):
        tid = uuid.uuid4()
        session = _Session([])
        row = await long_term.remember(
            session, tid, subject="Cust 42", content="  Prefers   email ", kind="preference", importance=4, now=NOW
        )
        assert session.added == [row] and row.subject == "cust-42" and row.content == "Prefers email"
        assert row.expires_at == NOW + timedelta(days=365) and row.retention_days == 365 and row.source == "api"
        existing = _row(importance=2, expires_at=NOW + timedelta(days=3))
        refreshed = await long_term.remember(
            _Session([existing]),
            tid,
            subject="cust-42",
            content="Prefers email over calls.",
            kind="preference",
            importance=4,
            retention_days=10,
            now=NOW,
        )
        assert refreshed is existing and existing.expires_at == NOW + timedelta(days=10) and existing.importance == 4
        agent = uuid.uuid4()
        scoped = await long_term.remember(
            _Session([]),
            tid,
            subject="cust-42",
            content="x",
            agent_id=str(agent),
            source="run",
            run_id="r" * 100,
            now=NOW,
        )
        assert scoped.agent_id == agent and scoped.source == "run" and len(scoped.run_id) == 64

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("fields", "code"),
        [
            ({"subject": "   ", "content": "x"}, "subject"),
            ({"subject": "s", "content": ""}, "content"),
            ({"subject": "s", "content": "x" * 2001}, "content"),
            ({"subject": "s", "content": "x", "kind": "rumour"}, "kind"),
            ({"subject": "s", "content": "x", "importance": 6}, "importance"),
            ({"subject": "s", "content": "x", "retention_days": 0}, "retention_days"),
        ],
    )
    async def test_bad_entries_are_refused(self, fields, code):
        with pytest.raises(long_term.MemoryStoreError) as refused:
            await long_term.remember(_Session([]), uuid.uuid4(), **fields)
        assert refused.value.code == code

    @pytest.mark.asyncio
    async def test_recall_reads_unexpired_entries_for_the_subject_and_counts_the_recall(self):
        rows = [_row(), _row(kind="fact", content="Account opened in 2021.", importance=2)]
        session = _Session(rows)
        got = await long_term.recall(
            session, uuid.uuid4(), subject="Cust 42", agent_id=str(uuid.uuid4()), query="email", limit=500, now=NOW
        )
        assert got == rows and rows[0].recall_count == 1 and rows[0].last_recalled_at == NOW
        statement = session.statements[0]
        assert (
            "agent_memories.expires_at >" in statement
            and "agent_memories.subject =" in statement
            and "LIKE" in statement
        )
        assert "LIMIT" in statement
        assert await long_term.recall(_Session(), uuid.uuid4(), subject="  ") == []

    @pytest.mark.asyncio
    async def test_remember_from_output_stores_what_the_run_asked_to_keep(self):
        session = _Session([], [])
        tid = uuid.uuid4()
        count = await long_term.remember_from_output(
            session,
            tid,
            subject="cust-42",
            agent_id=None,
            output={"remember": ["A", {"content": "B", "kind": "event"}]},
            run_id="run_1",
        )
        assert count == 2 and [r.content for r in session.added] == ["A", "B"] and session.added[1].kind == "event"
        assert all(r.source == "run" and r.run_id == "run_1" for r in session.added)

    @pytest.mark.asyncio
    async def test_erase_and_prune_delete_and_report_the_count(self):
        tid = uuid.uuid4()
        session = _Session(3)
        assert await long_term.erase(session, tid, subject="Cust 42") == 3
        assert (
            "DELETE FROM agent_memories" in session.statements[0]
            and "agent_memories.agent_id" not in session.statements[0]
        )
        scoped = _Session(1)
        assert await long_term.erase(scoped, tid, subject="cust-42", agent_id=str(uuid.uuid4())) == 1
        assert "agent_memories.agent_id" in scoped.statements[0]
        with pytest.raises(long_term.MemoryStoreError):
            await long_term.erase(_Session(), tid, subject="")
        pruned = _Session(7)
        assert (
            await long_term.prune(pruned, tid, now=NOW) == 7 and "agent_memories.expires_at <=" in pruned.statements[0]
        )


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_off_the_endpoints_are_not_found_and_the_policy_still_answers(self):
        tid = str(uuid.uuid4())
        assert (await api.memory_policy(tenant_id=tid))["kinds"] == list(long_term.KINDS)
        with pytest.raises(HTTPException) as refused:
            await api.recall_memory(subject="cust-42", agent_id=None, q=None, limit=10, tenant_id=tid)
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "runtime_memory_disabled"
        with pytest.raises(HTTPException) as refused:
            await api.erase_memory(subject="cust-42", agent_id=None, tenant_id=tid)
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_on_remember_recall_erase_and_prune_answer(self, monkeypatch):
        monkeypatch.setattr(settings, "runtime_memory_enabled", True)
        tid = str(uuid.uuid4())
        write = _Session([])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: write)
        entry = await api.remember_memory(
            api.MemoryIn(subject="Cust 42", content="Prefers email", kind="preference"), tenant_id=tid, user={}
        )
        assert entry["subject"] == "cust-42" and entry["kind"] == "preference" and entry["retention_days"] == 365
        with pytest.raises(HTTPException) as refused:
            await api.remember_memory(api.MemoryIn(subject="s", content="x", kind="rumour"), tenant_id=tid, user={})
        assert refused.value.status_code == 422 and refused.value.detail["error"] == "kind"
        with pytest.raises(HTTPException) as refused:
            await api.remember_memory(api.MemoryIn(subject="s", content="x", agent_id="nope"), tenant_id=tid, user={})
        assert refused.value.detail["error"] == "agent_id"
        read = _Session([_row()])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: read)
        recalled = await api.recall_memory(subject="cust-42", agent_id=None, q=None, limit=10, tenant_id=tid)
        assert recalled["total"] == 1 and recalled["entries"][0]["content"] == "Prefers email over calls."
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session(2))
        assert (await api.erase_memory(subject="cust-42", agent_id=None, tenant_id=tid)) == {
            "subject": "cust-42",
            "erased": 2,
        }
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session(5))
        assert (await api.prune_memory(tenant_id=tid)) == {"pruned": 5}

    def test_the_run_recalls_and_remembers_and_the_task_is_scheduled(self):
        runner = (ROOT / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        assert "long_term_memory.subject_of(task_input)" in runner and "long_term_memory.prompt_block(" in runner
        assert "long_term_memory.remember_from_output(" in runner
        main = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        assert "app.include_router(memory.router" in main
        celery = (ROOT / "core" / "tasks" / "celery_app.py").read_text(encoding="utf-8")
        assert '"core.tasks.memory_tasks"' in celery and "core.tasks.memory_tasks.prune_expired_memories" in celery
        migration = (ROOT / "migrations" / "versions" / "v6_z57_agent_memories.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z56_finops_thresholds"' in migration and "ix_agent_memories_agent_id" in migration
        assert "agent_memories_tenant_isolation" in migration
        from core.models.agent_memory import AgentMemory

        assert AgentMemory.__tablename__ == "agent_memories"
