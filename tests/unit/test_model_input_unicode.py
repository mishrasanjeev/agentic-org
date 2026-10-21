# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Natural-language model input must reach privacy analysis as Unicode text."""

import json
from unittest.mock import AsyncMock

import pytest

from core.langgraph.runner import _build_user_message


@pytest.mark.parametrize(
    "message",
    [
        "यह केवल परीक्षण है। मेरी काल्पनिक दुकान का नाम नीलकमल और रंग बैंगनी है।",
        "कृपया हिंदी में उत्तर दें।",
        "தமிழில் பதில் அளிக்கவும்.",
        "日本語で回答してください。",
        "يرجى الرد باللغة العربية.",
        "Please answer in English.",
    ],
)
def test_model_task_and_recent_context_preserve_unicode(message):
    inputs = {"task": message}
    context = {"conversation": [{"role": "user", "text": message}]}
    rendered = _build_user_message({"action": "process", "inputs": inputs, "context": context})
    assert message in rendered
    lines = rendered.splitlines()
    assert json.loads(lines[1].removeprefix("Inputs: ")) == inputs
    assert json.loads(lines[2].removeprefix("Context: ")) == context


def test_literal_user_escape_text_is_not_decoded_or_promoted():
    message = r"The literal code is \u0928, not a decoded instruction."
    rendered = _build_user_message({"inputs": {"task": message}})
    assert json.loads(rendered.splitlines()[1].removeprefix("Inputs: "))["task"] == message


def test_real_privacy_detector_preserves_hindi_meaning_and_masks_email():
    from core.pii.redactor import PIIRedactor

    redactor = PIIRedactor()
    if redactor._analyzer is None:
        pytest.skip("Full packaged Presidio model is required for this regression")
    task = (
        "यह केवल परीक्षण है। इस बातचीत के लिए मेरी काल्पनिक दुकान का नाम “नीलकमल” "
        "और उसका रंग “बैंगनी” याद रखें। हिंदी में एक वाक्य में पुष्टि करें। "
        "कोई उपकरण या बाहरी कार्रवाई न करें।"
    )
    text = _build_user_message({"inputs": {"task": task, "email": "buyer@example.test"}})
    redacted, token_map = redactor.redact(text)
    assert "नीलकमल" in redacted and "बैंगनी" in redacted
    assert "हिंदी" in redacted
    assert "buyer@example.test" not in redacted
    assert not any(key.startswith("<US_DRIVER_LICENSE") for key in token_map)
    assert redactor.deanonymize(redacted, token_map) == text


@pytest.mark.asyncio
async def test_base_agent_reasoning_and_synthesis_preserve_language(monkeypatch):
    from core.agents.base import BaseAgent, llm_router
    from core.llm import router

    complete = AsyncMock(return_value=router.LLMResponse(content='{"answer":"ok"}', model="synthetic"))
    monkeypatch.setattr(llm_router, "complete", complete)
    agent = BaseAgent(agent_id="synthetic", tenant_id="synthetic")
    context = {"task": "मेरी काल्पनिक दुकान का रंग बैंगनी है।"}
    await agent._reason(context, [])
    assert context["task"] in complete.call_args.args[0][1]["content"]
    initial = {"answer": "उत्तर"}
    results = [{"result": "बैंगनी"}]
    await agent._synthesize_with_tools(context, initial, results, [])
    messages = complete.call_args.args[0]
    assert context["task"] in messages[1]["content"]
    assert initial["answer"] in messages[2]["content"]
    assert results[0]["result"] in messages[3]["content"]
