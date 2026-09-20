# SPDX-License-Identifier: Apache-2.0
"""The ``mock`` verification provider: fixture-backed, in-process or over HTTP.

- :class:`MockProvider` runs in-process.
- ``connectors.providers.mock.service`` serves a ``MockProvider`` over HTTP, and
  :class:`MockHttpProvider` is the provider that talks to it, so the seam is exercised over the
  network.
- :func:`create_mock_provider` is what the providers registry calls: it reads
  ``AGENTICORG_MOCK_PROVIDER_*`` and returns the HTTP client when ``AGENTICORG_MOCK_PROVIDER_URL``
  is set, the in-process provider otherwise. It refuses to run unless ``AGENTICORG_ENV`` is set
  explicitly to a local, development or test value - unlike the application settings, an unset
  variable does not default to development here.
"""

from __future__ import annotations

import os

from connectors.framework.verification_provider import VerificationProvider
from connectors.providers.mock.config import FaultKind, MockConfig, MockProviderSettings
from connectors.providers.mock.http_client import MockHttpProvider
from connectors.providers.mock.provider import PROVIDER_NAME, MockProvider
from core.config import is_relaxed_env


class MockProviderRefusedError(RuntimeError):
    pass


def mock_provider_available() -> bool:
    """True only when ``AGENTICORG_ENV`` is explicitly local, dev, development, test or ci."""
    return is_relaxed_env(os.getenv("AGENTICORG_ENV", ""))


def create_mock_provider() -> VerificationProvider:
    environment = os.getenv("AGENTICORG_ENV", "")
    if not mock_provider_available():
        raise MockProviderRefusedError(
            f"the mock provider only runs in local, development and test environments (AGENTICORG_ENV={environment!r})"
        )
    settings = MockProviderSettings()
    config = settings.to_config()
    if settings.url:
        return MockHttpProvider(settings.url, config=config)
    return MockProvider(config)


__all__ = [
    "PROVIDER_NAME",
    "FaultKind",
    "MockConfig",
    "MockHttpProvider",
    "MockProvider",
    "MockProviderRefusedError",
    "MockProviderSettings",
    "create_mock_provider",
    "mock_provider_available",
]
