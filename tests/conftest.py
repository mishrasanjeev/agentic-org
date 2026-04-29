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


@pytest.fixture(autouse=True)
def _reset_fake_doubles_between_tests():
    """Clear hermetic doubles' state before each test so captures
    + registrations don't bleed across cases."""
    for _mod_name in ("fake_llm", "fake_mail", "fake_storage"):
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
