# SPDX-License-Identifier: Apache-2.0
"""Query transformation and agentic retrieval: the plan names its rules, the trace shows every step."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from api.v1 import knowledge
from core.config import settings
from core.rag import query as q

ROOT = Path(__file__).resolve().parents[2]


class TestPlan:
    def test_normalising_collapses_whitespace_quotes_and_length(self):
        assert q.normalise('  "locker   rent"  ') == "locker rent"
        assert q.normalise(None) == ""
        assert len(q.normalise("x" * 5000)) == q.MAX_QUERY_CHARS

    def test_the_keyword_form_drops_the_lead_in_and_the_stop_words(self):
        assert q.keyword_form("Can you tell me what the locker rent is?") == "locker rent"
        assert q.keyword_form("What is the penalty for late EMI payment") == "penalty late emi payment"
        assert q.keyword_form("locker rent") == "locker rent"
        assert q.keyword_form("home loan vs personal loan") == "home loan personal loan"

    @pytest.mark.parametrize(
        ("text", "parts", "rule"),
        [
            (
                "What is the locker rent? Who can open a locker?",
                ["What is the locker rent", "Who can open a locker"],
                "sentence",
            ),
            (
                "difference between savings account and current account",
                ["savings account", "current account"],
                "difference",
            ),
            ("compare term deposit with recurring deposit", ["term deposit", "recurring deposit"], "compare"),
            ("home loan vs personal loan", ["home loan", "personal loan"], "versus"),
            (
                "what is the locker rent and who can open a locker",
                ["what is the locker rent", "who can open a locker"],
                "conjunction",
            ),
        ],
    )
    def test_a_compound_query_is_decomposed_by_a_named_rule(self, text, parts, rule):
        decomposed = q.decompose(text)
        assert [p for p, _ in decomposed] == parts and {r for _, r in decomposed} == {rule}

    def test_a_single_question_is_not_decomposed(self):
        assert q.decompose("What is the locker rent?") == []
        assert q.decompose("bread and butter") == []

    def test_the_plan_lists_variants_with_their_rules_and_is_bounded(self):
        planned = q.plan("Can you tell me the difference between savings account and current account?")
        assert planned.normalised == "Can you tell me the difference between savings account and current account?"
        assert planned.variants == [
            "savings account",
            "current account",
            "difference between savings account current account",
        ]
        assert planned.rules == ["difference", "difference", "keywords"]
        many = q.plan("a b c? d e f? g h i? j k l? m n o? p q r?")
        assert len(many.variants) == q.MAX_VARIANTS
        # A part with fewer than two terms is not a variant; the keyword form of the whole query still is.
        single = q.plan("locker? rent?")
        assert single.variants == ["locker rent"] and single.rules == ["keywords"]
        # A variant already planned is not added twice.
        assert q.plan("home loan vs personal loan").variants == [
            "home loan",
            "personal loan",
            "home loan personal loan",
        ]
        assert planned.as_dict()["rules"] == planned.rules

    def test_off_by_default(self):
        assert settings.knowledge_query_transform_enabled is False and q.enabled() is False
        assert settings.knowledge_query_rewrite_model == ""


class TestModelVariants:
    @pytest.mark.asyncio
    async def test_without_a_model_nothing_is_asked(self):
        asked = []

        async def _complete(*args):
            asked.append(args)
            return {"content": "[]"}

        assert await q.model_variants(uuid.uuid4(), "locker rent", complete=_complete) == ([], "no_model")
        assert asked == []

    @pytest.mark.asyncio
    async def test_a_model_answer_is_parsed_bounded_and_a_failure_adds_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "knowledge_query_rewrite_model", "openai/gpt-test")
        seen = []

        async def _complete(tenant_id, model, messages, max_tokens):
            seen.append((model, messages[-1]["content"], max_tokens))
            return {
                "content": 'Sure: ["safe deposit locker charges", "locker fee", "", 7, "annual locker rent", "x y"]'
            }

        variants, reason = await q.model_variants(uuid.uuid4(), "  locker rent ", complete=_complete)
        assert variants == ["safe deposit locker charges", "locker fee", "annual locker rent"] and reason is None
        assert seen == [("openai/gpt-test", "locker rent", q.MODEL_MAX_TOKENS)]

        async def _broken(*args):
            raise RuntimeError("provider down")

        assert await q.model_variants(uuid.uuid4(), "locker rent", complete=_broken) == ([], "model_failed")

        async def _prose(*args):
            return {"content": "I cannot help with that."}

        assert await q.model_variants(uuid.uuid4(), "locker rent", complete=_prose) == ([], "model_unusable")


def _hit(name, text, score):
    return {"document_name": name, "chunk_text": text, "score": score}


def _key(hit):
    return (hit["document_name"], hit["chunk_text"])


def _rescore(hit, score):
    return {**hit, "score": score}


class TestRetrieve:
    @pytest.mark.asyncio
    async def test_a_strong_first_pass_is_accepted_and_traced(self):
        planned = q.plan("home loan vs personal loan")
        searched = []

        async def _search(text):
            searched.append(text)
            return [_hit("Loans", "home loan rates", 0.9), _hit("Loans", "personal loan rates", 0.8)]

        results, trace = await q.retrieve(
            planned, 2, _search, key=_key, score_of=lambda h: h["score"], rescore=_rescore
        )
        assert searched == ["home loan vs personal loan"] and len(results) == 2
        assert [s["stage"] for s in trace.steps] == ["plan", "search", "decision"]
        assert trace.steps[2]["action"] == "accept" and "best score 0.90" in trace.steps[2]["reason"]
        assert trace.steps[1] == {
            "stage": "search",
            "elapsed_ms": trace.steps[1]["elapsed_ms"],
            "query": "home loan vs personal loan",
            "hits": 2,
            "best": 0.9,
        }

    @pytest.mark.asyncio
    async def test_a_weak_first_pass_expands_to_the_variants_and_fuses(self):
        planned = q.plan("home loan vs personal loan")
        answers = {
            "home loan vs personal loan": [_hit("Loans", "a comparison", 0.2)],
            "home loan": [_hit("Loans", "home loan rates", 0.9), _hit("Loans", "a comparison", 0.5)],
            "personal loan": [_hit("Loans", "personal loan rates", 0.9)],
            "home loan personal loan": [_hit("Loans", "a comparison", 0.7)],
        }

        async def _search(text):
            return list(answers[text])

        results, trace = await q.retrieve(
            planned, 3, _search, key=_key, score_of=lambda h: h["score"], rescore=_rescore
        )
        # The chunk found by three of the four searches comes first; scores are fused, not the lists' own.
        assert [r["chunk_text"] for r in results] == ["a comparison", "home loan rates", "personal loan rates"]
        assert results[0]["score"] > results[1]["score"] >= results[2]["score"]
        stages = [s["stage"] for s in trace.steps]
        assert stages == ["plan", "search", "decision", "search", "search", "search", "fuse"]
        assert trace.steps[2] == {
            "stage": "decision",
            "elapsed_ms": trace.steps[2]["elapsed_ms"],
            "action": "expand",
            "reason": "1 of 3 hits",
        }
        assert trace.steps[-1]["lists"] == 4 and trace.steps[-1]["candidates"] == 3 and trace.steps[-1]["returned"] == 3
        assert trace.as_dict()["steps"][0]["variants"] == planned.variants

    @pytest.mark.asyncio
    async def test_a_weak_pass_without_variants_is_returned_as_it_is(self):
        planned = q.plan("locker rent")
        assert planned.variants == []

        async def _search(_text):
            return [_hit("Fees", "locker rent is 1200", 0.1)]

        results, trace = await q.retrieve(
            planned, 5, _search, key=_key, score_of=lambda h: h["score"], rescore=_rescore
        )
        assert (
            len(results) == 1 and trace.steps[-1]["action"] == "accept" and "no variants" in trace.steps[-1]["reason"]
        )

    def test_fusion_scores_one_for_a_hit_that_tops_every_list(self):
        top = _hit("A", "x", 0.1)
        fused = q.fuse([[top, _hit("A", "y", 0.1)], [top]], 5, key=_key, rescore=_rescore)
        assert fused[0]["score"] == 1.0 and fused[1]["chunk_text"] == "y"

    def test_the_trace_names_no_tenant_or_user(self):
        src = (ROOT / "core" / "rag" / "query.py").read_text(encoding="utf-8")
        body = src[src.index("class Trace") :]
        assert "tenant" not in body.split("def is_weak")[0].replace("never tenant", "").replace(
            "identifies a tenant or user", ""
        )


class TestSearchPath:
    @pytest.mark.asyncio
    async def test_off_the_search_runs_as_before_and_carries_no_trace(self, monkeypatch):
        calls = []

        async def _native(tenant_id, query, top_k, filters=None, domains=None):
            calls.append(query)
            return [knowledge.SearchResult(chunk_text="t", score=0.1, document_name="D")]

        async def _guard(_tenant, results):
            return results

        monkeypatch.setattr(knowledge, "_native_semantic_search", _native)
        monkeypatch.setattr(knowledge, "_guard_results", _guard)
        monkeypatch.setattr(knowledge, "_ragflow_available", lambda: False)
        response = await knowledge._search_knowledge(
            knowledge.SearchRequest(query="home loan vs personal loan", top_k=3, trace=True), str(uuid.uuid4()), None
        )
        assert calls == ["home loan vs personal loan"] and response.trace is None
        assert "trace" in response.model_dump()

    @pytest.mark.asyncio
    async def test_on_a_weak_search_expands_and_the_response_carries_the_trace_when_asked(self, monkeypatch):
        monkeypatch.setattr(settings, "knowledge_query_transform_enabled", True)
        calls = []

        async def _native(tenant_id, query, top_k, filters=None, domains=None):
            calls.append((query, top_k, domains))
            if query == "home loan":
                return [knowledge.SearchResult(chunk_text="home loan rates", score=0.9, document_name="Loans")]
            return [knowledge.SearchResult(chunk_text="a comparison", score=0.2, document_name="Loans")]

        async def _guard(_tenant, results):
            return results

        monkeypatch.setattr(knowledge, "_native_semantic_search", _native)
        monkeypatch.setattr(knowledge, "_guard_results", _guard)
        monkeypatch.setattr(knowledge, "_ragflow_available", lambda: False)
        request = knowledge.SearchRequest(query="home loan vs personal loan", top_k=3, trace=True)
        response = await knowledge._search_knowledge(request, str(uuid.uuid4()), ["finance"])
        assert [c[0] for c in calls] == [
            "home loan vs personal loan",
            "home loan",
            "personal loan",
            "home loan personal loan",
        ]
        assert all(c[1] == 3 and c[2] == ["finance"] for c in calls)
        assert [r.chunk_text for r in response.results] == ["a comparison", "home loan rates"]
        assert response.trace is not None
        assert [s.stage for s in response.trace.steps] == [
            "plan",
            "search",
            "decision",
            "search",
            "search",
            "search",
            "fuse",
        ]
        # Without trace=true the same search answers without the trace.
        quiet = await knowledge._search_knowledge(
            knowledge.SearchRequest(query="home loan vs personal loan", top_k=3), str(uuid.uuid4()), None
        )
        assert quiet.trace is None and len(quiet.results) == 2

    @pytest.mark.asyncio
    async def test_a_configured_model_adds_its_variants_and_its_failure_is_traced(self, monkeypatch):
        monkeypatch.setattr(settings, "knowledge_query_transform_enabled", True)
        monkeypatch.setattr(settings, "knowledge_query_rewrite_model", "openai/gpt-test")

        async def _variants(tenant_id, text, complete=None):
            return ["locker charges"], None

        monkeypatch.setattr(q, "model_variants", _variants)
        calls = []

        async def _native(tenant_id, query, top_k, filters=None, domains=None):
            calls.append(query)
            return []

        async def _guard(_tenant, results):
            return results

        monkeypatch.setattr(knowledge, "_native_semantic_search", _native)
        monkeypatch.setattr(knowledge, "_guard_results", _guard)
        monkeypatch.setattr(knowledge, "_ragflow_available", lambda: False)
        response = await knowledge._search_knowledge(
            knowledge.SearchRequest(query="locker rent", top_k=2, trace=True), str(uuid.uuid4()), None
        )
        assert calls == ["locker rent", "locker charges"]
        assert response.trace is not None and response.trace.steps[0].stage == "rewrite"
        assert response.trace.steps[0].detail == {"model": "openai/gpt-test", "added": 1, "reason": None}
        assert response.trace.steps[1].detail["rules"] == ["model"]

        async def _nothing(tenant_id, text, complete=None):
            return [], "model_failed"

        monkeypatch.setattr(q, "model_variants", _nothing)
        response = await knowledge._search_knowledge(
            knowledge.SearchRequest(query="locker rent", top_k=2, trace=True), str(uuid.uuid4()), None
        )
        assert response.trace is not None
        assert [s.stage for s in response.trace.steps][:2] == ["rewrite", "plan"]
        assert response.trace.steps[0].detail == {"model": "openai/gpt-test", "added": 0, "reason": "model_failed"}
