# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The Plural handler's advertised schema URLs on agenticorg.ai map to files the UI build publishes.

The UCP profile and ACP sessions advertise
``https://agenticorg.ai/ucp/handlers/plural-tokenized/2026-10-20/config.json`` (and the ACP
``instrument_schemas`` entry beside it). agenticorg.ai is the UI image: Vite copies ``ui/public``
into the bundle root, and nginx serves ``*.json`` paths outside ``/api/`` and ``/ws/`` as static
files. This test replays that mapping so a renamed or missing file fails here, not in production.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from jsonschema import Draft202012Validator

from core.commerce.payment_handlers import plural

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("url", [plural.HANDLER_SCHEMA, plural.INSTRUMENT_SCHEMA])
def test_advertised_schema_url_maps_to_a_published_json_schema(url: str) -> None:
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "agenticorg.ai"
    path = ROOT / "ui" / "public" / parts.path.lstrip("/")
    assert path.is_file(), path
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == url
    for template in ("nginx.cloudrun.conf.template", "nginx.conf"):
        config = (ROOT / "ui" / template).read_text(encoding="utf-8")
        static = re.search(r"location ~\*\s+(\^/\(\?!api/\|ws/\)\.\*\\\.\([a-z|]+\)\$)", config)
        assert static is not None, template
        assert re.match(static.group(1), parts.path), (template, parts.path)


def test_the_handler_config_the_server_emits_validates_against_the_hosted_schema() -> None:
    import uuid

    from tests import ucp_txn_fixtures as uf

    tenant = str(uuid.uuid4())
    schema = json.loads((ROOT / "ui" / "public" / urlsplit(plural.HANDLER_SCHEMA).path.lstrip("/")).read_text())
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    for version in ("2026-04-08", "2026-08-25"):
        (entry,) = plural.PluralTokenizedInstrumentHandler().entries(uf.merchant(tenant), version)
        assert [e.message for e in validator.iter_errors(entry.config)] == []
    instrument = json.loads((ROOT / "ui" / "public" / urlsplit(plural.INSTRUMENT_SCHEMA).path.lstrip("/")).read_text())
    good = {"type": "card", "credential": {"type": "plural_pine_token", "token": "tok_example_1"}}
    pan = {"type": "card", "credential": {"type": "card", "card_number_type": "fpan", "number": "4000000000000002"}}
    checker = Draft202012Validator(instrument)
    assert checker.is_valid(good) and not checker.is_valid(pan)
