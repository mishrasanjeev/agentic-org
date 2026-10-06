# SPDX-License-Identifier: Apache-2.0
"""Promotion evidence is selected tenant-safely from real PostgreSQL rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core.config import settings
from core.evals import gates, runs
from core.models.eval_dataset import EvalDataset, EvalDatasetVersion
from core.models.eval_run import EvalRun


@pytest.mark.asyncio(loop_scope="session")
async def test_complete_model_bound_evidence_and_explicit_baseline(db_session, tenant_id, monkeypatch):
    tid = uuid.UUID(tenant_id)
    dataset_id = uuid.uuid4()
    content_hash = "a" * 64
    prompt = "Classify this synthetic case."
    model = "gpt-4o"
    monkeypatch.setattr(settings, "evals_v2_enabled", True)
    monkeypatch.setattr(settings, "eval_promotion_gate_enabled", True)
    monkeypatch.setattr(settings, "llm_primary", model)
    monkeypatch.setattr(settings, "llm_fallback", model)
    db_session.add(EvalDataset(id=dataset_id, tenant_id=tid, name=f"gate-{dataset_id}", latest_version=1, case_count=2))
    await db_session.flush()
    db_session.add(
        EvalDatasetVersion(
            tenant_id=tid,
            dataset_id=dataset_id,
            version=1,
            cases=[],
            case_count=2,
            content_hash=content_hash,
        )
    )
    await db_session.flush()
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        llm_model=model,
        system_prompt_text=prompt,
        config={"eval_gate": {"dataset_id": str(dataset_id), "min_pass_rate": 80}},
    )
    sequence = 0

    async def store(**over):
        nonlocal sequence
        sequence += 1
        values = {
            "id": uuid.uuid4(),
            "tenant_id": tid,
            "dataset_id": dataset_id,
            "version": 1,
            "content_hash": content_hash,
            "model": model,
            "prompt_hash": runs.prompt_hash(prompt),
            "offset": 0,
            "cases_run": 2,
            "cases_total": 2,
            "passed": 2,
            "failed": 0,
            "errors": 0,
            "pass_rate": 1.0,
            "scores": {},
            "created_at": datetime.now(UTC) + timedelta(seconds=sequence),
        }
        values.update(over)
        row = EvalRun(**values)
        db_session.add(row)
        await db_session.flush()
        return row

    # Another model cannot satisfy the configured model, despite identical text.
    await store(model="gpt-4o-mini")
    assert (await gates.evaluate(db_session, tid, agent)).code == "not_evaluated"
    await store(cases_run=1, passed=1)
    assert (await gates.evaluate(db_session, tid, agent)).code == "incomplete_run"
    await store(errors=1, passed=1)
    assert (await gates.evaluate(db_session, tid, agent)).code == "evaluation_errors"
    current = await store(pass_rate=0.9)
    assert (await gates.evaluate(db_session, tid, agent)).ok
    # Unrelated prompts are not silently picked as this agent's prior revision.
    baseline = await store(prompt_hash=runs.prompt_hash("Prior approved text."))
    assert (await gates.evaluate(db_session, tid, agent)).ok
    agent.config["eval_gate"]["baseline_run_id"] = str(baseline.id)
    assert (await gates.evaluate(db_session, tid, agent)).code == "regressed"
    agent.config["eval_gate"]["baseline_run_id"] = str(uuid.uuid4())
    assert (await gates.evaluate(db_session, tid, agent)).code == "baseline_unusable"
    # Tenant predicates are enforced even when the DB test role can bypass RLS.
    with pytest.raises(gates.datasets.DatasetError):
        await runs.get_run(db_session, uuid.uuid4(), current.id)
    agent.config["eval_gate"].pop("baseline_run_id")
    agent.llm_model = "never-evaluated"
    assert (await gates.evaluate(db_session, tid, agent)).code == "not_evaluated"
