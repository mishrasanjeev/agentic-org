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
