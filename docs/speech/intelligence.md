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

## Summaries

`POST /speech/recordings/{id}/summary` summarises a transcribed recording into one shape: the
intent, the key points, the next actions, the outcome (resolved, unresolved, escalated, follow up)
and the customer's mood (`core/speech/summary.py`). The model path goes through the content
services' checked JSON call (schema, one retry); the extractive path needs no model: the intent
from the banking intent catalogue over the customer's turns, the key points as the most informative
turns, the next actions as the turns that commit to something, the outcome from the closing turns.
`method=auto` takes the model and falls back to the words, saying so; `model` and `extractive` insist.
The summary is kept encrypted like the transcript.

## Analytics

`core/speech/analytics.py` computes, from the words and timings alone, the customer's sentiment turn
by turn and by thirds of the call (the conversation service's lexicon), the agent's empathy markers
(acknowledgement, apology, reassurance, thanks) and whether negative customer turns were answered
with one (a 0 to 100 empathy score), the interaction figures (talk ratio, words per minute,
interruptions as overlapping segments of different speakers, silences over three seconds, the
longest monologue) and escalation signals (a phrase asking for a person or a complaint, a run of
negative turns, a mood that fell). Roles come from `channel_roles` or recognisable speaker names,
else the first speaker is the agent (a recognisable label wins over channel order). A transcript
attached later replaces the summary and the analytics of the old one. The figures hold no words and
are kept in clear;
`GET /speech/analytics` averages them over the latest summarised recordings.

## Disclosure scripts

`core/speech/disclosures.py` fixes the scripts a bank requires on calls: the recorded line (within
60 s), identity verification (within 120 s), rate and fees, the cooling-off period, how to complain,
consent to proceed, collections conduct; each with the phrases that count as having said it and the
call types it applies to. The business console's `speech.required_disclosures` says which this
tenant requires. `GET /speech/recordings/{id}/disclosures?call_type=` checks a kept transcript:
each required disclosure said where, late, or missing.

## Live agent assist

`POST /speech/live/sessions` opens a call (`call_ref`, `call_type`) with the checklist its type
requires; each turn posted to `POST /speech/live/sessions/{id}/turns` (speaker, text, seconds
from the start) comes back with what the agent should see now: the checklist with each item said,
pending or late, the customer's mood on the last turn and the negative streak, the banking intent
recognised, the next question the intent still needs, the knowledge articles that answer the
customer's turn (the tenant's knowledge search; nothing when it cannot answer), and the flags raised
by this turn: a disclosure overdue the moment its deadline passes unsaid, two negative turns in a
row, a request for a person or a complaint. `POST .../close` gives the compliance report. The
turns are kept encrypted like a transcript; the flags and the report hold no words. The knowledge
search runs with the caller's own domains, so the agent sees only what they could read themselves;
two turns arriving together are serialised by the session's turn count, so none is lost; the
speech settings appear in the business console only while speech intelligence is on
(`core/speech/assist.py`, `speech_live_sessions`).

## Spoken sensitive data

`core/speech/redaction.py` finds card numbers, one-time codes, CVVs and PINs in the timed words of
a transcript. Speech comes as digits, number words, "double" and "triple", so every run of
consecutive spoken digits is read first and then judged: 13 to 19 digits that pass the Luhn check
(or follow a card cue) are a card number; 4 to 8 digits after a one-time-code cue are a code; 3 or
4 after a CVV cue a CVV; 4 to 6 after a PIN cue a PIN. `POST /speech/recordings/{id}/redact` cuts
the spans from the transcript (a marker in place of the words, a card keeping its last four) and
silences them in the audio with a little padding either side; the original audio is not kept, the
summary made from the old transcript is dropped, and what was cut is recorded as kinds and times
only (`redactions`, `GET .../redactions`). `dry_run` reports the spans without changing anything; an empty `kinds` list cuts nothing; a
redaction that finds the recording redacted meanwhile is refused and run again, so two never
restore what the other cut. Spelled separators (dash, hyphen, slash, punctuation) between the
digits of one number do not break the run.
The business console's `speech.redaction_kinds` says what is cut and `speech.redact_on_transcription`
cuts it as soon as a recording is transcribed, by an engine or by an attached transcript. A live
session masks each turn the same way before keeping it.

## Storage

`speech_recordings` keeps the audio, the segments and speakers in clear (they hold no words), and
the transcript under the tenant's key as the voice runtime keeps call transcripts
(`{"_encrypted": ...}`, `core/crypto/tenant_secrets.py`), so a database read never yields speech in
clear. Tenant scoped under a forced row-level policy. `GET /speech/recordings` lists without
transcripts; `GET /speech/recordings/{id}` returns the segments, speakers and transcript;
`GET /speech/recordings/{id}/audio` returns the audio as kept. The speech routes map onto enforced
RBAC scopes (`api/route_enforcement.py`): a read needs `audit:read`, a write `approvals:write`;
administrators pass. An upload is read one byte past the limit at most and refused beyond it; the
decoding, the signal work and local inference run in worker threads so the event loop keeps
serving; a transcript is encrypted before the row is locked.
