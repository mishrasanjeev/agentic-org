# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The API logs the platform licence notice once, at warning level, when it starts."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest


class _StartupStoppedError(Exception):
    pass


async def test_api_start_logs_the_licence_notice_once_at_warning(caplog: pytest.LogCaptureFixture) -> None:
    from api import main

    # Stop startup at its first step after the notice, so nothing else needs a database or Redis.
    with (
        patch("core.crypto.credential_vault.assert_vault_key_configured", side_effect=_StartupStoppedError),
        caplog.at_level(logging.WARNING, logger="api.main"),
        pytest.raises(_StartupStoppedError),
    ):
        async with main.lifespan(main.app):
            pytest.fail("the app must not start")

    notices = [record for record in caplog.records if record.getMessage() == main.LICENCE_NOTICE]
    assert len(notices) == 1
    assert notices[0].levelno == logging.WARNING
    assert notices[0].name == "api.main"


def test_licence_notice_is_a_fixed_statement_of_the_licence() -> None:
    from api import main

    notice = main.LICENCE_NOTICE
    assert "Orchestrum Personal Use License 1.0" in notice
    assert "signed agreement with Orchestrum Technologies LLP" in notice
    assert "COMMERCIAL-LICENSE.md" in notice
    assert "https://agenticorg.ai/docs/licensing" in notice
    assert "%" not in notice and "{" not in notice  # no formatting placeholders for runtime data
