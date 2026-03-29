"""HubSpot connector — real HubSpot CRM API v3 integration."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class HubspotConnector(BaseConnector):
    name = "hubspot"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://api.hubapi.com"
    rate_limit_rpm = 200

    def _register_tools(self):
        # Contacts
        self._tool_registry["list_contacts"] = self.list_contacts
        self._tool_registry["search_contacts"] = self.search_contacts
        self._tool_registry["create_contact"] = self.create_contact
        self._tool_registry["get_contact"] = self.get_contact
        self._tool_registry["update_contact"] = self.update_contact
        # Deals
        self._tool_registry["list_deals"] = self.list_deals
        self._tool_registry["create_deal"] = self.create_deal
        self._tool_registry["get_deal"] = self.get_deal
        self._tool_registry["update_deal"] = self.update_deal
        # Pipeline
        self._tool_registry["list_pipelines"] = self.list_pipelines
        # Companies
        self._tool_registry["list_companies"] = self.list_companies
        self._tool_registry["create_company"] = self.create_company
        # Analytics
        self._tool_registry["get_campaign_analytics"] = self.get_campaign_analytics

    async def _authenticate(self):
        # Always try OAuth refresh first (tokens expire every 30 min)
        refresh_token = self._get_secret("refresh_token")
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")

        if refresh_token and client_id:
            fresh_token = await self._refresh_oauth(client_id, client_secret, refresh_token)
            if fresh_token:
                self._auth_headers = {
                    "Authorization": f"Bearer {fresh_token}",
                    "Content-Type": "application/json",
                }
                return

        # Fallback: stored access_token / private app token
        access_token = self._get_secret("access_token") or self._get_secret("api_key")
        if access_token:
            self._auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

    async def _refresh_oauth(
        self, client_id: str, client_secret: str, refresh_token: str
    ) -> str | None:
        """Exchange refresh_token for a fresh access_token."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.hubapi.com/oauth/v1/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info("hubspot_token_refreshed", expires_in=data.get("expires_in"))
                return data["access_token"]
        except Exception as exc:
            logger.warning("hubspot_token_refresh_failed", error=str(exc))
            return None

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute tool with automatic 401 retry (re-authenticates and retries once)."""
        try:
            return await super().execute_tool(tool_name, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            # Token expired mid-session — refresh and retry
            logger.info("hubspot_401_retry", tool=tool_name)
            await self._authenticate()
            # Re-create client with fresh headers
            if self._client:
                await self._client.aclose()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_ms / 1000,
                headers=self._auth_headers,
            )
            return await super().execute_tool(tool_name, params)

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self._get("/crm/v3/objects/contacts", params={"limit": 1})
            return {"status": "healthy", "total_contacts": data.get("total", 0)}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Contacts ────────────────────────────────────────────────────────

    async def list_contacts(self, **params) -> dict[str, Any]:
        """List contacts with optional property selection."""
        limit = params.get("limit", 20)
        properties = params.get("properties", "firstname,lastname,email,company,phone")
        data = await self._get(
            "/crm/v3/objects/contacts",
            params={"limit": limit, "properties": properties},
        )
        return {
            "contacts": [
                {
                    "id": c["id"],
                    **c.get("properties", {}),
                }
                for c in data.get("results", [])
            ],
            "total": data.get("total", len(data.get("results", []))),
            "has_more": bool(data.get("paging", {}).get("next")),
        }

    async def search_contacts(self, **params) -> dict[str, Any]:
        """Search contacts by email, name, or custom properties."""
        query = params.get("query", "")
        if not query:
            return {"error": "query is required"}
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "email", "operator": "CONTAINS_TOKEN", "value": query},
                    ]
                }
            ],
            "properties": ["firstname", "lastname", "email", "company", "phone", "lifecyclestage"],
            "limit": params.get("limit", 10),
        }
        return await self._post("/crm/v3/objects/contacts/search", body)

    async def get_contact(self, **params) -> dict[str, Any]:
        """Get a contact by ID."""
        contact_id = params.get("contact_id", "")
        if not contact_id:
            return {"error": "contact_id is required"}
        return await self._get(
            f"/crm/v3/objects/contacts/{contact_id}",
            params={"properties": "firstname,lastname,email,company,phone,lifecyclestage,hs_lead_status"},
        )

    async def create_contact(self, **params) -> dict[str, Any]:
        """Create a new contact."""
        properties: dict[str, Any] = {}
        for field in ("email", "firstname", "lastname", "company", "phone", "jobtitle", "website"):
            if params.get(field):
                properties[field] = params[field]
        if not properties.get("email"):
            return {"error": "email is required"}
        return await self._post("/crm/v3/objects/contacts", {"properties": properties})

    async def update_contact(self, **params) -> dict[str, Any]:
        """Update contact properties."""
        contact_id = params.get("contact_id", "")
        if not contact_id:
            return {"error": "contact_id is required"}
        properties = {k: v for k, v in params.items() if k != "contact_id" and v is not None}
        return await self._patch(f"/crm/v3/objects/contacts/{contact_id}", {"properties": properties})

    # ── Deals ───────────────────────────────────────────────────────────

    async def list_deals(self, **params) -> dict[str, Any]:
        """List deals with properties."""
        limit = params.get("limit", 20)
        data = await self._get(
            "/crm/v3/objects/deals",
            params={
                "limit": limit,
                "properties": "dealname,amount,dealstage,pipeline,closedate,hubspot_owner_id",
            },
        )
        return {
            "deals": [
                {
                    "id": d["id"],
                    **d.get("properties", {}),
                }
                for d in data.get("results", [])
            ],
            "total": data.get("total", len(data.get("results", []))),
        }

    async def get_deal(self, **params) -> dict[str, Any]:
        """Get a deal by ID."""
        deal_id = params.get("deal_id", "")
        if not deal_id:
            return {"error": "deal_id is required"}
        return await self._get(
            f"/crm/v3/objects/deals/{deal_id}",
            params={"properties": "dealname,amount,dealstage,pipeline,closedate"},
        )

    async def create_deal(self, **params) -> dict[str, Any]:
        """Create a new deal in the pipeline."""
        properties: dict[str, Any] = {
            "dealname": params.get("dealname", ""),
            "pipeline": params.get("pipeline", "default"),
            "dealstage": params.get("dealstage", "appointmentscheduled"),
        }
        if params.get("amount"):
            properties["amount"] = str(params["amount"])
        if params.get("closedate"):
            properties["closedate"] = params["closedate"]
        if not properties["dealname"]:
            return {"error": "dealname is required"}
        return await self._post("/crm/v3/objects/deals", {"properties": properties})

    async def update_deal(self, **params) -> dict[str, Any]:
        """Update deal properties (stage, amount, etc.)."""
        deal_id = params.get("deal_id", "")
        if not deal_id:
            return {"error": "deal_id is required"}
        properties = {k: v for k, v in params.items() if k != "deal_id" and v is not None}
        return await self._patch(f"/crm/v3/objects/deals/{deal_id}", {"properties": properties})

    # ── Pipeline ────────────────────────────────────────────────────────

    async def list_pipelines(self, **params) -> dict[str, Any]:
        """List all deal pipelines and their stages."""
        data = await self._get("/crm/v3/pipelines/deals")
        return {
            "pipelines": [
                {
                    "id": p["id"],
                    "label": p.get("label"),
                    "stages": [
                        {"id": s["id"], "label": s.get("label"), "display_order": s.get("displayOrder")}
                        for s in p.get("stages", [])
                    ],
                }
                for p in data.get("results", [])
            ],
        }

    # ── Companies ───────────────────────────────────────────────────────

    async def list_companies(self, **params) -> dict[str, Any]:
        """List companies."""
        limit = params.get("limit", 20)
        data = await self._get(
            "/crm/v3/objects/companies",
            params={"limit": limit, "properties": "name,domain,industry,numberofemployees"},
        )
        return {
            "companies": [
                {"id": c["id"], **c.get("properties", {})}
                for c in data.get("results", [])
            ],
            "total": data.get("total", len(data.get("results", []))),
        }

    async def create_company(self, **params) -> dict[str, Any]:
        """Create a company."""
        properties: dict[str, Any] = {}
        for field in ("name", "domain", "industry", "phone", "city", "numberofemployees"):
            if params.get(field):
                properties[field] = params[field]
        if not properties.get("name"):
            return {"error": "name is required"}
        return await self._post("/crm/v3/objects/companies", {"properties": properties})

    # ── Analytics ───────────────────────────────────────────────────────

    async def get_campaign_analytics(self, **params) -> dict[str, Any]:
        """Get marketing email campaign analytics."""
        limit = params.get("limit", 10)
        data = await self._get(
            "/marketing/v1/emails",
            params={"limit": limit, "orderBy": "-updated"},
        )
        if not isinstance(data, dict) or "objects" not in data:
            return data if isinstance(data, dict) else {"raw": str(data)[:500]}
        return {
            "campaigns": [
                {
                    "id": e.get("id"),
                    "name": e.get("name"),
                    "subject": e.get("subject"),
                    "state": e.get("state"),
                    "stats": e.get("stats", {}),
                }
                for e in data.get("objects", [])
            ],
            "total": data.get("total", 0),
        }
