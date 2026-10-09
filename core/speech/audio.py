# SPDX-License-Identifier: Apache-2.0
"""Recordings as samples: a WAV file decoded into one float array per channel, bounded in size and length.

Only PCM WAV is read here (8, 16, 24 or 32-bit, one or two channels),
through the standard library, so a deployment needs no media binaries.
A compressed recording (MP3, Opus, AAC) is refused with a clear message
rather than a silent failure; a deployment that handles those converts
them to WAV at the edge.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field
from typing import Any

import numpy as np

MAX_BYTES = 50 * 1024 * 1024
MAX_SECONDS = 2 * 60 * 60
MAX_CHANNELS = 2
WAV_MIMES = ("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave")


class SpeechError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Recording:
    sample_rate: int
    channels: list[np.ndarray] = field(default_factory=list)  # one float32 array in [-1, 1] per channel

    @property
    def duration(self) -> float:
        if not self.channels or self.sample_rate <= 0:
            return 0.0
        return float(len(self.channels[0]) / self.sample_rate)

    @property
    def mono(self) -> np.ndarray:
        if not self.channels:
            return np.zeros(0, dtype=np.float32)
        if len(self.channels) == 1:
            return self.channels[0]
        return np.mean(np.stack(self.channels), axis=0).astype(np.float32)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_rate": self.sample_rate,
            "channels": len(self.channels),
            "duration_seconds": round(self.duration, 3),
        }


def is_wav(stream: bytes, mime_type: str) -> bool:
    mime = (mime_type or "").split(";")[0].strip().lower()
    return mime in WAV_MIMES or (stream[:4] == b"RIFF" and stream[8:12] == b"WAVE")


def decode_wav(stream: bytes) -> Recording:
    """The channels of a PCM WAV as float arrays in [-1, 1]."""
    if len(stream) > MAX_BYTES:
        raise SpeechError(413, "too_large", f"The recording is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if not stream:
        raise SpeechError(422, "empty_file", "The recording is empty")
    try:
        with wave.open(io.BytesIO(stream), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
            raw = handle.readframes(frames)
    except (wave.Error, EOFError, ValueError) as exc:
        raise SpeechError(422, "wav_unreadable", f"The recording is not a PCM WAV file: {type(exc).__name__}") from None
    if channels < 1 or channels > MAX_CHANNELS:
        raise SpeechError(422, "channels_unsupported", f"A recording has one or two channels, not {channels}")
    if rate < 8000 or rate > 192000:
        raise SpeechError(422, "sample_rate_unsupported", f"Unsupported sample rate {rate}")
    if frames / rate > MAX_SECONDS:
        raise SpeechError(413, "too_long", f"The recording is longer than {MAX_SECONDS // 3600} hours")
    if width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        as_bytes = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (
            as_bytes[:, 0].astype(np.int32)
            | (as_bytes[:, 1].astype(np.int32) << 8)
            | (as_bytes[:, 2].astype(np.int32) << 16)
        )
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
        samples = ints.astype(np.float32) / float(1 << 23)
    elif width == 4:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise SpeechError(422, "sample_width_unsupported", f"Unsupported sample width {width}")
    usable = (len(samples) // channels) * channels
    frames_array = samples[:usable].reshape(-1, channels)
    return Recording(sample_rate=rate, channels=[np.ascontiguousarray(frames_array[:, c]) for c in range(channels)])


def encode_wav(recording: Recording) -> bytes:
    """A recording back to 16-bit PCM WAV (used by the redaction step to write the audio it changed)."""
    out = io.BytesIO()
    frames = np.stack(recording.channels, axis=1) if recording.channels else np.zeros((0, 1), dtype=np.float32)
    ints = np.clip(frames * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(out, "wb") as handle:
        handle.setnchannels(max(1, len(recording.channels)))
        handle.setsampwidth(2)
        handle.setframerate(recording.sample_rate)
        handle.writeframes(ints.tobytes())
    return out.getvalue()


def resample(samples: np.ndarray, rate: int, target: int) -> np.ndarray:
    """Linear resampling, enough for a speech model's 16 kHz input."""
    if rate == target or len(samples) == 0:
        return samples.astype(np.float32)
    duration = len(samples) / rate
    count = max(1, int(round(duration * target)))
    positions = np.linspace(0, len(samples) - 1, count)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def load(stream: bytes, mime_type: str) -> Recording:
    if not is_wav(stream, mime_type):
        raise SpeechError(
            415,
            "format_unsupported",
            "Give a PCM WAV recording; compressed audio is converted to WAV before upload",
        )
    return decode_wav(stream)
