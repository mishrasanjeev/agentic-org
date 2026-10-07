# Speech and conversation intelligence: transcription and diarisation

With `AGENTICORG_SPEECH_INTELLIGENCE_ENABLED` on, `POST /speech/recordings` takes a call recording,
finds who spoke when, transcribes it where an engine is available and keeps it with the transcript
encrypted (`core/speech/`). Off, `GET /speech/status` answers `enabled: false` and the rest is not
found.

## Recordings

`core/speech/audio.py` reads PCM WAV (8 to 32-bit, one or two channels) through the standard
library, so a deployment needs no media binaries; a compressed recording is refused with a clear
message and is converted to WAV at the edge. A file is at most 50 MB and two hours. The audio is
kept as uploaded (`speech_recordings.content`) so the later parts (summaries, agent assist,
redaction) work from the same bytes; the redaction part rewrites it.

## Who spoke when

`core/speech/segments.py` finds speech segments by energy: a voice activity detector over 20 ms
frames with a floor set between the quiet and the loud frames of the recording, pauses under 0.4 s
bridged, bursts under 0.25 s dropped. A stereo call carries one party per channel, so the channel
is the speaker and `channel_roles=agent,customer` names them. A mono recording's segments are
grouped into two speakers by their sound (loudness, zero crossings and spectral centroid, a
deterministic two-means), and one speaker when the segments do not separate. Every segment carries
its speaker, start, end and channel; the recording carries each speaker's talk time, turns and
share.

## Transcription

`core/speech/transcribe.py` gives three ways to timed words: a local `faster-whisper` model where
the package is installed (nothing leaves the deployment), the tenant's Deepgram credential through
the provider resolver's `stt` kind (the provider's own speaker labels are kept beside), or words the
caller transcribed elsewhere (`POST /speech/recordings/{id}/transcript` with text, start, end and an
optional confidence). Whatever the source, each word takes the speaker of the segment that holds
its middle (the nearest segment when none does) and consecutive words of one speaker become a turn,
split at a pause over a second; the transcript carries the words, the turns, the plain text and a
confidence. An engine that is not installed or not configured leaves the recording kept with its
speakers and `status: failed` naming the reason, never a blank transcript presented as speech.

## Storage

`speech_recordings` keeps the audio, the segments and speakers in clear (they hold no words), and
the transcript under the tenant's key as the voice runtime keeps call transcripts
(`{"_encrypted": ...}`, `core/crypto/tenant_secrets.py`), so a database read never yields speech in
clear. Tenant scoped under a forced row-level policy. `GET /speech/recordings` lists without
transcripts; `GET /speech/recordings/{id}` returns the segments, speakers and transcript;
`GET /speech/recordings/{id}/audio` returns the audio as kept.
