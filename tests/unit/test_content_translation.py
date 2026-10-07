# SPDX-License-Identifier: Apache-2.0
"""Content services, part 3: translation checks (figures, glossary, verbatim terms, script) and routes."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.content import services, translation

TENANT = uuid.uuid4()
HINDI = "शाखा 2 अक्टूबर 2026 को बंद रहेगी। ATM खुले रहेंगे। NetBanking उपलब्ध है।"


class _Reply:
    def __init__(self, payload, *, tokens=5, model="m"):
        self.content = json.dumps(payload, ensure_ascii=False)
        self.tokens_used = tokens
        self.model = model


def _completer(*replies):
    queue = list(replies)
    calls: list[list[dict[str, str]]] = []

    async def complete(_tenant, _model, messages, _max_tokens):
        calls.append(list(messages))
        return queue.pop(0)

    complete.calls = calls  # type: ignore[attr-defined]
    return complete


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "content_services_enabled", True)
    monkeypatch.setattr(settings, "content_services_model", "")
    import core.governance.guardrails.hooks as hooks

    monkeypatch.setattr(hooks, "guard_text", AsyncMock(return_value=None))


class TestChecks:
    def test_languages_and_scripts(self):
        assert (
            translation.check_language("HI") == "hi" and translation.check_language("auto", allow_auto=True) == "auto"
        )
        with pytest.raises(services.ContentError) as info:
            translation.check_language("fr")
        assert info.value.code == "language_unsupported"
        assert translation.script_share(HINDI, "hi", ignore=["ATM", "NetBanking"]) == 1.0
        assert translation.script_share("The branch is closed", "hi") == 0.0
        assert translation.script_share("", "ta") == 0.0

    def test_word_overlap_is_jaccard_over_content_words(self):
        assert translation.word_overlap("the branch is closed", "The branch is closed") == 1.0
        assert 0 < translation.word_overlap("the branch is closed", "the branch stays open") < 1
        assert translation.word_overlap("", "") == 1.0

    @pytest.mark.asyncio
    async def test_a_good_translation_passes_every_check(self, on):
        payload = translation.TranslateIn(
            text="The branch will remain closed on 2 October 2026. ATMs stay open. NetBanking is available.",
            target_language="hi",
            preserve=["NetBanking", "ATM"],
            glossary=[{"term": "branch", "translation": "शाखा"}],
        )
        answer = {"translation": HINDI, "detected_source_language": "en", "notes": []}
        complete = _completer(_Reply(answer))
        run = await services.run(translation.SERVICE, TENANT, payload, complete=complete)
        out = run.output
        assert out["trusted"] is True and out["checks"]["script_share"] == 1.0 and out["checks"]["missing_facts"] == []
        assert out["target_language"] == "hi" and out["detected_source_language"] == "en"
        prompt = complete.calls[0][1]["content"]
        assert (
            "Devanagari" in prompt and "- branch -> शाखा" in prompt and "NetBanking" in complete.calls[0][0]["content"]
        )

    @pytest.mark.asyncio
    async def test_a_translation_that_drops_a_figure_or_a_glossary_term_or_the_script_is_not_trusted(self, on):
        payload = translation.TranslateIn(
            text="A charge of ₹150 applies when the quarterly average balance is below ₹10,000. Use NetBanking.",
            target_language="hi",
            preserve=["NetBanking"],
            glossary=[{"term": "charge", "translation": "शुल्क"}],
        )
        answer = {"translation": "The quarterly average balance must be 10000. Net banking.", "notes": []}
        run = await services.run(translation.SERVICE, TENANT, payload, complete=_completer(_Reply(answer)))
        checks = run.output["checks"]
        assert run.output["trusted"] is False
        assert checks["missing_facts"] == ["150"] and checks["glossary_misses"] == ["charge"]
        assert checks["preserve_misses"] == ["NetBanking"] and checks["script_ok"] is False

    @pytest.mark.asyncio
    async def test_back_translation_reports_the_overlap(self, on):
        payload = translation.TranslateIn(
            text="The branch will remain closed on 2 October 2026.", target_language="hi", verify=True
        )
        output = {"translation": HINDI, "detected_source_language": "en"}
        complete = _completer(
            _Reply({"translation": "The branch will remain closed on 2 October 2026. ATMs stay open."})
        )
        result = await translation.back_translate(TENANT, payload, output, complete=complete)
        assert result["overlap"] > 0.5 and "October" in result["back_translation"]
        assert "English" in complete.calls[0][1]["content"]

    def test_dataset_and_catalogue(self):
        described = translation.SERVICE.describe()
        assert described["name"] == "translate" and described["dataset"]["cases"] == 3
        assert described["guardrail_profile"]["grounded"] is True


class TestRoutes:
    @pytest.mark.asyncio
    async def test_languages_are_listed_and_unknown_ones_refused(self, on):
        from api.v1 import content_translation as api

        listed = await api.list_languages(tenant_id=str(TENANT))
        assert any(item["code"] == "ta" and item["script"] == "Tamil" for item in listed["languages"])
        with pytest.raises(HTTPException) as info:
            await api.post_translate(
                translation.TranslateIn(text="x", target_language="fr"), tenant_id=str(TENANT), domains=None
            )
        assert info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_a_batch_translates_each_item_and_reports_failures_in_place(self, on, monkeypatch):
        from api.v1 import content_translation as api

        async def run(service, tenant_id, payload, *, domains=None, complete=None):
            if "fail" in payload.text:
                raise services.ContentError(502, "model_failed", "no answer")
            return services.Run(
                service="translate", output={"translation": HINDI, "trusted": True}, sources=[], guardrails={}, model={}
            )

        monkeypatch.setattr(services, "run", run)
        body = api.BatchIn(
            items=[{"id": "a", "text": "ok one"}, {"id": "b", "text": "fail two"}],
            target_language="hi",
            register="odd",
            format="plain",
        )
        answer = await api.post_translate_batch(body, tenant_id=str(TENANT), domains=None)
        assert answer["total"] == 2 and answer["failed"] == 1
        assert answer["results"][0]["ok"] is True and answer["results"][0]["output"]["trusted"] is True
        assert answer["results"][1] == {"id": "b", "ok": False, "error": "model_failed", "message": "no answer"}

    @pytest.mark.asyncio
    async def test_verify_adds_the_back_translation(self, on, monkeypatch):
        from api.v1 import content_translation as api

        monkeypatch.setattr(
            services,
            "run",
            AsyncMock(
                return_value=services.Run(
                    service="translate", output={"translation": HINDI}, sources=[], guardrails={}, model={}
                )
            ),
        )
        monkeypatch.setattr(
            translation,
            "back_translate",
            AsyncMock(return_value={"back_translation": "x", "overlap": 0.8, "model": {}}),
        )
        answer = await api.post_translate(
            translation.TranslateIn(text="The branch", target_language="hi", verify=True),
            tenant_id=str(TENANT),
            domains=None,
        )
        assert answer["verification"]["overlap"] == 0.8

    @pytest.mark.asyncio
    async def test_off_means_not_found(self, monkeypatch):
        from api.v1 import content_translation as api

        monkeypatch.setattr(settings, "content_services_enabled", False)
        with pytest.raises(HTTPException) as info:
            await api.post_translate_batch(
                api.BatchIn(items=[{"id": "a", "text": "t"}], target_language="hi"), tenant_id=str(TENANT), domains=None
            )
        assert info.value.status_code == 404
        request = SimpleNamespace()
        assert request is not None
