# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The UPI mandate extension's advertised schema URL on agenticorg.ai maps to a file the UI build publishes.

The UCP profile advertises ``ai.agenticorg.payment.upi_mandate`` with
``schema: https://agenticorg.ai/ucp/extensions/upi-mandate/2026-10-20/schema.json``
(``core.commerce.india_rails.UCP_UPI_MANDATE_SCHEMA``). agenticorg.ai is the UI
image: Vite copies ``ui/public`` into the bundle root and nginx serves ``*.json``
paths outside ``/api/`` and ``/ws/`` as static files (the same mapping as the
Plural handler config schema). A renamed or missing file fails here, and the
Action the server emits must validate against the hosted schema.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from core.commerce import india_rails

ROOT = Path(__file__).resolve().parents[2]


def _schema() -> dict:
    path = ROOT / "ui" / "public" / urlsplit(india_rails.UCP_UPI_MANDATE_SCHEMA).path.lstrip("/")
    assert path.is_file(), path
    return json.loads(path.read_text(encoding="utf-8"))


def test_advertised_schema_url_maps_to_a_published_json_schema() -> None:
    url = india_rails.UCP_UPI_MANDATE_SCHEMA
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "agenticorg.ai"
    assert india_rails.UCP_UPI_MANDATE_VERSION in parts.path
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == url
    assert india_rails.UCP_AFA_ACTION in schema["properties"]["actions"]["properties"]
    for template in ("nginx.cloudrun.conf.template", "nginx.conf"):
        config = (ROOT / "ui" / template).read_text(encoding="utf-8")
        static = re.search(r"location ~\*\s+(\^/\(\?!api/\|ws/\)\.\*\\\.\([a-z|]+\)\$)", config)
        assert static is not None, template
        assert re.match(static.group(1), parts.path), (template, parts.path)


def test_the_action_the_server_emits_validates_against_the_hosted_schema() -> None:
    capability = india_rails.parse_upi_autopay_capability(
        {
            "amount_rule": "max",
            "max_amount_minor_units": 500000,
            "frequency": "monthly",
            "validity_days": 365,
            "purpose": "general",
            "authorization_url": "https://payments.example/upi/mandates/{mandate_reference}",
        }
    )
    assert capability is not None
    reference = india_rails.mandate_reference("tenant-1", "chk_1", "instr_1")
    terms = india_rails.mandate_terms(capability, amount_minor_units=45000, currency="INR", start=date(2026, 10, 20))
    action = india_rails.ucp_afa_action(
        reference=reference,
        instrument_id="instr_1",
        terms=terms,
        url=india_rails.authorization_url(capability, reference),
    )
    schema = _schema()
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    checkout = {"actions": {india_rails.UCP_AFA_ACTION: [action]}}
    assert [e.message for e in validator.iter_errors(checkout)] == []
    broken = json.loads(json.dumps(checkout))
    broken["actions"][india_rails.UCP_AFA_ACTION][0]["config"]["mandate"]["currency"] = "USD"
    assert not validator.is_valid(broken)
