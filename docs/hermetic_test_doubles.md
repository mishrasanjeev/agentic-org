# Hermetic test doubles (Foundation #7)

PR CI must run without prod secrets, real LLM credits, or real
external services. This doc tracks each environmental dependency,
its in-process double, and the env-var seam that selects between
them.

The doubles live under `core/test_doubles/` and are imported
lazily inside the production seam so they don't add to cold-path
imports outside test runs.

## Active doubles

### Fake LLM — `core/test_doubles/fake_llm.py`

| | |
|---|---|
| Replaces | Gemini / Claude / OpenAI clients in `core.llm.router.LLMRouter._call_model` |
| Activation | `AGENTICORG_TEST_FAKE_LLM=1` |
| Default in tests | Yes — `tests/conftest.py` sets the flag at import time |
| Determinism | Same `(model, messages)` always returns the same response (sha256 of inputs) |
| Cost | Always `$0` so it can never trip `AGENTICORG_GEMINI_DAILY_USD_CAP` |
| Custom responses | `fake_llm.register_response(prompt_contains="...", content="...")` |
| Inspection | `fake_llm.call_log()` and `fake_llm.call_count()` |
| Cleanup between tests | `fake_llm.reset()` runs in an autouse conftest fixture |

#### Why a single global flag, not dependency injection?

Every code path that ends up calling the LLM (workflows,
explainers, agent generators, content safety, summarisers, ...)
would otherwise need `llm_router=` threaded through every
constructor. The flag gives us one seam at the boundary that
catches every caller, without growing the surface area of every
intermediate service.

#### Adding a new test that needs a specific LLM response

```python
from core.test_doubles import fake_llm


def test_my_workflow_with_specific_llm_output():
    fake_llm.register_response(
        prompt_contains="extract entities",
        content='{"entities": ["Alice", "Bob"]}',
    )
    result = my_workflow.run(...)
    assert result["entities"] == ["Alice", "Bob"]
    # Optional: assert which prompts the workflow sent.
    assert any(
        "extract entities" in c.messages[0]["content"].lower()
        for c in fake_llm.call_log()
    )
```

The autouse fixture in `tests/conftest.py` calls `fake_llm.reset()`
before every test, so registrations + the call log start fresh.

#### Opting back into the real client for one test

```python
def test_real_gemini_call(monkeypatch):
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_LLM", raising=False)
    # ... requires AGENTICORG_GEMINI_API_KEY in the environment ...
```

These tests should be rare and should be marked
`@pytest.mark.skipif(not os.getenv("AGENTICORG_GEMINI_API_KEY"))`
so they skip cleanly when the key is absent.

## Planned doubles (Foundation #7 follow-up PRs)

| Service | Double | Status |
|---------|--------|--------|
| LLM | Fake LLM | **Shipped (PR-A)** |
| Mail | MailHog | PR-B |
| Object storage | LocalStack S3 / fake-gcs | PR-C |
| Connector APIs | Stub server (httpx mock app) | PR-D |
| Celery worker | service container in CI | PR-E |

Each follow-up follows the same shape: env-var seam at the
boundary, in-process double, autouse fixture, regression test
pinning the seam, doc entry above.
