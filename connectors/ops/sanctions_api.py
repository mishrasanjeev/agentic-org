# SPDX-License-Identifier: Apache-2.0
"""Sanctions API connector (deprecated) — ops / compliance.

The legacy screening connector, kept for tenants that already use the ``sanctions_api`` id. It
behaves as it did before ``sanctions_screening`` existed: the same five tools, ``api_key`` bearer
authentication, the same HTTP requests and the provider's JSON answers returned unchanged.

Only the provider's address moved out of the code. It is the connector config's ``base_url`` or,
when the tenant's config has none, the deployment setting ``AGENTICORG_SANCTIONS_API_BASE_URL``
(``settings.sanctions_api_base_url``). With neither set the connector is not configured: it
connects nothing, every tool raises :class:`SanctionsApiNotConfiguredError` before a request is
sent, and the health check reports ``not_configured``.

Deprecated: creating it logs ``connector_id_deprecated`` and raises a ``DeprecationWarning``. It is
registered with ``ConnectorRegistry.register_deprecated``, so it resolves by name but stays out of
the catalog and the product counts. An agent moves to ``sanctions_screening``
(``connectors/ops/sanctions_screening.py``) by linking it; a grant held under either id covers the
tools the two connectors share (``auth.grant_enforcement.enforce_connector_grant``).
"""

from __future__ import annotations

import warnings
from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()

NOT_CONFIGURED = (
    "sanctions_api has no provider base URL: set `base_url` in its connector config or "
    "AGENTICORG_SANCTIONS_API_BASE_URL in the deployment; no request was sent"
)


class SanctionsApiNotConfiguredError(RuntimeError):
    """Neither the connector config nor the deployment names the provider, so the call is refused."""


def _configured_base_url(config: dict[str, Any]) -> str:
    """The connector config's ``base_url`` when it has one, else the deployment setting."""
    # Imported here so the setting is read when the connector is created, as deployed.
    from core.config import settings  # noqa: PLC0415

    return str(config.get("base_url") or "").strip() or settings.sanctions_api_base_url.strip()


class SanctionsApiConnector(BaseConnector):
    name = "sanctions_api"
    category = "ops"
    auth_type = "api_key"
    base_url = ""
    rate_limit_rpm = 500
    replacement = "sanctions_screening"

    def __init__(self, config: dict[str, Any] | None = None):
        warnings.warn(
            f"connector id {self.name!r} is deprecated; use {self.replacement!r}", DeprecationWarning, stacklevel=2
        )
        logger.warning("connector_id_deprecated", connector=self.name, replacement=self.replacement)
        super().__init__(config)
        self.base_url = _configured_base_url(self.config)

    def _register_tools(self):
        self._tool_registry["screen_entity"] = self.screen_entity
        self._tool_registry["screen_transaction"] = self.screen_transaction
        self._tool_registry["get_alert"] = self.get_alert
        self._tool_registry["batch_screen"] = self.batch_screen
        self._tool_registry["generate_report"] = self.generate_report

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

    async def connect(self) -> None:
        if not self.base_url:
            # Fail closed: no client, so nothing can reach a guessed or empty address.
            logger.warning("sanctions_api_not_configured", connector=self.name)
            return
        await super().connect()

    def _require_base_url(self) -> None:
        if not self.base_url:
            raise SanctionsApiNotConfiguredError(NOT_CONFIGURED)

    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        self._require_base_url()
        return await super()._get(path, params)

    async def _post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        self._require_base_url()
        return await super()._post(path, data)

    async def health_check(self) -> dict[str, Any]:
        if not self.base_url:
            return {"status": "not_configured", "reason": NOT_CONFIGURED}
        try:
            await self._post("/search", {"name": "test"})
            return {"status": "healthy"}
        # enterprise-gate: broad-except-ok reason=connector-health-boundary-reports-unhealthy
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def screen_entity(self, **params) -> dict[str, Any]:
        """Screen an entity name against sanctions lists.

        Params: name (required), type (individual/entity, default individual),
                date_of_birth (optional YYYY-MM-DD), nationality (optional 2-letter),
                min_score (0-100, default 80).
        """
        params.setdefault("min_score", 80)
        return await self._post("/search", params)

    async def screen_transaction(self, **params) -> dict[str, Any]:
        """Screen all parties in a transaction.

        Params: sender_name (required), receiver_name (required),
                sender_country (optional), receiver_country (optional),
                amount (optional), currency (optional).
        """
        return await self._post("/search/transaction", params)

    async def get_alert(self, **params) -> dict[str, Any]:
        """Get details of a screening alert.

        Params: alert_id (required).
        """
        alert_id = params["alert_id"]
        return await self._get(f"/alerts/{alert_id}")

    async def batch_screen(self, **params) -> dict[str, Any]:
        """Screen multiple entities in a single batch.

        Params: entities (list of {name, type, date_of_birth, nationality}),
                min_score (default 80).
        """
        return await self._post("/search/batch", params)

    async def generate_report(self, **params) -> dict[str, Any]:
        """Generate a screening report for audit purposes.

        Params: screening_id (required), format (pdf/json, default json).
        """
        screening_id = params["screening_id"]
        return await self._get(f"/reports/{screening_id}", {"format": params.get("format", "json")})
