"""Root-cause tests for TC_004 chat honesty + chat-history isolation.

Codex 2026-04-22 review flagged two problems in ``api/v1/chat.py``:

1. When the LangGraph run failed or produced nothing, the endpoint
   fabricated a plausible-looking "[AgentName] I've analyzed your
   query..." response and forced confidence to ``0.6``/``0.7``. That
   made the UI pretend an agent had reasoned about the query when in
   reality no grounded answer existed.

2. Chat history was keyed by ``tenant:company_id`` only — switching
   between agents within the same company leaked one agent's chat into
   the other's sidebar.

The tests below pin the root-cause fixes without touching Redis or the
database.
"""

from __future__ import annotations

import inspect

from api.v1 import chat as chat_mod


class TestSessionKeyIsolation:
    def test_session_key_is_scoped_by_agent_when_provided(self) -> None:
        a = chat_mod._session_key("t1", "c1", "agent-one")
        b = chat_mod._session_key("t1", "c1", "agent-two")
        assert a != b, (
            "Different agents must land in different chat buckets, "
            "otherwise switching agents shows the wrong history."
        )

    def test_session_key_is_legacy_bucket_without_agent(self) -> None:
        """Defensive: callers that don't pass an agent_id (legacy
        clients, admin tooling) must still read from the same bucket
        they always used, so we don't orphan historical sessions."""
        assert chat_mod._session_key("t1", "c1") == "t1:c1"
        assert chat_mod._session_key("t1", "c1", "") == "t1:c1"

    def test_session_key_tenant_and_company_bound(self) -> None:
        """Cross-tenant leak would be catastrophic. Keys must differ by
        every axis."""
        assert chat_mod._session_key("t1", "c1", "a1") != chat_mod._session_key("t2", "c1", "a1")
        assert chat_mod._session_key("t1", "c1", "a1") != chat_mod._session_key("t1", "c2", "a1")


class TestChatHistoryEndpointAcceptsAgentId:
    def test_history_signature_accepts_agent_id(self) -> None:
        """Pin the contract: the GET /chat/history route must accept
        ``agent_id`` so the write-side ``tenant:company:agent`` bucket
        is readable from the UI. Dropping this parameter would re-
        introduce the isolation bug."""
        sig = inspect.signature(chat_mod.chat_history)
        assert "agent_id" in sig.parameters
        assert "company_id" in sig.parameters


class TestNoCannedFallbackInSource:
    """Stronger than a behavioural test: grep for the fabricated-answer
    pattern that Codex flagged. If a future change reintroduces it this
    test fails immediately."""

    def test_source_has_no_i_have_analyzed_your_query_string(self) -> None:
        src = inspect.getsource(chat_mod.chat_query)
        # The specific bogus-answer template we just removed.
        assert "I've analyzed your query about" not in src, (
            "The canned fallback response is back — whoever added it "
            "should read the Codex 2026-04-22 review before shipping."
        )

    def test_source_no_longer_forces_confidence_to_0_7(self) -> None:
        """The old fallback forced confidence = 0.7 on a fabricated
        answer. The honest no-answer path sets confidence = 0.0.
        Make sure the 0.7 magic number isn't smuggled back in as a
        default for the no-answer case."""
        src = inspect.getsource(chat_mod.chat_query)
        # We allow 0.7 elsewhere (e.g., in tool-use adjustments), but
        # not immediately after a ``not answer`` branch.
        lines = src.splitlines()
        for idx, line in enumerate(lines):
            if "if not answer:" in line:
                window = "\n".join(lines[idx : idx + 20])
                assert "confidence = 0.7" not in window, (
                    "Canned-confidence pattern detected in no-answer "
                    "branch — confidence should drop to 0.0 to signal "
                    "the absence of a grounded answer."
                )
