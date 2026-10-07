# SPDX-License-Identifier: Apache-2.0
"""Long-term memory inside a run: recalled entries are redacted like the task, and the sidecar never fails a run."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import BaseMessage, SystemMessage
from sqlalchemy.exc import MultipleResultsFound, OperationalError

from core.config import settings
from core.memory import long_term
from core.pii.redactor import PIIRedactor
from core.test_doubles.scripted_model import final

TENANT = str(uuid.uuid4())
RAW_EMAIL = "jo.tester@example.test"


class _NoDatabase:
    """A session that answers nothing: the run's optional reads fall back as they would offline."""

    async def __aenter__(self) -> _NoDatabase:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("no database in unit tests")


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.langgraph import runner
    from core.pii import pseudonymiser

    async def no_explanation(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"bullets": []}

    monkeypatch.setattr("core.billing.metering.gate_agent_run", AsyncMock(return_value=None))
    monkeypatch.setattr("core.billing.metering.meter_agent_run", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "prefetch_llm_credential", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "generate_explanation", no_explanation)
    monkeypatch.setattr(pseudonymiser, "pseudonymisation_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr("core.database.get_tenant_session", lambda _tid: _NoDatabase())
    monkeypatch.setattr(settings, "runtime_memory_enabled", True)
    monkeypatch.setenv("AGENTICORG_PII_REDACTION_MODE", "before_llm")


def _remembered(content: str) -> SimpleNamespace:
    return SimpleNamespace(kind="preference", content=content)


async def _run(**overrides: Any) -> dict[str, Any]:
    from core.langgraph.runner import run_agent

    arguments: dict[str, Any] = {
        "agent_id": str(uuid.uuid4()),
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": TENANT,
        "system_prompt": "You answer account questions.",
        "authorized_tools": [],
        "task_input": {"action": "answer", "context": {"subject": "cust-42", "question": "How to reach them?"}},
        "confidence_floor": 0.5,
        "connector_config": {},
        "connector_names": [],
    }
    arguments.update(overrides)
    return await run_agent(**arguments)


def _system(messages: list[BaseMessage]) -> str:
    system = messages[0]
    assert isinstance(system, SystemMessage)
    return str(system.content)


class TestRedaction:
    def test_the_block_is_redacted_under_before_llm_and_its_tokens_join_the_run_map(self, monkeypatch):
        monkeypatch.setenv("AGENTICORG_PII_REDACTION_MODE", "before_llm")
        from core.langgraph.runner import _redacted_memory_block

        token_map: dict[str, str] = {}
        block = long_term.prompt_block([_remembered(f"Write to {RAW_EMAIL} only.")])
        redacted = _redacted_memory_block(block, "before_llm", PIIRedactor(), token_map)
        assert RAW_EMAIL not in redacted and redacted.startswith("What is remembered about this subject")
        assert RAW_EMAIL in token_map.values()
        assert _redacted_memory_block("", "before_llm", PIIRedactor(), token_map) == ""
        assert _redacted_memory_block(block, "disabled", PIIRedactor(), {}) == block

    @pytest.mark.asyncio
    async def test_recalled_personal_data_never_reaches_the_model_raw(self, scripted_model, offline, monkeypatch):
        monkeypatch.setattr(
            long_term, "recall", AsyncMock(return_value=[_remembered(f"Prefers email at {RAW_EMAIL}.")])
        )
        model = scripted_model([final({"status": "completed", "confidence": 0.95})])
        result = await _run()
        assert result["status"] == "completed", result
        system = _system(model.calls[0])
        assert "What is remembered about this subject" in system and "Prefers email at" in system
        assert RAW_EMAIL not in system
        assert all(RAW_EMAIL not in str(m.content) for m in model.calls[0])


class TestSidecarFailures:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [OperationalError("SELECT 1", {}, Exception("down")), MultipleResultsFound("two rows")],
    )
    async def test_a_recall_database_error_leaves_the_run_without_memory(
        self, scripted_model, offline, monkeypatch, error
    ):
        monkeypatch.setattr(long_term, "recall", AsyncMock(side_effect=error))
        model = scripted_model([final({"status": "completed", "confidence": 0.95})])
        result = await _run()
        assert result["status"] == "completed", result
        assert "What is remembered about this subject" not in _system(model.calls[0])

    @pytest.mark.asyncio
    async def test_a_store_database_error_keeps_the_completed_run_completed(self, scripted_model, offline, monkeypatch):
        monkeypatch.setattr(long_term, "recall", AsyncMock(return_value=[]))
        store = AsyncMock(side_effect=OperationalError("INSERT", {}, Exception("down")))
        monkeypatch.setattr(long_term, "remember_from_output", store)
        scripted_model([final({"status": "completed", "confidence": 0.95, "remember": ["Prefers mornings"]})])
        result = await _run()
        assert store.await_count == 1
        assert result["status"] == "completed", result
        assert "remembered" not in result
