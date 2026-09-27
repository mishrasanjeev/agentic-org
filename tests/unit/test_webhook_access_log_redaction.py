# SPDX-License-Identifier: Apache-2.0
"""Per-tenant webhook URLs carry a path token; the access log must not."""

from __future__ import annotations

import logging
import uuid

import pytest

from core.cases.provider_webhooks import webhook_path_token
from core.email_webhooks import webhook_path as email_webhook_path
from core.logging_config import configure_logging, redact_webhook_path_tokens

TENANT = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _access_record(path: str) -> logging.LogRecord:
    # The shape uvicorn's access logger emits: the request line is an argument.
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("203.0.113.7:4711", "POST", path, "1.1", 200),
        None,
    )


@pytest.mark.parametrize("provider", ["sendgrid", "mailchimp", "moengage"])
def test_email_webhook_path_token_is_redacted(provider: str) -> None:
    path = email_webhook_path(TENANT, provider)
    token = path.rsplit("/", 1)[1]
    record = _access_record(path)
    assert redact_webhook_path_tokens(record) is True
    message = record.getMessage()
    assert token not in message
    assert f"/api/v1/webhooks/email/{provider}/{TENANT}/[redacted]" in message


def test_provider_webhook_path_token_is_redacted() -> None:
    token = webhook_path_token(TENANT, "mock")
    record = _access_record(f"/api/v1/webhooks/providers/{TENANT}/mock/{token}?attempt=2")
    redact_webhook_path_tokens(record)
    message = record.getMessage()
    assert token not in message
    assert f"/api/v1/webhooks/providers/{TENANT}/mock/[redacted]?attempt=2" in message


def test_other_request_lines_are_unchanged() -> None:
    record = _access_record("/api/v1/webhooks/email/sendgrid")
    redact_webhook_path_tokens(record)
    assert '"POST /api/v1/webhooks/email/sendgrid HTTP/1.1" 200' in record.getMessage()


def test_configured_access_logger_redacts(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging()
    path = email_webhook_path(TENANT, "sendgrid")
    token = path.rsplit("/", 1)[1]
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', "203.0.113.7:4711", "POST", path, "1.1", 200
        )
    assert caplog.records, "the access record should still be logged"
    assert all(token not in r.getMessage() for r in caplog.records)
