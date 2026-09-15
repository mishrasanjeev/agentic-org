# Record and replay model calls

Agent tests must not depend on a live model. `core/model_replay.py` records
model requests and responses to **cassettes** and replays them, for both model
entry points:

- LangGraph agents — chat models from `core.langgraph.llm_factory.create_chat_model`
- Completions through `core.llm.router.LLMRouter`

Why it is built this way: [ADR 0008](../adr/0008-record-and-replay-model-testing.md).

## Modes

| `AGENTICORG_MODEL_MODE` | Behaviour |
|---|---|
| `live` | Calls the provider. Default outside CI. |
| `record` | Calls the provider and writes a cassette per request. |
| `replay` | Answers from cassettes only. A missing cassette fails with `CassetteMissError`; it never calls the provider. |
| *(unset, in CI)* | `replay` for calls inside a cassette directory; unchanged elsewhere. |

`record` and `replay` are refused outside local and test runtimes
(`AGENTICORG_ENV` must be a relaxed environment). An unrecognised value is an
error, not a fallback.

In tests the router keeps using the deterministic fake LLM
(`docs/hermetic_test_doubles.md`) unless `AGENTICORG_MODEL_MODE` is set
explicitly.

## Using cassettes in a test

Request the `model_cassette` fixture (defined in `tests/conftest.py`). Model
calls made during the test use `tests/cassettes/<test module>/<test name>/`.

The worked examples run in CI:

- `tests/unit/test_model_replay_agent_graph.py` — an agent graph run is
  recorded, then replayed with no live model; changing the task text misses
  instead of replaying the old answer.
- `tests/unit/test_model_replay.py` — modes, keys, misses, corrupt cassettes,
  the router path and the fixture.

Run a test in replay mode locally exactly as CI does:

```bash
CI=true python -m pytest tests/unit/test_model_replay_agent_graph.py --no-cov
```

## How a request is keyed

The key is `sha256:` over canonical JSON of:

- the model id (`provider/model` when a provider is pinned),
- every message: role, content, tool calls and their ids, tool results,
- the bound tool schemas,
- temperature, max tokens and stop sequences.

Message ids that change on every run are excluded. Anything that changes what
the model would see changes the key.

## When a replay misses

The error names the key, the cassette directory and where the request first
differs from the closest recording, for example:

```text
no cassette for model request sha256:3f1c… in tests/cassettes/test_case_review/test_clean_case.
  The request differs from the nearest recording at messages[1] (9a0b….json):
    recorded: {"content":"Review case CASE-0001.","role":"human"}
    now:      {"content":"Review case CASE-0002.","role":"human"}
```

Decide whether the change is intended:

- **Not intended** — fix the prompt, tool or data that changed.
- **Intended** — re-record, then review the cassette diff like any other
  change to expected output.

## Recording

Recording calls a real model, so it needs provider credentials in your
environment and is never part of pull-request CI.

```bash
AGENTICORG_ENV=test AGENTICORG_MODEL_MODE=record \
  python -m pytest tests/path/to/test_file.py::test_name --no-cov
```

Before committing:

- Record only from **synthetic** fixtures. Cassettes contain the rendered
  prompts and responses.
- Read the diff. A cassette is expected output; review it as such.
- Delete cassettes for tests you removed or renamed.

## Nightly re-record

`.github/workflows/cassette-rerecord.yml` runs every night (and on demand). It
re-runs every test that uses the `model_cassette` fixture (they carry the
`model_cassette` marker automatically, so `pytest -m model_cassette` selects
them) in `record` mode against a live model, using the `MODEL_RECORD_API_KEY`
secret for the provider named by the `MODEL_RECORD_PROVIDER` repository
variable (`gemini` by default, or `openai` or `anthropic`). The job summary
says whether the recording run passed and which cassettes now differ from the
committed ones; the log and the diff are uploaded as an artifact.

It only reports. Nothing is committed, and pull requests do not wait for it. A
difference means the model's answer to an unchanged request has drifted:
decide whether the tests still hold and, if the new behaviour is expected,
re-record in a pull request. Without the secret the job is skipped with a
warning. The same script runs locally:

```bash
MODEL_RECORD_API_KEY=... MODEL_RECORD_PROVIDER=openai bash scripts/rerecord_cassettes.sh
```

## Cassette format

One JSON file per request, named by the key's hex digest:

```json
{
  "format": 1,
  "key": "sha256:…",
  "model": "gemini-2.5-flash",
  "request": {"model": "…", "messages": […], "tools": […], "params": {…}},
  "response": {"type": "ai", "data": {"content": "…", "tool_calls": […]}}
}
```

Router cassettes store the `LLMResponse` fields as the response. Provider
response ids and metadata are dropped so re-recording an unchanged request does
not produce a noisy diff.
