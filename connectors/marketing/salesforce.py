# ruff: noqa: S608 -- SOQL is sent to Salesforce API, not a SQL database
"""Salesforce connector for native CRM operations."""

from __future__ import annotations

import re
from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector
from connectors.framework.url_security import require_https_origin

SALESFORCE_LOGIN_URLS = {
    "production": "https://login.salesforce.com/services/oauth2/token",
    "login": "https://login.salesforce.com/services/oauth2/token",
    "sandbox": "https://test.salesforce.com/services/oauth2/token",
    "test": "https://test.salesforce.com/services/oauth2/token",
}
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")


class SalesforceConnector(BaseConnector):
    name = "salesforce"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://org.my.salesforce.com/services/data/v60.0"
    rate_limit_rpm = 300
    tools = [
        "query",
        "validate_crm_access",
        "create_lead",
        "list_accounts",
        "search_accounts",
        "get_account",
        "create_account",
        "update_account",
        "delete_account",
        "list_contacts",
        "search_contacts",
        "get_contact",
        "create_contact",
        "update_contact",
        "delete_contact",
        "list_opportunities",
        "search_opportunities",
        "get_opportunity",
        "create_opportunity",
        "update_opportunity",
        "delete_opportunity",
        "create_task",
        "create_note",
        "create_opportunity_contact_role",
    ]

    def __init__(self, config: dict[str, Any] | None = None):
        safe_config = dict(config or {})
        safe_config.pop("base_url", None)
        super().__init__(safe_config)
        self._instance_url = ""

    def _register_tools(self):
        self._tool_registry["query"] = self.query
        self._tool_registry["validate_crm_access"] = self.validate_crm_access
        self._tool_registry["create_lead"] = self.create_lead
        self._tool_registry["list_accounts"] = self.list_accounts
        self._tool_registry["search_accounts"] = self.search_accounts
        self._tool_registry["get_account"] = self.get_account
        self._tool_registry["create_account"] = self.create_account
        self._tool_registry["update_account"] = self.update_account
        self._tool_registry["delete_account"] = self.delete_account
        self._tool_registry["list_contacts"] = self.list_contacts
        self._tool_registry["search_contacts"] = self.search_contacts
        self._tool_registry["get_contact"] = self.get_contact
        self._tool_registry["create_contact"] = self.create_contact
        self._tool_registry["update_contact"] = self.update_contact
        self._tool_registry["delete_contact"] = self.delete_contact
        self._tool_registry["list_opportunities"] = self.list_opportunities
        self._tool_registry["search_opportunities"] = self.search_opportunities
        self._tool_registry["get_opportunity"] = self.get_opportunity
        self._tool_registry["create_opportunity"] = self.create_opportunity
        self._tool_registry["update_opportunity"] = self.update_opportunity
        self._tool_registry["delete_opportunity"] = self.delete_opportunity
        self._tool_registry["create_task"] = self.create_task
        self._tool_registry["create_note"] = self.create_note
        self._tool_registry["create_opportunity_contact_role"] = (
            self.create_opportunity_contact_role
        )

    async def _authenticate(self):
        access_token = self._get_secret("access_token")
        instance_url = self.config.get("instance_url")
        if access_token and instance_url:
            self._instance_url = require_https_origin(
                str(instance_url),
                field="Salesforce instance_url",
                allowed_exact_hosts=("login.salesforce.com", "test.salesforce.com"),
                allowed_host_suffixes=(".salesforce.com", ".force.com"),
            )
            self.base_url = f"{self._instance_url}/services/data/v60.0"
            self._auth_headers = {"Authorization": f"Bearer {access_token}"}
            return

        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        username = self.config.get("username", "")
        password = self.config.get("password", "")
        login_url = self._login_url()

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

        async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as client:
            resp = await client.post(login_url, data=data)
            resp.raise_for_status()
            body = resp.json()
            self._instance_url = require_https_origin(
                body["instance_url"],
                field="Salesforce instance_url",
                allowed_exact_hosts=("login.salesforce.com", "test.salesforce.com"),
                allowed_host_suffixes=(".salesforce.com", ".force.com"),
            )
            token = body["access_token"]

        self.base_url = f"{self._instance_url}/services/data/v60.0"
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    def _login_url(self) -> str:
        environment = str(
            self.config.get("environment")
            or self.config.get("login_domain")
            or "production"
        ).strip().lower()
        return SALESFORCE_LOGIN_URLS.get(environment, SALESFORCE_LOGIN_URLS["production"])

    def _limit(self, params: dict[str, Any], default: int = 100) -> int:
        try:
            value = int(params.get("limit", default))
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, 200))

    def _soql_literal(self, value: Any) -> str:
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    def _fields(self, params: dict[str, Any], default: str) -> str:
        raw = params.get("fields", default)
        if isinstance(raw, list | tuple):
            candidates = [str(field).strip() for field in raw]
        else:
            candidates = [field.strip() for field in str(raw or default).split(",")]
        fields = [field for field in candidates if _FIELD_RE.match(field)]
        return ",".join(fields) if fields else default

    async def _delete_sobject(self, object_name: str, object_id: Any) -> dict[str, Any]:
        if not object_id:
            return {"error": f"{object_name.lower()}_id is required"}
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.delete(f"/sobjects/{object_name}/{object_id}")
        resp.raise_for_status()
        return {"status": "deleted", "object": object_name, "id": str(object_id)}

    async def _query_records(self, soql: str) -> dict[str, Any]:
        return await self._get("/query", {"q": soql})

    async def health_check(self) -> dict[str, Any]:
        try:
            result = await self._get("/limits")
            return {
                "status": "healthy",
                "limits": len(result) if isinstance(result, dict) else 0,
            }
        # enterprise-gate: broad-except-ok reason=salesforce-health-probe-failure-returns-unhealthy
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "error": type(exc).__name__}

    async def query(self, **params) -> dict[str, Any]:
        """Run a SOQL query against Salesforce."""
        if not params.get("q"):
            return {"error": "q is required"}
        return await self._get("/query", params)

    async def validate_crm_access(self, **_params) -> dict[str, Any]:
        """Validate read access to core Salesforce CRM objects."""
        checks: dict[str, dict[str, Any]] = {}
        for object_name in ("Account", "Contact", "Opportunity", "Task"):
            try:
                data = await self._query_records(
                    f"SELECT Id FROM {object_name} LIMIT 1"  # nosec B608
                )
                checks[object_name] = {
                    "status": "ready",
                    "total_size": data.get("totalSize", 0),
                }
            except httpx.HTTPStatusError as exc:
                checks[object_name] = {
                    "status": "blocked",
                    "http_status": exc.response.status_code,
                }
            # enterprise-gate: broad-except-ok reason=salesforce-object-access-probe-reports-blocked
            except Exception as exc:  # noqa: BLE001
                checks[object_name] = {
                    "status": "blocked",
                    "error_type": type(exc).__name__,
                }
        ready = all(row.get("status") == "ready" for row in checks.values())
        return {"status": "healthy" if ready else "unhealthy", "objects": checks}

    async def create_lead(self, **params) -> dict[str, Any]:
        """Create a Lead in Salesforce."""
        if not params.get("LastName"):
            return {"error": "LastName is required"}
        if not params.get("Company"):
            return {"error": "Company is required"}
        return await self._post("/sobjects/Lead", params)

    async def list_accounts(self, **params) -> dict[str, Any]:
        """List Salesforce Accounts."""
        fields = self._fields(params, "Id,Name,Industry,AnnualRevenue,NumberOfEmployees")
        soql = (
            f"SELECT {fields} FROM Account ORDER BY LastModifiedDate DESC "  # nosec B608
            f"LIMIT {self._limit(params)}"
        )
        return await self._query_records(soql)

    async def search_accounts(self, **params) -> dict[str, Any]:
        """Search Salesforce Accounts by Name."""
        query = params.get("query") or params.get("name")
        if not query:
            return {"error": "query is required"}
        fields = self._fields(params, "Id,Name,Industry,AnnualRevenue,NumberOfEmployees")
        like = self._soql_literal(f"%{query}%")
        soql = (
            f"SELECT {fields} FROM Account WHERE Name LIKE {like} "  # nosec B608
            f"LIMIT {self._limit(params)}"
        )
        return await self._query_records(soql)

    async def get_account(self, **params) -> dict[str, Any]:
        """Get an Account by ID."""
        account_id = params.get("account_id") or params.get("id")
        if not account_id:
            return {"error": "account_id is required"}
        fields = self._fields(params, "Id,Name,Industry,AnnualRevenue,NumberOfEmployees")
        return await self._get(f"/sobjects/Account/{account_id}", {"fields": fields})

    async def create_account(self, **params) -> dict[str, Any]:
        """Create a Salesforce Account."""
        if not params.get("Name"):
            return {"error": "Name is required"}
        return await self._post("/sobjects/Account", params)

    async def update_account(self, **params) -> dict[str, Any]:
        """Update a Salesforce Account."""
        account_id = params.pop("account_id", None) or params.pop("id", None)
        if not account_id:
            return {"error": "account_id is required"}
        return await self._patch(f"/sobjects/Account/{account_id}", params)

    async def delete_account(self, **params) -> dict[str, Any]:
        """Delete a Salesforce Account."""
        return await self._delete_sobject(
            "Account",
            params.get("account_id") or params.get("id"),
        )

    async def list_contacts(self, **params) -> dict[str, Any]:
        """List Salesforce Contacts."""
        fields = self._fields(params, "Id,FirstName,LastName,Email,Phone,AccountId,Title")
        soql = (
            f"SELECT {fields} FROM Contact ORDER BY LastModifiedDate DESC "  # nosec B608
            f"LIMIT {self._limit(params)}"
        )
        return await self._query_records(soql)

    async def search_contacts(self, **params) -> dict[str, Any]:
        """Search Salesforce Contacts by name or email."""
        query = params.get("query") or params.get("email") or params.get("name")
        if not query:
            return {"error": "query is required"}
        fields = self._fields(params, "Id,FirstName,LastName,Email,Phone,AccountId,Title")
        like = self._soql_literal(f"%{query}%")
        soql = (
            f"SELECT {fields} FROM Contact WHERE Email LIKE {like} "  # nosec B608
            f"OR LastName LIKE {like} LIMIT {self._limit(params)}"
        )
        return await self._query_records(soql)

    async def get_contact(self, **params) -> dict[str, Any]:
        """Get a Salesforce Contact by ID."""
        contact_id = params.get("contact_id") or params.get("id")
        if not contact_id:
            return {"error": "contact_id is required"}
        fields = self._fields(params, "Id,FirstName,LastName,Email,Phone,AccountId,Title")
        return await self._get(f"/sobjects/Contact/{contact_id}", {"fields": fields})

    async def create_contact(self, **params) -> dict[str, Any]:
        """Create a Salesforce Contact."""
        if not params.get("LastName"):
            return {"error": "LastName is required"}
        return await self._post("/sobjects/Contact", params)

    async def update_contact(self, **params) -> dict[str, Any]:
        """Update a Salesforce Contact."""
        contact_id = params.pop("contact_id", None) or params.pop("id", None)
        if not contact_id:
            return {"error": "contact_id is required"}
        return await self._patch(f"/sobjects/Contact/{contact_id}", params)

    async def delete_contact(self, **params) -> dict[str, Any]:
        """Delete a Salesforce Contact."""
        return await self._delete_sobject(
            "Contact",
            params.get("contact_id") or params.get("id"),
        )

    async def list_opportunities(self, **params) -> dict[str, Any]:
        """List Opportunities via SOQL."""
        fields = self._fields(params, "Id,Name,Amount,StageName,CloseDate,AccountId")
        stage_filter = ""
        if params.get("stage"):
            stage_filter = f" WHERE StageName={self._soql_literal(params['stage'])}"
        soql = (
            f"SELECT {fields} FROM Opportunity{stage_filter} "  # nosec B608
            f"ORDER BY CloseDate DESC LIMIT {self._limit(params)}"
        )
        return await self._query_records(soql)

    async def search_opportunities(self, **params) -> dict[str, Any]:
        """Search Salesforce Opportunities by Name."""
        query = params.get("query") or params.get("name")
        if not query:
            return {"error": "query is required"}
        fields = self._fields(params, "Id,Name,Amount,StageName,CloseDate,AccountId")
        like = self._soql_literal(f"%{query}%")
        soql = (
            f"SELECT {fields} FROM Opportunity WHERE Name LIKE {like} "  # nosec B608
            f"LIMIT {self._limit(params)}"
        )
        return await self._query_records(soql)

    async def get_opportunity(self, **params) -> dict[str, Any]:
        """Get a Salesforce Opportunity by ID."""
        opp_id = params.get("opportunity_id") or params.get("id")
        if not opp_id:
            return {"error": "opportunity_id is required"}
        fields = self._fields(params, "Id,Name,Amount,StageName,CloseDate,AccountId")
        return await self._get(f"/sobjects/Opportunity/{opp_id}", {"fields": fields})

    async def create_opportunity(self, **params) -> dict[str, Any]:
        """Create a Salesforce Opportunity."""
        for required in ("Name", "StageName", "CloseDate"):
            if not params.get(required):
                return {"error": f"{required} is required"}
        return await self._post("/sobjects/Opportunity", params)

    async def update_opportunity(self, **params) -> dict[str, Any]:
        """Update an Opportunity."""
        opp_id = params.pop("opportunity_id", None) or params.pop("id", None)
        if not opp_id:
            return {"error": "opportunity_id is required"}
        return await self._patch(f"/sobjects/Opportunity/{opp_id}", params)

    async def delete_opportunity(self, **params) -> dict[str, Any]:
        """Delete a Salesforce Opportunity."""
        return await self._delete_sobject(
            "Opportunity",
            params.get("opportunity_id") or params.get("id"),
        )

    async def create_task(self, **params) -> dict[str, Any]:
        """Create a Task (follow-up, call, meeting)."""
        if not params.get("Subject"):
            return {"error": "Subject is required"}
        return await self._post("/sobjects/Task", params)

    async def create_note(self, **params) -> dict[str, Any]:
        """Create a Salesforce Note attached to a parent record."""
        parent_id = params.get("ParentId") or params.get("parent_id")
        title = params.get("Title") or params.get("title")
        body = params.get("Body") or params.get("body")
        if not parent_id:
            return {"error": "ParentId is required"}
        if not title:
            return {"error": "Title is required"}
        payload = {"ParentId": parent_id, "Title": title}
        if body:
            payload["Body"] = body
        return await self._post("/sobjects/Note", payload)

    async def create_opportunity_contact_role(self, **params) -> dict[str, Any]:
        """Associate a contact to an opportunity using OpportunityContactRole."""
        opportunity_id = params.get("OpportunityId") or params.get("opportunity_id")
        contact_id = params.get("ContactId") or params.get("contact_id")
        if not opportunity_id:
            return {"error": "OpportunityId is required"}
        if not contact_id:
            return {"error": "ContactId is required"}
        payload = {
            "OpportunityId": opportunity_id,
            "ContactId": contact_id,
            "Role": params.get("Role") or params.get("role", "Decision Maker"),
            "IsPrimary": bool(params.get("IsPrimary") or params.get("is_primary", False)),
        }
        return await self._post("/sobjects/OpportunityContactRole", payload)
