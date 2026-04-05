"""LiveKit voice agent bridge — connects LiveKit agent events to AgenticOrg agents.

This module provides a ``VoiceAgentWorker`` that:
1. Receives transcribed text (STT output) from a LiveKit voice session.
2. Dispatches it to the AgenticOrg LangGraph agent runner.
3. Returns the agent's text response for TTS synthesis.

All LiveKit imports are guarded so the module can be imported without
``livekit-agents`` installed.  Install with ``pip install agenticorg[v4]``.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Guarded LiveKit imports
# ---------------------------------------------------------------------------
try:
    from livekit.agents import (  # type: ignore[import-untyped]
        AutoSubscribe,
        JobContext,
        WorkerOptions,
        cli,
    )

    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False
    AutoSubscribe = None  # type: ignore[assignment,misc]
    JobContext = None  # type: ignore[assignment,misc]
    WorkerOptions = None  # type: ignore[assignment,misc]
    cli = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# VoiceAgentWorker
# ---------------------------------------------------------------------------
class VoiceAgentWorker:
    """Bridge between LiveKit voice sessions and AgenticOrg agent execution.

    Parameters
    ----------
    agent_config : dict
        Agent configuration containing at minimum ``agent_id``, ``agent_type``,
        ``domain``, ``tenant_id``, ``system_prompt``, ``authorized_tools``.
    grant_token : str
        Grantex JWT for scope enforcement on tool calls.
    """

    def __init__(self, agent_config: dict[str, Any], grant_token: str = "") -> None:
        self.agent_config = agent_config
        self.grant_token = grant_token
        self._thread_id: str | None = None

    async def handle_call(
        self,
        session: Any,
        user_text: str,
    ) -> str:
        """Process a single voice turn.

        Receives transcribed text from STT, runs the AgenticOrg agent,
        and returns text for TTS synthesis.

        Parameters
        ----------
        session : Any
            The LiveKit session object (or mock for testing).
        user_text : str
            Transcribed speech from the caller.

        Returns
        -------
        str
            Agent response text to be synthesized via TTS.
        """
        from core.langgraph.runner import run_agent

        cfg = self.agent_config

        task_input = {
            "action": "voice_conversation",
            "inputs": {"transcript": user_text},
            "context": {"channel": "voice", "session_id": str(id(session))},
        }

        result = await run_agent(
            agent_id=cfg.get("agent_id", "voice-agent"),
            agent_type=cfg.get("agent_type", "voice"),
            domain=cfg.get("domain", "general"),
            tenant_id=cfg.get("tenant_id", ""),
            system_prompt=cfg.get("system_prompt", "You are a helpful voice assistant."),
            authorized_tools=cfg.get("authorized_tools", []),
            task_input=task_input,
            grant_token=self.grant_token,
            thread_id=self._thread_id,
        )

        # Persist thread for multi-turn conversations
        if "thread_id" in result:
            self._thread_id = result["thread_id"]

        status = result.get("status", "failed")
        if status == "completed":
            output = result.get("output", {})
            if isinstance(output, dict):
                return output.get("response", output.get("text", str(output)))
            return str(output)

        error = result.get("error", "I encountered an error processing your request.")
        logger.warning(
            "voice_agent_error",
            status=status,
            error=error,
            agent_id=cfg.get("agent_id"),
        )
        return "I'm sorry, I wasn't able to process that. Could you try again?"

    @staticmethod
    def is_available() -> bool:
        """Return True if livekit-agents SDK is installed."""
        return _LIVEKIT_AVAILABLE
