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

### Fake mail — `core/test_doubles/fake_mail.py`

| | |
|---|---|
| Replaces | SMTP send in `core.email.send_email` |
| Activation | `AGENTICORG_TEST_FAKE_MAIL=1` |
| Default in tests | Yes — `tests/conftest.py` sets the flag at import time |
| Capture | In-process `_OUTBOX` — every send becomes a `CapturedEmail` |
| Inspection | `fake_mail.outbox()`, `last()`, `count()`, `count_to(addr)` |
| Cleanup between tests | `fake_mail.reset()` runs in the autouse conftest fixture |

#### Domain validation runs FIRST

`send_email` validates the recipient domain BEFORE checking the
fake-mail flag. Tests that assert "invalid domain → no send"
(e.g. SECURITY tests for SSRF / open-relay protection) keep
working under the fake. Foundation #8's claude-mistakes test
forbids the false-green pattern where a fake masks a production
validation contract.

#### Adding a test that asserts an email was sent

```python
from core.test_doubles import fake_mail
from core.email import send_welcome_email


def test_welcome_email_sent_to_new_user():
    send_welcome_email(to="alice@example.com", org_name="Acme", name="Alice")
    rec = fake_mail.last()
    assert rec is not None
    assert rec.to == "alice@example.com"
    assert "Welcome" in rec.subject
```

The autouse fixture resets the outbox between tests so captures
don't leak.

### Fake storage — `core/test_doubles/fake_storage.py`

| | |
|---|---|
| Replaces | GCS upload in `core.billing.invoice_generator._upload_pdf` (and any future GCS upload sites) |
| Activation | `AGENTICORG_TEST_FAKE_STORAGE=1` |
| Default in tests | Yes — `tests/conftest.py` sets the flag at import time |
| Capture | In-process `_OBJECTS` list — every upload becomes a `StoredObject` |
| Inspection | `fake_storage.objects()`, `last()`, `count()`, `get(bucket, key)`, `list_in(bucket)` |
| Cleanup between tests | `fake_storage.reset()` runs in the autouse conftest fixture |

#### Adding a test that asserts a file was uploaded

```python
import asyncio, uuid
from core.test_doubles import fake_storage
from core.billing.invoice_generator import _upload_pdf


def test_invoice_pdf_uploaded(monkeypatch):
    monkeypatch.setenv("AGENTICORG_INVOICE_BUCKET", "billing-bucket")
    tenant_id = uuid.uuid4()
    url = asyncio.run(_upload_pdf(tenant_id, "INV-001", b"%PDF-1.4..."))
    assert url == f"gs://billing-bucket/invoices/{tenant_id}/INV-001.pdf"
    obj = fake_storage.get("billing-bucket", f"invoices/{tenant_id}/INV-001.pdf")
    assert obj.content_type == "application/pdf"
    assert obj.data.startswith(b"%PDF-")
```

Re-uploads to the same `(bucket, key)` overwrite — `get()` always
returns the most-recent object, matching real GCS semantics.

## Planned doubles (Foundation #7 follow-up PRs)

| Service | Double | Status |
|---------|--------|--------|
| LLM | Fake LLM | **Shipped (PR-A #357)** |
| Mail | Fake mail | **Shipped (PR-B #358)** |
| Object storage | Fake storage | **Shipped (PR-C)** |
| Connector APIs | Stub server (httpx mock app) | PR-D |
| Celery worker | service container in CI | PR-E |

Each follow-up follows the same shape: env-var seam at the
boundary, in-process double, autouse fixture, regression test
pinning the seam, doc entry above.
