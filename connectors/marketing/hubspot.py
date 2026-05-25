"""HubSpot connector — real HubSpot CRM API v3 integration."""

from __future__ import annotations

from datetime import UTC, datetime
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
    tools = [
        "list_contacts",
        "search_contacts",
        "create_contact",
        "get_contact",
        "update_contact",
        "delete_contact",
        "list_deals",
        "search_deals",
        "create_deal",
        "get_deal",
        "update_deal",
        "delete_deal",
        "list_pipelines",
        "list_pipeline_stages",
        "list_companies",
        "search_companies",
        "create_company",
        "get_company",
        "update_company",
        "delete_company",
        "list_associations",
        "create_association",
        "delete_association",
        "list_tasks",
        "create_task",
        "list_notes",
        "create_note",
        "validate_crm_access",
        "get_campaign_analytics",
    ]

    def _register_tools(self):
        # Contacts
        self._tool_registry["list_contacts"] = self.list_contacts
        self._tool_registry["search_contacts"] = self.search_contacts
        self._tool_registry["create_contact"] = self.create_contact
        self._tool_registry["get_contact"] = self.get_contact
        self._tool_registry["update_contact"] = self.update_contact
        self._tool_registry["delete_contact"] = self.delete_contact
        # Deals
        self._tool_registry["list_deals"] = self.list_deals
        self._tool_registry["search_deals"] = self.search_deals
        self._tool_registry["create_deal"] = self.create_deal
        self._tool_registry["get_deal"] = self.get_deal
        self._tool_registry["update_deal"] = self.update_deal
        self._tool_registry["delete_deal"] = self.delete_deal
        # Pipeline
        self._tool_registry["list_pipelines"] = self.list_pipelines
        self._tool_registry["list_pipeline_stages"] = self.list_pipeline_stages
        # Companies
        self._tool_registry["list_companies"] = self.list_companies
        self._tool_registry["search_companies"] = self.search_companies
        self._tool_registry["create_company"] = self.create_company
        self._tool_registry["get_company"] = self.get_company
        self._tool_registry["update_company"] = self.update_company
        self._tool_registry["delete_company"] = self.delete_company
        # Associations and CRM activity objects
        self._tool_registry["list_associations"] = self.list_associations
        self._tool_registry["create_association"] = self.create_association
        self._tool_registry["delete_association"] = self.delete_association
        self._tool_registry["list_tasks"] = self.list_tasks
        self._tool_registry["create_task"] = self.create_task
        self._tool_registry["list_notes"] = self.list_notes
        self._tool_registry["create_note"] = self.create_note
        self._tool_registry["validate_crm_access"] = self.validate_crm_access
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
        # enterprise-gate: broad-except-ok reason=connector-oauth-refresh-falls-back-to-existing-token-or-fails-health
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

    def _properties_param(self, value: Any, default: str) -> str:
        if isinstance(value, list | tuple):
            return ",".join(str(item).strip() for item in value if str(item).strip())
        return str(value or default)

    def _timestamp(self, value: Any = None) -> str:
        if value:
            return str(value)
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.request(method, path, json=json_body, params=params)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {"status": "ok", "http_status": 204}
        try:
            return resp.json()
        except ValueError:
            return {"status": "ok", "http_status": resp.status_code}

    async def _delete_object(self, object_type: str, object_id: Any) -> dict[str, Any]:
        if not object_id:
            return {"error": f"{object_type[:-1]}_id is required"}
        await self._request_json("DELETE", f"/crm/v3/objects/{object_type}/{object_id}")
        return {"status": "deleted", "object_type": object_type, "id": str(object_id)}

    def _association_payload(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        raw = params.get("associations")
        if isinstance(raw, list):
            return raw
        object_id = params.get("associated_object_id")
        type_id = params.get("association_type_id")
        if not object_id or not type_id:
            return []
        return [
            {
                "to": {"id": str(object_id)},
                "types": [
                    {
                        "associationCategory": params.get(
                            "association_category",
                            "HUBSPOT_DEFINED",
                        ),
                        "associationTypeId": int(type_id),
                    }
                ],
            }
        ]

    async def _search_object(
        self,
        object_type: str,
        response_key: str,
        default_property: str,
        default_properties: list[str],
        **params: Any,
    ) -> dict[str, Any]:
        query = params.get("query") or params.get("value")
        if not query:
            return {"error": "query is required"}
        property_name = params.get("property_name") or default_property
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": str(property_name),
                            "operator": params.get("operator", "CONTAINS_TOKEN"),
                            "value": str(query),
                        },
                    ]
                }
            ],
            "properties": params.get("properties") or default_properties,
            "limit": params.get("limit", 10),
        }
        data = await self._post(f"/crm/v3/objects/{object_type}/search", body)
        return {
            response_key: [
                {"id": item["id"], **item.get("properties", {})}
                for item in data.get("results", [])
            ],
            "total": data.get("total", len(data.get("results", []))),
            "paging": data.get("paging", {}),
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self._get("/crm/v3/objects/contacts", params={"limit": 1})
            return {"status": "healthy", "total_contacts": data.get("total", 0)}
        # enterprise-gate: broad-except-ok reason=connector-health-boundary-reports-unhealthy
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
        return await self._search_object(
            "contacts",
            "contacts",
            "email",
            ["firstname", "lastname", "email", "company", "phone", "lifecyclestage"],
            **params,
        )

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
        contact_id = params.get("contact_id") or params.get("id")
        if not contact_id:
            return {"error": "contact_id is required"}
        properties = {
            k: v
            for k, v in params.items()
            if k not in {"contact_id", "id"} and v is not None
        }
        return await self._patch(f"/crm/v3/objects/contacts/{contact_id}", {"properties": properties})

    async def delete_contact(self, **params) -> dict[str, Any]:
        """Delete a contact by ID."""
        contact_id = params.get("contact_id") or params.get("id")
        return await self._delete_object("contacts", contact_id)

    # ── Deals ───────────────────────────────────────────────────────────

    async def list_deals(self, **params) -> dict[str, Any]:
        """List deals with properties."""
        limit = params.get("limit", 20)
        data = await self._get(
            "/crm/v3/objects/deals",
            params={
                "limit": limit,
                "properties": self._properties_param(
                    params.get("properties"),
                    "dealname,amount,dealstage,pipeline,closedate,hubspot_owner_id",
                ),
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

    async def search_deals(self, **params) -> dict[str, Any]:
        """Search deals by name, stage, pipeline, or another deal property."""
        return await self._search_object(
            "deals",
            "deals",
            "dealname",
            ["dealname", "amount", "dealstage", "pipeline", "closedate", "hubspot_owner_id"],
            **params,
        )

    async def get_deal(self, **params) -> dict[str, Any]:
        """Get a deal by ID."""
        deal_id = params.get("deal_id") or params.get("id")
        if not deal_id:
            return {"error": "deal_id is required"}
        return await self._get(
            f"/crm/v3/objects/deals/{deal_id}",
            params={
                "properties": self._properties_param(
                    params.get("properties"),
                    "dealname,amount,dealstage,pipeline,closedate",
                )
            },
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
        properties = {k: v for k, v in params.items() if k not in {"deal_id", "id"} and v is not None}
        return await self._patch(f"/crm/v3/objects/deals/{deal_id}", {"properties": properties})

    async def delete_deal(self, **params) -> dict[str, Any]:
        """Delete a deal by ID."""
        deal_id = params.get("deal_id") or params.get("id")
        return await self._delete_object("deals", deal_id)

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

    async def list_pipeline_stages(self, **params) -> dict[str, Any]:
        """List stages for one pipeline, or flatten stages for all deal pipelines."""
        pipeline_id = params.get("pipeline_id")
        if pipeline_id:
            data = await self._get(f"/crm/v3/pipelines/deals/{pipeline_id}/stages")
            return {
                "pipeline_id": str(pipeline_id),
                "stages": [
                    {
                        "id": stage["id"],
                        "label": stage.get("label"),
                        "display_order": stage.get("displayOrder"),
                    }
                    for stage in data.get("results", [])
                ],
            }
        pipelines = await self.list_pipelines()
        stages: list[dict[str, Any]] = []
        for pipeline in pipelines.get("pipelines", []):
            for stage in pipeline.get("stages", []):
                stages.append({"pipeline_id": pipeline.get("id"), **stage})
        return {"stages": stages, "pipelines": pipelines.get("pipelines", [])}

    # ── Companies ───────────────────────────────────────────────────────

    async def list_companies(self, **params) -> dict[str, Any]:
        """List companies."""
        limit = params.get("limit", 20)
        data = await self._get(
            "/crm/v3/objects/companies",
            params={
                "limit": limit,
                "properties": self._properties_param(
                    params.get("properties"),
                    "name,domain,industry,numberofemployees,city,phone",
                ),
            },
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

    async def search_companies(self, **params) -> dict[str, Any]:
        """Search companies by name, domain, or another company property."""
        default_property = "domain" if "." in str(params.get("query") or "") else "name"
        return await self._search_object(
            "companies",
            "companies",
            default_property,
            ["name", "domain", "industry", "numberofemployees", "city", "phone"],
            **params,
        )

    async def get_company(self, **params) -> dict[str, Any]:
        """Get a company by ID."""
        company_id = params.get("company_id") or params.get("id")
        if not company_id:
            return {"error": "company_id is required"}
        return await self._get(
            f"/crm/v3/objects/companies/{company_id}",
            params={
                "properties": self._properties_param(
                    params.get("properties"),
                    "name,domain,industry,numberofemployees,city,phone",
                )
            },
        )

    async def update_company(self, **params) -> dict[str, Any]:
        """Update company properties."""
        company_id = params.get("company_id") or params.get("id")
        if not company_id:
            return {"error": "company_id is required"}
        properties = {
            k: v
            for k, v in params.items()
            if k not in {"company_id", "id"} and v is not None
        }
        return await self._patch(
            f"/crm/v3/objects/companies/{company_id}",
            {"properties": properties},
        )

    async def delete_company(self, **params) -> dict[str, Any]:
        """Delete a company by ID."""
        company_id = params.get("company_id") or params.get("id")
        return await self._delete_object("companies", company_id)

    async def list_associations(self, **params) -> dict[str, Any]:
        """List associations from one CRM object to another object type."""
        from_type = params.get("from_object_type") or params.get("from_type")
        from_id = params.get("from_object_id") or params.get("from_id")
        to_type = params.get("to_object_type") or params.get("to_type")
        if not from_type or not from_id or not to_type:
            return {
                "error": (
                    "from_object_type, from_object_id, and to_object_type "
                    "are required"
                )
            }
        return await self._get(
            f"/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}"
        )

    async def create_association(self, **params) -> dict[str, Any]:
        """Create a default HubSpot association between two CRM objects."""
        from_type = params.get("from_object_type") or params.get("from_type")
        from_id = params.get("from_object_id") or params.get("from_id")
        to_type = params.get("to_object_type") or params.get("to_type")
        to_id = params.get("to_object_id") or params.get("to_id")
        if not from_type or not from_id or not to_type or not to_id:
            return {
                "error": (
                    "from_object_type, from_object_id, to_object_type, "
                    "and to_object_id are required"
                )
            }
        data = await self._request_json(
            "PUT",
            f"/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_type}/{to_id}",
        )
        return {"status": "associated", "response": data}

    async def delete_association(self, **params) -> dict[str, Any]:
        """Delete an association between two CRM objects."""
        from_type = params.get("from_object_type") or params.get("from_type")
        from_id = params.get("from_object_id") or params.get("from_id")
        to_type = params.get("to_object_type") or params.get("to_type")
        to_id = params.get("to_object_id") or params.get("to_id")
        if not from_type or not from_id or not to_type or not to_id:
            return {
                "error": (
                    "from_object_type, from_object_id, to_object_type, "
                    "and to_object_id are required"
                )
            }
        await self._request_json(
            "DELETE",
            f"/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}/{to_id}",
        )
        return {"status": "deleted", "from": str(from_id), "to": str(to_id)}

    async def list_tasks(self, **params) -> dict[str, Any]:
        """List CRM tasks."""
        data = await self._get(
            "/crm/v3/objects/tasks",
            params={
                "limit": params.get("limit", 20),
                "properties": self._properties_param(
                    params.get("properties"),
                    "hs_task_subject,hs_task_status,hs_task_priority,hs_timestamp",
                ),
            },
        )
        return {"tasks": data.get("results", []), "total": data.get("total", 0)}

    async def create_task(self, **params) -> dict[str, Any]:
        """Create a HubSpot CRM task."""
        subject = params.get("subject") or params.get("hs_task_subject")
        if not subject:
            return {"error": "subject is required"}
        properties = {
            "hs_task_subject": subject,
            "hs_task_status": params.get("status", "NOT_STARTED"),
            "hs_task_priority": params.get("priority", "MEDIUM"),
            "hs_timestamp": self._timestamp(
                params.get("timestamp") or params.get("hs_timestamp")
            ),
        }
        if params.get("body") or params.get("description"):
            properties["hs_task_body"] = params.get("body") or params.get("description")
        payload: dict[str, Any] = {"properties": properties}
        associations = self._association_payload(params)
        if associations:
            payload["associations"] = associations
        return await self._post("/crm/v3/objects/tasks", payload)

    async def list_notes(self, **params) -> dict[str, Any]:
        """List CRM notes."""
        data = await self._get(
            "/crm/v3/objects/notes",
            params={
                "limit": params.get("limit", 20),
                "properties": self._properties_param(
                    params.get("properties"),
                    "hs_note_body,hs_timestamp,hubspot_owner_id",
                ),
            },
        )
        return {"notes": data.get("results", []), "total": data.get("total", 0)}

    async def create_note(self, **params) -> dict[str, Any]:
        """Create a HubSpot CRM note."""
        body = params.get("body") or params.get("note") or params.get("hs_note_body")
        if not body:
            return {"error": "body is required"}
        payload: dict[str, Any] = {
            "properties": {
                "hs_note_body": body,
                "hs_timestamp": self._timestamp(
                    params.get("timestamp") or params.get("hs_timestamp")
                ),
            }
        }
        associations = self._association_payload(params)
        if associations:
            payload["associations"] = associations
        return await self._post("/crm/v3/objects/notes", payload)

    async def validate_crm_access(self, **_params) -> dict[str, Any]:
        """Validate read access to core CRM objects without mutating data."""
        checks: dict[str, dict[str, Any]] = {}
        for object_type in ("contacts", "companies", "deals"):
            try:
                data = await self._get(
                    f"/crm/v3/objects/{object_type}",
                    params={"limit": 1},
                )
                checks[object_type] = {
                    "status": "ready",
                    "total": data.get("total", len(data.get("results", []))),
                }
            except httpx.HTTPStatusError as exc:
                checks[object_type] = {
                    "status": "blocked",
                    "http_status": exc.response.status_code,
                }
            # enterprise-gate: broad-except-ok reason=hubspot-object-access-probe-reports-blocked
            except Exception as exc:  # noqa: BLE001
                checks[object_type] = {
                    "status": "blocked",
                    "error_type": type(exc).__name__,
                }
        ready = all(row.get("status") == "ready" for row in checks.values())
        return {"status": "healthy" if ready else "unhealthy", "objects": checks}

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
