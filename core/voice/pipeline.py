"""Voice pipeline builder — assembles STT -> Agent -> TTS chains.

Default STT: ``faster-whisper`` (Apache 2.0, runs locally).
Default TTS: ``piper-tts`` (MIT, runs locally) or Google Cloud TTS opt-in.

All heavy imports are guarded so this module loads without optional deps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Guarded optional imports
# ---------------------------------------------------------------------------
try:
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    _FASTER_WHISPER_AVAILABLE = True
except ImportError:
    _FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None  # type: ignore[assignment,misc]

try:
    import piper  # type: ignore[import-untyped]  # noqa: F401

    _PIPER_AVAILABLE = True
except ImportError:
    _PIPER_AVAILABLE = False

try:
    from google.cloud import texttospeech  # type: ignore[import-untyped]

    _GCLOUD_TTS_AVAILABLE = True
except ImportError:
    _GCLOUD_TTS_AVAILABLE = False

try:
    from livekit.agents import pipeline as lk_pipeline  # type: ignore[import-untyped]  # noqa: F401

    _LIVEKIT_PIPELINE_AVAILABLE = True
except ImportError:
    _LIVEKIT_PIPELINE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Pipeline configuration
# ---------------------------------------------------------------------------
@dataclass
class STTConfig:
    """Speech-to-text configuration."""

    engine: str = "faster-whisper"  # "faster-whisper" | "google" | "custom"
    model_size: str = "base"  # tiny, base, small, medium, large-v3
    language: str = "en"
    device: str = "cpu"  # "cpu" | "cuda"
    compute_type: str = "int8"


@dataclass
class TTSConfig:
    """Text-to-speech configuration."""

    engine: str = "piper"  # "piper" | "google" | "custom"
    voice: str = "en_US-lessac-medium"  # Piper voice model name
    language: str = "en-US"
    speaking_rate: float = 1.0
    sample_rate: int = 22050


@dataclass
class VoicePipelineConfig:
    """Full voice pipeline configuration."""

    stt: STTConfig = field(default_factory=STTConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    agent_config: dict[str, Any] = field(default_factory=dict)
    sip_config: dict[str, Any] = field(default_factory=dict)
    max_turn_duration_s: int = 30
    silence_timeout_s: float = 1.5
    vad_enabled: bool = True


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------
def build_voice_pipeline(
    agent_config: dict[str, Any],
    sip_config: dict[str, Any] | None = None,
    stt_config: STTConfig | None = None,
    tts_config: TTSConfig | None = None,
) -> VoicePipelineConfig:
    """Build a voice pipeline definition.

    This returns a ``VoicePipelineConfig`` describing the STT -> Agent -> TTS
    chain.  The actual pipeline execution happens when a LiveKit session
    starts (see ``VoiceAgentWorker``).

    Parameters
    ----------
    agent_config : dict
        AgenticOrg agent configuration (agent_id, system_prompt, etc.).
    sip_config : dict | None
        SIP trunk configuration (optional for WebRTC-only calls).
    stt_config : STTConfig | None
        Override STT settings.  Defaults to local faster-whisper.
    tts_config : TTSConfig | None
        Override TTS settings.  Defaults to local Piper TTS.

    Returns
    -------
    VoicePipelineConfig
        Complete pipeline definition.
    """
    stt = stt_config or STTConfig()
    tts = tts_config or TTSConfig()

    # Validate STT engine availability
    if stt.engine == "faster-whisper" and not _FASTER_WHISPER_AVAILABLE:
        logger.warning(
            "stt_engine_unavailable",
            engine=stt.engine,
            msg="faster-whisper not installed; pipeline will fail at runtime",
        )

    # Validate TTS engine availability
    if tts.engine == "piper" and not _PIPER_AVAILABLE:
        logger.warning(
            "tts_engine_unavailable",
            engine=tts.engine,
            msg="piper-tts not installed; pipeline will fail at runtime",
        )
    if tts.engine == "google" and not _GCLOUD_TTS_AVAILABLE:
        logger.warning(
            "tts_engine_unavailable",
            engine=tts.engine,
            msg="google-cloud-texttospeech not installed",
        )

    pipeline_config = VoicePipelineConfig(
        stt=stt,
        tts=tts,
        agent_config=agent_config,
        sip_config=sip_config or {},
    )

    logger.info(
        "voice_pipeline_built",
        stt_engine=stt.engine,
        tts_engine=tts.engine,
        agent_id=agent_config.get("agent_id", "unknown"),
    )

    return pipeline_config


# ---------------------------------------------------------------------------
# Runtime helpers (used by VoiceAgentWorker at call time)
# ---------------------------------------------------------------------------
def create_stt_instance(config: STTConfig) -> Any:
    """Create and return an STT engine instance.

    Returns None if the required library is not installed.
    """
    if config.engine == "faster-whisper":
        if not _FASTER_WHISPER_AVAILABLE:
            return None
        return WhisperModel(
            config.model_size,
            device=config.device,
            compute_type=config.compute_type,
        )
    # Other engines return None (placeholder for future providers)
    return None


def create_tts_instance(config: TTSConfig) -> Any:
    """Create and return a TTS engine instance.

    Returns None if the required library is not installed.
    """
    if config.engine == "piper" and _PIPER_AVAILABLE:
        return {"engine": "piper", "voice": config.voice}
    if config.engine == "google" and _GCLOUD_TTS_AVAILABLE:
        return texttospeech.TextToSpeechClient()
    return None
