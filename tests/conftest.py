"""Shared test fixtures."""

import os
import tempfile
import uuid
from pathlib import Path

import pytest

_TEST_TMPDIR = Path.cwd() / "codex-pytest-temp"
_TEST_TMPDIR.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TEST_TMPDIR)
os.environ.setdefault("TMP", str(_TEST_TMPDIR))
os.environ.setdefault("TEMP", str(_TEST_TMPDIR))
os.environ.setdefault("TMPDIR", str(_TEST_TMPDIR))

# Foundation #7 PR-A: hermetic CI default. Every test run gets the
# fake LLM unless a specific test opts back in to the real client by
# clearing the flag in a fixture. This eliminates the "skipped
# because no GEMINI_API_KEY in CI" pattern that's been the largest
# silent coverage gap. See docs/hermetic_test_doubles.md.
os.environ.setdefault("AGENTICORG_TEST_FAKE_LLM", "1")
os.environ.setdefault("AGENTICORG_TEST_FAKE_MAIL", "1")
os.environ.setdefault("AGENTICORG_TEST_FAKE_STORAGE", "1")
os.environ.setdefault("AGENTICORG_TEST_FAKE_CONNECTORS", "1")
os.environ.setdefault("AGENTICORG_TEST_FAKE_CELERY", "1")

# SEC-2026-05-P1-007 (docs/BRUTAL_SECURITY_SCAN_2026-05-01.md): the
# webhook unsigned-bypass guard refuses to start in any non-local
# environment. Default the test environment to ``test`` so existing
# tests that flip ``AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1`` for payload-
# parsing tests still work. Tests that explicitly set
# ``AGENTICORG_ENV`` (e.g. the env-guard regression tests themselves)
# override this default via ``monkeypatch.setenv``.
os.environ.setdefault("AGENTICORG_ENV", "test")

# Foundation #7 PR-D: install the global httpx.AsyncClient patch so
# auth-time + one-off clients (e.g. OAuth token refresh inside
# connector ``_authenticate``) also route through MockTransport, not
# just the long-lived BaseConnector.self._client. Re-checks the env
# flag per call → per-test opt-out still works.
__import__(
    "core.test_doubles.fake_connectors",
    fromlist=["install_global_patch"],
).install_global_patch()

# Foundation #7 PR-E: explicitly switch the Celery app into eager
# mode + invocation-capture for tests. Done HERE (not at celery_app
# import time) so the flip is observable, reversible, and tied to
# the current env-var state. Tests that need a real broker round-
# trip can call ``fake_celery.deactivate(app)`` to opt back out
# without being silently overridden by a latched module-level flag.
__import__(
    "core.test_doubles.fake_celery", fromlist=["activate"]
).activate(
    __import__("core.tasks.celery_app", fromlist=["app"]).app
)


@pytest.fixture(autouse=True)
def _reset_fake_doubles_between_tests():
    """Clear hermetic doubles' state before each test so captures
    + registrations don't bleed across cases."""
    for _mod_name in (
        "fake_llm",
        "fake_mail",
        "fake_storage",
        "fake_connectors",
        "fake_celery",
    ):
        try:
            _mod = __import__(
                f"core.test_doubles.{_mod_name}", fromlist=[_mod_name]
            )
            _mod.reset()
        except ImportError:
            # Module not yet shipped on this branch.
            pass
    yield


@pytest.fixture
def tenant_id():
    return str(uuid.uuid4())


@pytest.fixture
def agent_config(tenant_id):
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "agent_type": "ap_processor",
        "domain": "finance",
        "authorized_tools": ["oracle_fusion:read:purchase_order"],
        "prompt_variables": {"org_name": "TestCorp", "ap_hitl_threshold": "500000"},
        "hitl_condition": "total > 500000",
        "output_schema": "Invoice",
    }


@pytest.fixture
def sample_invoice():
    return {
        "invoice_id": "INV-001",
        "vendor_id": "VND-001",
        "total": 94000,
        "status": "matched",
        "gstin": "29ABCDE1234F1Z5",
        "confidence": 0.96,
    }
