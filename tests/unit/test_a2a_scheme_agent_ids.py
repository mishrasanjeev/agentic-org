# SPDX-License-Identifier: Apache-2.0
"""AgenticOrg's own scheme agent ids in its A2A agent card (G51).

AgenticOrg serves no ``/.well-known/agent-registry.json`` (that document is the
Grantex registry's); its public agent description is the A2A agent card. With
``AGENTICORG_A2A_SCHEME_AGENT_IDS`` set, the card carries them as an A2A
``AgentExtension``; unset (the default) the card is unchanged.
"""

from __future__ import annotations

import json

import pytest

from api.v1 import a2a_interop
from core.config import settings

IDS = [{"scheme": "visa_tap", "agent_id": "tap-agent-0001"}, {"scheme": "mastercard_agent_pay", "agent_id": "ap-1"}]


def test_the_card_is_unchanged_while_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "a2a_scheme_agent_ids", "")
    assert a2a_interop._card()["capabilities"] == {
        "streaming": False,
        "pushNotifications": False,
        "extendedAgentCard": True,
    }


def test_configured_scheme_agent_ids_are_published_as_an_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "a2a_scheme_agent_ids", json.dumps(IDS))
    [extension] = a2a_interop._card()["capabilities"]["extensions"]
    assert extension["uri"] == a2a_interop.SCHEME_AGENT_IDS_EXTENSION
    assert extension["required"] is False
    assert extension["params"] == {"scheme_agent_ids": IDS}
    # The seller-specific card publishes them too.
    assert a2a_interop._card(merchant_name="Merchant")["capabilities"]["extensions"] == [extension]


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"scheme": "visa_tap", "agent_id": "x"}),
        json.dumps([{"scheme": "visa", "agent_id": "x"}]),
        json.dumps([{"scheme": "visa_tap", "agent_id": ""}]),
        json.dumps([{"scheme": "visa_tap", "agent_id": "x" * 257}]),
        json.dumps([{"scheme": "visa_tap", "agent_id": "x", "extra": 1}]),
        json.dumps([*IDS, {"scheme": "visa_tap"}]),
    ],
)
def test_a_malformed_setting_publishes_nothing(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setattr(settings, "a2a_scheme_agent_ids", raw)
    assert "extensions" not in a2a_interop._card()["capabilities"]
