# SPDX-License-Identifier: Apache-2.0
"""Transcription engines and the transcript they produce: timed words, then turns with a speaker each.

Three ways to words: a local ``faster-whisper`` model where it is
installed (nothing leaves the deployment), a tenant's speech provider
through its own credential (Deepgram today, the resolver's ``stt`` kind),
or words supplied by the caller from a provider of their own. Whatever
the source, the words are aligned to the diarised segments so every
turn carries a speaker, and a word no engine timed falls to the speaker
heard nearest. When no engine is available the recording still gets its
segments and speakers and says ``engine: unavailable``, never a blank
transcript presented as speech.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
import structlog

from core.speech.audio import Recording, SpeechError, encode_wav, resample
from core.speech.segments import Segment

logger = structlog.get_logger()

ENGINES = ("faster_whisper", "deepgram", "supplied")
WHISPER_RATE = 16000
TURN_GAP = 1.0  # seconds of silence that end a turn within one speaker
MAX_WORDS = 50_000
DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


@dataclass
class Word:
    text: str
    start: float
    end: float
    confidence: float = 1.0
    speaker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "confidence": round(self.confidence, 3),
            "speaker": self.speaker,
        }


def whisper_available() -> bool:
    try:
        import faster_whisper  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


def engines_available() -> dict[str, bool]:
    return {"faster_whisper": whisper_available(), "deepgram": True, "supplied": True}


def check_words(raw: Any) -> list[Word]:
    """Words a caller supplies: text, start and end in seconds, an optional confidence."""
    if not isinstance(raw, list) or len(raw) > MAX_WORDS:
        raise SpeechError(422, "words_invalid", f"words is a list of up to {MAX_WORDS} timed words")
    out: list[Word] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise SpeechError(422, "words_invalid", "each word is an object with text, start and end")
        text = str(entry.get("text") or entry.get("word") or "").strip()
        try:
            start = float(entry.get("start"))
            end = float(entry.get("end"))
        except (TypeError, ValueError):
            raise SpeechError(422, "words_invalid", f"word {text!r} needs a start and an end in seconds") from None
        if not text or start < 0 or end < start:
            raise SpeechError(422, "words_invalid", f"word {text!r} has an empty text or an impossible time")
        try:
            confidence = float(entry.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        speaker = entry.get("speaker")
        out.append(
            Word(
                text=text[:100],
                start=start,
                end=end,
                confidence=max(0.0, min(1.0, confidence)),
                speaker=str(speaker) if speaker not in (None, "") else None,
            )
        )
    return sorted(out, key=lambda w: w.start)


def transcribe_whisper(recording: Recording, *, language: str = "en", model_size: str = "base") -> list[Word]:
    """Timed words from a local faster-whisper model over the mono mix."""
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    audio = resample(recording.mono, recording.sample_rate, WHISPER_RATE)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio, language=language or None, word_timestamps=True, vad_filter=True)
    words: list[Word] = []
    for segment in segments:
        for word in getattr(segment, "words", None) or []:
            text = str(word.word).strip()
            if text:
                words.append(
                    Word(
                        text=text,
                        start=float(word.start),
                        end=float(word.end),
                        confidence=float(getattr(word, "probability", 1.0)),
                    )
                )
    return words


async def transcribe_deepgram(tenant_id: uuid.UUID, recording: Recording, *, language: str = "en") -> list[Word]:
    """Timed words from the tenant's Deepgram credential; the provider's speaker labels are kept beside."""
    import httpx

    from core.ai_providers.resolver import ProviderNotConfigured, get_provider_credential
    from core.spend import context as spend_context

    try:
        credential = await get_provider_credential(tenant_id, "stt_deepgram", "stt")
    except ProviderNotConfigured:
        raise SpeechError(
            503, "engine_not_configured", "No Deepgram credential is configured for this tenant"
        ) from None
    # AI spend: whose key pays for these minutes (the tenant's or the platform's); a no-op while off.
    spend_context.note_credential("deepgram", getattr(credential, "source", ""))
    params = {"model": "nova-2", "punctuate": "true", "diarize": "true", "smart_format": "false"}
    if language:
        params["language"] = language
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                DEEPGRAM_URL,
                params=params,
                headers={"Authorization": f"Token {credential.secret}", "Content-Type": "audio/wav"},
                content=encode_wav(recording),
            )
    except httpx.HTTPError as exc:
        raise SpeechError(
            502, "engine_failed", f"The speech provider could not be reached: {type(exc).__name__}"
        ) from None
    if response.status_code >= 400:
        raise SpeechError(502, "engine_failed", f"The speech provider answered {response.status_code}")
    try:
        payload = response.json()
        channels = payload["results"]["channels"]
        raw_words = channels[0]["alternatives"][0].get("words", [])
    except (ValueError, KeyError, IndexError, TypeError):
        raise SpeechError(502, "engine_failed", "The speech provider's answer had no words") from None
    words: list[Word] = []
    for entry in raw_words:
        text = str(entry.get("punctuated_word") or entry.get("word") or "").strip()
        if not text:
            continue
        speaker = entry.get("speaker")
        words.append(
            Word(
                text=text,
                start=float(entry.get("start", 0.0)),
                end=float(entry.get("end", 0.0)),
                confidence=float(entry.get("confidence", 1.0)),
                speaker=f"provider_{speaker}" if speaker is not None else None,
            )
        )
    return words


async def transcribe(tenant_id: uuid.UUID, recording: Recording, *, engine: str, language: str = "en") -> list[Word]:
    """Words from the named engine; ``supplied`` has none to give and ``faster_whisper`` needs the package."""
    if engine == "faster_whisper":
        if not whisper_available():
            raise SpeechError(503, "engine_unavailable", "faster-whisper is not installed in this deployment")
        # Model loading and inference are CPU bound: a worker thread keeps the event loop serving.
        return await asyncio.to_thread(transcribe_whisper, recording, language=language)
    if engine == "deepgram":
        return await transcribe_deepgram(tenant_id, recording, language=language)
    if engine == "supplied":
        return []
    raise SpeechError(422, "engine_unknown", f"engine is one of {', '.join(ENGINES)}")


def assign_speakers(words: list[Word], segments: list[Segment]) -> list[Word]:
    """Each word takes the speaker of the segment that holds its middle, else of the nearest segment."""
    if not segments:
        return words
    starts = np.array([s.start for s in segments])
    ends = np.array([s.end for s in segments])
    for word in words:
        middle = (word.start + word.end) / 2.0
        inside = np.where((starts <= middle) & (ends >= middle))[0]
        if len(inside):
            word.speaker = segments[int(inside[0])].speaker
            continue
        distance = np.minimum(np.abs(starts - middle), np.abs(ends - middle))
        word.speaker = segments[int(np.argmin(distance))].speaker
    return words


def turns_of(words: list[Word]) -> list[dict[str, Any]]:
    """Consecutive words of one speaker, split at a long pause, as turns with text, times and confidence."""
    turns: list[dict[str, Any]] = []
    current: list[Word] = []

    def close() -> None:
        if current:
            turns.append(
                {
                    "speaker": current[0].speaker,
                    "start": round(current[0].start, 3),
                    "end": round(current[-1].end, 3),
                    "text": " ".join(w.text for w in current),
                    "confidence": round(sum(w.confidence for w in current) / len(current), 3),
                    "words": len(current),
                }
            )
        current.clear()

    for word in sorted(words, key=lambda w: w.start):
        if current and (word.speaker != current[-1].speaker or word.start - current[-1].end > TURN_GAP):
            close()
        current.append(word)
    close()
    return turns


def transcript_of(words: list[Word], segments: list[Segment]) -> dict[str, Any]:
    """Words aligned to the speakers and grouped into turns, with the plain text beside."""
    aligned = assign_speakers(words, segments)
    turns = turns_of(aligned)
    return {
        "words": [w.to_dict() for w in aligned],
        "turns": turns,
        "text": "\n".join(f"{t['speaker']}: {t['text']}" for t in turns),
        "word_count": len(aligned),
        "confidence": round(sum(w.confidence for w in aligned) / len(aligned), 3) if aligned else None,
    }
