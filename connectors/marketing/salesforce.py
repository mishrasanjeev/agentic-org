"""Salesforce connector — marketing/CRM.

Integrates with Salesforce REST API v60.0 for CRM operations.
Uses OAuth2 password or client_credentials grant. All object
operations use the sObject REST pattern.
"""

from __future__ import annotations

from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector


class SalesforceConnector(BaseConnector):
    name = "salesforce"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://org.my.salesforce.com/services/data/v60.0"
    rate_limit_rpm = 300

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._instance_url = ""

    def _register_tools(self):
        self._tool_registry["create_lead"] = self.create_lead
        self._tool_registry["update_opportunity"] = self.update_opportunity
        self._tool_registry["query"] = self.query
        self._tool_registry["create_task"] = self.create_task
        self._tool_registry["get_account"] = self.get_account
        self._tool_registry["list_opportunities"] = self.list_opportunities

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        username = self.config.get("username", "")
        password = self.config.get("password", "")
        login_url = self.config.get(
            "login_url", "https://login.salesforce.com/services/oauth2/token"
        )

        data: dict[str, str] = {
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if username and password:
            data["grant_type"] = "password"
            data["username"] = username
            data["password"] = password
        else:
            data["grant_type"] = "client_credentials"

        async with httpx.AsyncClient() as client:
            resp = await client.post(login_url, data=data)
            resp.raise_for_status()
            body = resp.json()
            self._instance_url = body["instance_url"]
            token = body["access_token"]

        # Update base_url to use the instance URL from auth response
        self.base_url = f"{self._instance_url}/services/data/v60.0"
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get("/limits")
            return {"status": "healthy", "limits": len(result) if isinstance(result, dict) else 0}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_lead(self, **params) -> dict[str, Any]:
        """Create a Lead in Salesforce.

        Params: LastName (required), Company (required), FirstName, Email,
                Phone, Title, LeadSource, Status, Description.
        """
        return await self._post("/sobjects/Lead", params)

    async def update_opportunity(self, **params) -> dict[str, Any]:
        """Update an Opportunity (e.g., change stage, amount, close date).

        Params: opportunity_id (required), StageName, Amount, CloseDate,
                Description, Probability.
        """
        opp_id = params.pop("opportunity_id")
        return await self._patch(f"/sobjects/Opportunity/{opp_id}", params)

    async def query(self, **params) -> dict[str, Any]:
        """Run a SOQL query against Salesforce.

        Params: q (required) — SOQL query string.
        Example: q="SELECT Id, Name, Amount, StageName FROM Opportunity WHERE IsClosed=false"
        """
        return await self._get("/query", params)

    async def create_task(self, **params) -> dict[str, Any]:
        """Create a Task (follow-up, call, meeting).

        Params: Subject (required), WhoId (contact/lead), WhatId (account/opp),
                ActivityDate, Priority, Status, Description.
        """
        return await self._post("/sobjects/Task", params)

    async def get_account(self, **params) -> dict[str, Any]:
        """Get an Account by ID.

        Params: account_id (required), fields (optional comma-separated).
        """
        account_id = params.pop("account_id")
        fields = params.get("fields", "Id,Name,Industry,AnnualRevenue,NumberOfEmployees")
        return await self._get(f"/sobjects/Account/{account_id}", {"fields": fields})

    async def list_opportunities(self, **params) -> dict[str, Any]:
        """List Opportunities via SOQL.

        Params: stage (optional), limit (optional, default 100).
        """
        limit = params.get("limit", 100)
        stage_filter = ""
        if params.get("stage"):
            stage_filter = f" WHERE StageName='{params['stage']}'"
        soql = f"SELECT Id,Name,Amount,StageName,CloseDate,AccountId FROM Opportunity{stage_filter} ORDER BY CloseDate DESC LIMIT {limit}"
        return await self._get("/query", {"q": soql})
