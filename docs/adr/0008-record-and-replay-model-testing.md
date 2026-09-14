# ADR 0008: Record and replay model calls, keyed by the rendered request

- **Status**: Accepted
- **Date**: 2026-09-14
- **Deciders**: Sanjeev, Engineering team

## Context

Agent behaviour has to be tested end to end — a graph that reasons, calls
tools, is interrupted and resumes — without depending on a live model. A live
model is slow, costs money, needs credentials CI should not hold, and is not
deterministic, so an end-to-end test against one cannot gate a pull request.

The repository already had two partial seams:

- `core/test_doubles/fake_llm.py` short-circuits `LLMRouter` completions with a
  deterministic generated string. It never covered LangGraph agents, which
  build LangChain chat models through `core/langgraph/llm_factory.py`, and its
  output is not a real model's output.
- Individual tests patch `create_chat_model` with scripted models. That is
  right for graph mechanics, but every test writes its own script and nothing
  notices when a prompt changes underneath it.

Options considered:

- **HTTP-level recording (VCR-style).** Records provider HTTP traffic. It
  couples cassettes to each provider SDK's wire format and headers (where
  credentials live), and the natural match key — the HTTP body — changes with
  SDK upgrades that do not change behaviour.
- **Keying on the test name and call order.** Simple, but a changed prompt
  replays the old answer silently, which is exactly the failure that makes
  recorded tests untrustworthy.
- **Keying on the rendered request at the LangChain / router boundary.**
  Provider-independent, and any change to what the model would actually see
  produces a different key.

## Decision

Record and replay at the two model entry points, keyed by the rendered request:

- `AGENTICORG_MODEL_MODE` = `live` | `record` | `replay`.
- The key is a SHA-256 over canonical JSON of the model id, every message
  (role, content, tool calls, tool call ids, tool results), the bound tool
  schemas and the sampling parameters. Volatile message ids are excluded.
- `replay` never falls back to a live call. A miss raises
  `CassetteMissError`, naming the key, the directory and where the request
  first differs from the nearest recording.
- `record` and `replay` are refused outside local and test runtimes.
- In CI, replay is the default for calls made inside a cassette directory (a
  test opts in with the `model_cassette` fixture). Calls outside one keep their
  existing behaviour, so tests that assert how a provider model is constructed
  are unaffected; CI holds no model credentials, so a stray live call still
  fails.
- The existing fake LLM stays the router's default in tests; an explicit
  `AGENTICORG_MODEL_MODE` overrides it.
- Cassettes are committed under `tests/cassettes/`, reviewed like any fixture,
  and re-recorded deliberately.

## Consequences

- A prompt, tool-definition or tool-output change fails loudly in replay and
  shows the first differing message, instead of replaying stale text.
- Cassettes contain rendered prompts. They are written after the run's PII
  redaction and must only ever be recorded from synthetic fixtures; secret
  scanning covers them like any other file.
- Recording needs a live model and credentials, so it is a deliberate local or
  scheduled step, not part of pull-request CI.
- A scripted stub that returns fixed tool-call sequences is still needed for
  graph mechanics (interrupts, resume, retries) that should not depend on
  model text at all; it is a separate change.
