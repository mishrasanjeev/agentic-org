# SPDX-License-Identifier: Apache-2.0
"""Who spoke when: speech segments from a recording's energy, and speakers from channels or from clustering.

A stereo call recording carries one party per channel, so the channel
is the speaker and the segments of each channel are that party's turns.
A mono recording is split into segments by energy (a voice activity
detector over 20 ms frames with an adaptive floor) and the segments are
grouped into two speakers by their sound (loudness, pitch-related zero
crossings and spectral centroid), which separates a typical agent and
customer pair without a model. Each segment carries its speaker, its
start and end in seconds and its channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.speech.audio import Recording

FRAME_SECONDS = 0.02
MIN_SEGMENT = 0.25  # shorter bursts are noise
MAX_GAP = 0.40  # a pause shorter than this stays inside the segment
ABSOLUTE_FLOOR = 0.01  # RMS below this is silence whatever the recording
SPEAKER_NAMES = ("speaker_1", "speaker_2")


@dataclass
class Segment:
    speaker: str
    start: float
    end: float
    channel: int = 0
    energy: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "channel": self.channel,
            "duration": round(self.duration, 3),
        }


def frame_rms(samples: np.ndarray, rate: int) -> np.ndarray:
    """The RMS of each 20 ms frame."""
    size = max(1, int(rate * FRAME_SECONDS))
    count = len(samples) // size
    if count == 0:
        return np.zeros(0, dtype=np.float32)
    frames = samples[: count * size].reshape(count, size)
    return np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)).astype(np.float32)


def active_frames(rms: np.ndarray) -> np.ndarray:
    """Which frames hold speech: above an adaptive floor between the quiet and the loud frames."""
    if len(rms) == 0:
        return np.zeros(0, dtype=bool)
    quiet = float(np.percentile(rms, 20))
    loud = float(np.percentile(rms, 90))
    threshold = max(ABSOLUTE_FLOOR, quiet + 0.25 * (loud - quiet))
    return rms > threshold


def speech_segments(samples: np.ndarray, rate: int, *, channel: int = 0) -> list[Segment]:
    """Runs of active frames, with short pauses bridged and short bursts dropped."""
    rms = frame_rms(samples, rate)
    active = active_frames(rms)
    segments: list[Segment] = []
    start: int | None = None
    last_active = -1
    gap_frames = int(MAX_GAP / FRAME_SECONDS)
    for index, is_active in enumerate(active):
        if is_active:
            if start is None:
                start = index
            last_active = index
        elif start is not None and index - last_active > gap_frames:
            segments.append(_segment(rms, start, last_active + 1, channel))
            start = None
    if start is not None:
        segments.append(_segment(rms, start, last_active + 1, channel))
    return [s for s in segments if s.duration >= MIN_SEGMENT]


def _segment(rms: np.ndarray, start_frame: int, end_frame: int, channel: int) -> Segment:
    energy = float(np.mean(rms[start_frame:end_frame])) if end_frame > start_frame else 0.0
    return Segment(
        speaker="", start=start_frame * FRAME_SECONDS, end=end_frame * FRAME_SECONDS, channel=channel, energy=energy
    )


def _features(samples: np.ndarray, rate: int, segment: Segment) -> np.ndarray:
    """Loudness, zero-crossing rate and spectral centroid of a segment's audio."""
    piece = samples[int(segment.start * rate) : int(segment.end * rate)].astype(np.float64)
    if len(piece) < 2:
        return np.zeros(3)
    rms = float(np.sqrt(np.mean(piece**2)))
    crossings = float(np.mean(np.abs(np.diff(np.sign(piece))) > 0))
    spectrum = np.abs(np.fft.rfft(piece * np.hanning(len(piece))))
    freqs = np.fft.rfftfreq(len(piece), d=1.0 / rate)
    centroid = float(np.sum(freqs * spectrum) / np.sum(spectrum)) if np.sum(spectrum) > 0 else 0.0
    return np.array([rms, crossings, centroid / 1000.0])


def cluster_speakers(samples: np.ndarray, rate: int, segments: list[Segment]) -> list[Segment]:
    """Two speakers from a mono recording's segments, by two-means over their sound; one speaker when they all agree."""
    if not segments:
        return segments
    if len(segments) < 2:
        segments[0].speaker = SPEAKER_NAMES[0]
        return segments
    features = np.stack([_features(samples, rate, s) for s in segments])
    spread = features.std(axis=0)
    spread[spread == 0] = 1.0
    scaled = (features - features.mean(axis=0)) / spread
    # Deterministic start: the two segments furthest apart in sound.
    centroid_axis = scaled[:, 2] + 0.5 * scaled[:, 1]
    centres = np.stack([scaled[int(np.argmin(centroid_axis))], scaled[int(np.argmax(centroid_axis))]])
    labels = np.zeros(len(segments), dtype=int)
    for _ in range(20):
        distances = np.stack([np.linalg.norm(scaled - centre, axis=1) for centre in centres], axis=1)
        new_labels = np.argmin(distances, axis=1)
        if np.array_equal(new_labels, labels) and _ > 0:
            break
        labels = new_labels
        for k in range(2):
            if np.any(labels == k):
                centres[k] = scaled[labels == k].mean(axis=0)
    separation = float(np.linalg.norm(centres[0] - centres[1]))
    if separation < 0.75 or len(set(labels.tolist())) < 2:
        for segment in segments:
            segment.speaker = SPEAKER_NAMES[0]
        return segments
    # The speaker heard first is speaker 1.
    first = labels[int(np.argmin([s.start for s in segments]))]
    for segment, label in zip(segments, labels, strict=True):
        segment.speaker = SPEAKER_NAMES[0] if label == first else SPEAKER_NAMES[1]
    return segments


def diarise(recording: Recording, *, channel_roles: list[str] | None = None) -> list[Segment]:
    """The recording's segments with speakers: by channel for stereo, by clustering for mono."""
    roles = [r.strip() for r in (channel_roles or []) if r and r.strip()]
    if len(recording.channels) >= 2:
        segments: list[Segment] = []
        for index, samples in enumerate(recording.channels):
            name = (
                roles[index]
                if index < len(roles)
                else SPEAKER_NAMES[index]
                if index < len(SPEAKER_NAMES)
                else f"speaker_{index + 1}"
            )
            for segment in speech_segments(samples, recording.sample_rate, channel=index):
                segment.speaker = name
                segments.append(segment)
        return sorted(segments, key=lambda s: (s.start, s.channel))
    mono = recording.mono
    segments = cluster_speakers(mono, recording.sample_rate, speech_segments(mono, recording.sample_rate))
    if roles:
        names = {SPEAKER_NAMES[i]: roles[i] for i in range(min(len(roles), 2))}
        for segment in segments:
            segment.speaker = names.get(segment.speaker, segment.speaker)
    return sorted(segments, key=lambda s: s.start)


def speakers_of(segments: list[Segment]) -> dict[str, dict[str, Any]]:
    """Each speaker's talk time, turns and share of the speech."""
    total = sum(s.duration for s in segments) or 1.0
    out: dict[str, dict[str, Any]] = {}
    for segment in segments:
        entry = out.setdefault(segment.speaker, {"talk_seconds": 0.0, "turns": 0, "channel": segment.channel})
        entry["talk_seconds"] = round(entry["talk_seconds"] + segment.duration, 3)
        entry["turns"] += 1
    for entry in out.values():
        entry["share"] = round(entry["talk_seconds"] / total, 3)
    return out
