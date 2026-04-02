"""Banking AA (Account Aggregator) connector — finance.

Integrates with the Finvu Account Aggregator API for read-only
financial data access.  Account Aggregator is an RBI-regulated
framework for consented financial data sharing — it supports
fetching statements, balances, and transactions but NEVER payment
initiation (NEFT/RTGS/IMPS are separate banking rails).

When ``callback_url`` is set in config, the connector uses the full
RBI-compliant consent flow (create consent → user approval → fetch
data via session).  Without it, falls back to direct API calls
(suitable for sandbox/testing).
"""

from __future__ import annotations

from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector


class BankingAaConnector(BaseConnector):
    name = "banking_aa"
    category = "finance"
    auth_type = "aa_oauth2"
    base_url = "https://aa.finvu.in/api/v1"
    rate_limit_rpm = 100

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._consent_manager = None

        # Initialize consent manager if callback_url is configured
        callback_url = self.config.get("callback_url", "")
        if callback_url:
            from connectors.finance.aa_consent import AAConsentManager

            self._consent_manager = AAConsentManager(
                base_url=self.base_url,
                client_id=self.config.get("client_id", ""),
                client_secret=self.config.get("client_secret", ""),
                callback_url=callback_url,
                fiu_id=self.config.get("fiu_id", ""),
            )

    def _register_tools(self):
        self._tool_registry["fetch_bank_statement"] = self.fetch_bank_statement
        self._tool_registry["check_account_balance"] = self.check_account_balance
        self._tool_registry["get_transaction_list"] = self.get_transaction_list
        self._tool_registry["request_consent"] = self.request_consent
        self._tool_registry["fetch_fi_data"] = self.fetch_fi_data

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        token_url = self.config.get("token_url", f"{self.base_url}/oauth2/token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def request_consent(self, **params) -> dict[str, Any]:
        """Create an AA consent request and return the redirect URL.

        The user must be redirected to the returned URL to approve
        data sharing on the Finvu consent UI.

        Required params: customer_vua, fi_types, purpose_code,
                         from_date, to_date
        """
        if not self._consent_manager:
            return {"error": "Consent flow not configured — set callback_url in config"}

        from connectors.finance.aa_consent_types import ConsentRequest

        request = ConsentRequest(**params)
        return await self._consent_manager.create_consent_request(request)

    async def fetch_fi_data(self, **params) -> dict[str, Any]:
        """Fetch financial data using an approved consent.

        Required params: consent_id, from_date, to_date
        """
        if not self._consent_manager:
            return {"error": "Consent flow not configured — set callback_url in config"}

        consent_id = params["consent_id"]
        from_date = params["from_date"]
        to_date = params["to_date"]

        session = await self._consent_manager.create_fi_session(
            consent_id=consent_id,
            from_date=from_date,
            to_date=to_date,
        )
        return await self._consent_manager.fetch_fi_data(session.session_id)

    async def fetch_bank_statement(self, **params) -> dict[str, Any]:
        """Fetch bank statement via Account Aggregator.

        If consent_id is provided, uses the consent flow.
        Otherwise, falls back to direct API call (sandbox mode).
        """
        consent_id = params.pop("consent_id", "")
        if consent_id and self._consent_manager:
            session = await self._consent_manager.create_fi_session(
                consent_id=consent_id,
                from_date=params.get("from_date", ""),
                to_date=params.get("to_date", ""),
            )
            return await self._consent_manager.fetch_fi_data(session.session_id)
        return await self._post("/fetch/bank/statement", params)

    async def check_account_balance(self, **params) -> dict[str, Any]:
        """Check account balance via Account Aggregator."""
        return await self._post("/check/account/balance", params)

    async def get_transaction_list(self, **params) -> dict[str, Any]:
        """Get transaction list via Account Aggregator."""
        return await self._post("/get/transaction/list", params)
