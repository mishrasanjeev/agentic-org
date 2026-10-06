# SPDX-License-Identifier: Apache-2.0
"""Ratings, reliability metrics and certification on the card, and their endpoints."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import agent_registry as api
from core.agent_registry import reliability
from core.config import settings
from core.models.agent_rating import AgentRating
from core.schemas.api import AgentRatingIn

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()
USER = uuid.uuid4()


class _Session:
    """Answers each query from a script: ``all()`` rows, ``one()`` tuples or a scalar row."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []
        self.added: list[Any] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        value = self.answers.pop(0)
        return SimpleNamespace(
            all=lambda: list(value) if isinstance(value, list) else [],
            one=lambda: value,
            scalar_one_or_none=lambda: value,
            scalars=lambda: SimpleNamespace(all=lambda: list(value) if isinstance(value, list) else []),
        )

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


def _agent(**over):
    base = {"id": uuid.uuid4(), "llm_provider": "openai", "shadow_accuracy_current": Decimal("0.910")}
    base.update(over)
    return SimpleNamespace(**base)


class TestMetrics:
    def test_counts_and_sums_over_the_window(self):
        agent = _agent()
        session = _Session(
            [("completed", 8, 2), ("failed", 1, 0), ("hitl_triggered", 1, 1)],
            (412.5, 1200.0, 9000, 0.9, 0.87),
            [("thumbs_up", 5), ("correction", 1)],
        )
        result = asyncio.run(reliability.metrics(session, TENANT, agent, days=7))
        assert result["window_days"] == 7 and result["runs"] == 10
        assert result["by_status"] == {"completed": 8, "failed": 1, "hitl_triggered": 1}
        assert (result["completion_rate"], result["failure_rate"], result["human_review_rate"]) == (0.8, 0.1, 0.3)
        assert (result["avg_duration_ms"], result["p95_duration_ms"]) == (412, 1200)
        assert result["tokens_per_run"] == 900.0 and result["cost_per_run_usd"] == 0.09
        assert result["avg_confidence"] == 0.87 and result["feedback"] == {"thumbs_up": 5, "correction": 1}
        assert result["shadow_accuracy"] == 0.91
        for statement in session.statements:
            assert "agent_task_results.tenant_id" in statement or "agent_feedback.tenant_id" in statement
            assert "created_at >= " in statement
        assert (
            "percentile_cont" in session.statements[1] and "GROUP BY agent_task_results.status" in session.statements[0]
        )

    def test_an_agent_with_no_runs_has_no_rates(self):
        session = _Session([], (None, None, None, None, None), [])
        result = asyncio.run(reliability.metrics(session, TENANT, _agent(shadow_accuracy_current=None)))
        assert result["runs"] == 0 and result["completion_rate"] is None and result["avg_duration_ms"] is None
        assert result["tokens_per_run"] is None and result["shadow_accuracy"] is None and result["window_days"] == 30

    def test_the_window_is_bounded(self):
        assert reliability.validate_window(None) == 30 and reliability.validate_window(365) == 365
        for bad in (0, 366, True, "30"):
            with pytest.raises(reliability.RatingError):
                reliability.validate_window(bad)


class TestRatings:
    def test_a_first_rating_is_added_and_a_second_by_the_same_person_replaces_it(self):
        agent_id = uuid.uuid4()
        session = _Session(None)
        first = asyncio.run(reliability.rate(session, TENANT, agent_id, USER, 4, " good "))
        assert session.added == [first] and (first.score, first.comment, first.user_id) == (4, "good", USER)
        assert "FOR UPDATE" in session.statements[0] and "agent_ratings.user_id" in session.statements[0]
        session = _Session(first)
        again = asyncio.run(reliability.rate(session, TENANT, agent_id, USER, 2, None))
        assert again is first and first.score == 2 and first.comment is None and session.added == []

    def test_refused_scores_and_comments(self):
        for score, comment in ((0, None), (6, None), (True, None), (3, "c" * 501), ("4", None)):
            with pytest.raises(reliability.RatingError):
                asyncio.run(reliability.rate(_Session(None), TENANT, uuid.uuid4(), USER, score, comment))

    def test_the_summary_is_a_count_and_an_average_only(self):
        session = _Session((3, 4.3333))
        assert asyncio.run(reliability.rating_summary(session, TENANT, uuid.uuid4())) == {"count": 3, "average": 4.33}
        assert asyncio.run(reliability.rating_summary(_Session((0, None)), TENANT, uuid.uuid4())) == {
            "count": 0,
            "average": None,
        }


class TestCertification:
    def test_the_registry_the_gate_and_the_provider_attestation_are_reported(self):
        agent = _agent()
        attestation = SimpleNamespace(
            provider="openai",
            data_region="IN",
            in_region=True,
            no_training=True,
            revoked_at=None,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            attested_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        result = asyncio.run(
            reliability.certification(
                _Session(attestation), TENANT, agent, registry_state="published", gate_verdict={"ok": True}
            )
        )
        assert result["registry_approved"] is True and result["registry_state"] == "published"
        assert result["provider_attestation"]["valid"] is True and result["provider_attestation"]["in_region"] is True
        assert result["trust_registry"] == {
            "attached": False,
            "note": "Grantex trust-registry attestations and passports are not attached",
        }
        expired = SimpleNamespace(**{**vars(attestation), "expires_at": datetime.now(UTC) - timedelta(days=1)})
        result = asyncio.run(
            reliability.certification(_Session(expired), TENANT, agent, registry_state="draft", gate_verdict={})
        )
        assert result["registry_approved"] is False and result["provider_attestation"]["valid"] is False
        result = asyncio.run(
            reliability.certification(
                _Session(), TENANT, _agent(llm_provider=None), registry_state="review", gate_verdict={}
            )
        )
        assert result["provider_attestation"] is None


class TestEndpoints:
    @pytest.fixture
    def store(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_registry_enabled", True)
        monkeypatch.setattr(api, "require_agent_visible", lambda _agent, _caller: None)
        holder: dict[str, _Session] = {}

        @asynccontextmanager
        async def _session(_tenant):
            yield holder["session"]

        monkeypatch.setattr(api, "get_tenant_session", _session)

        async def _metrics(_session, _tenant, _agent, days=30):
            return {"window_days": days, "runs": 1}

        async def _summary(_session, _tenant, _agent_id):
            return {"count": 1, "average": 4.0}

        monkeypatch.setattr(reliability, "metrics", _metrics)
        monkeypatch.setattr(reliability, "rating_summary", _summary)

        def install(*answers):
            holder["session"] = _Session(*answers)
            return holder["session"]

        return install

    def test_reliability_is_read_for_a_window(self, store):
        agent = _agent()
        store(agent)
        result = asyncio.run(
            api.get_agent_reliability(agent.id, days=90, tenant_id=str(TENANT), user_domains=None, caller=None)
        )
        assert result["reliability"] == {"window_days": 90, "runs": 1} and result["rating"]["count"] == 1
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                api.get_agent_reliability(agent.id, days=0, tenant_id=str(TENANT), user_domains=None, caller=None)
            )
        assert refused.value.status_code == 422

    def test_a_signed_in_user_rates_and_an_api_key_cannot(self, store):
        agent = _agent()
        session = store(agent, None)
        result = asyncio.run(
            api.rate_agent(
                agent.id,
                AgentRatingIn(score=5, comment="Reliable"),
                tenant_id=str(TENANT),
                user={"agenticorg:user_id": str(USER)},
                user_domains=None,
                caller=None,
            )
        )
        [rating] = session.added
        assert isinstance(rating, AgentRating) and rating.user_id == USER and result["score"] == 5
        assert result["rating"] == {"count": 1, "average": 4.0}
        store(agent)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                api.rate_agent(
                    agent.id, AgentRatingIn(score=5), tenant_id=str(TENANT), user={}, user_domains=None, caller=None
                )
            )
        assert refused.value.status_code == 403

    def test_off(self, monkeypatch):
        assert settings.agent_registry_enabled is False
        with pytest.raises(HTTPException) as refused:
            asyncio.run(api.get_agent_reliability(uuid.uuid4(), tenant_id=str(TENANT), user_domains=None, caller=None))
        assert refused.value.status_code == 409


class TestMigration:
    def test_one_rating_per_user_and_agent_under_row_level_security(self):
        src = (ROOT / "migrations" / "versions" / "v6_z49_agent_ratings.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z48_agent_registry"' in src
        assert "ux_agent_ratings_agent_user ON agent_ratings(agent_id, user_id)" in src
        assert "FORCE ROW LEVEL SECURITY" in src and "score >= 1 AND score <= 5" in src
