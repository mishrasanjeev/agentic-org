"""ServiceNow connector — ops.

Integrates with ServiceNow Table API for incident management,
change requests, CMDB, and service catalog fulfillment.
"""

from __future__ import annotations

from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector


class ServicenowConnector(BaseConnector):
    name = "servicenow"
    category = "ops"
    auth_type = "rest_oauth2"
    base_url = "https://org.service-now.com/api/now"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_incident"] = self.create_incident
        self._tool_registry["update_incident"] = self.update_incident
        self._tool_registry["submit_change_request"] = self.submit_change_request
        self._tool_registry["get_cmdb_ci"] = self.get_cmdb_ci
        self._tool_registry["check_sla_status"] = self.check_sla_status
        self._tool_registry["get_kb_article"] = self.get_kb_article

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        username = self.config.get("username", "")
        password = self.config.get("password", "")

        instance = self.config.get("instance", "org")
        token_url = f"https://{instance}.service-now.com/oauth_token.do"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "password",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "username": username,
                    "password": password,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]

        self._auth_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._get("/table/incident", {"sysparm_limit": "1"})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_incident(self, **params) -> dict[str, Any]:
        """Create an incident.

        Params: short_description (required), description, urgency (1/2/3),
                impact (1/2/3), category, assignment_group, caller_id.
        """
        return await self._post("/table/incident", params)

    async def update_incident(self, **params) -> dict[str, Any]:
        """Update an incident.

        Params: sys_id (required), state, work_notes, close_notes,
                assigned_to, resolution_code.
        """
        sys_id = params.pop("sys_id")
        return await self._patch(f"/table/incident/{sys_id}", params)

    async def submit_change_request(self, **params) -> dict[str, Any]:
        """Submit a change request.

        Params: short_description (required), description, type (normal/emergency/standard),
                risk (high/moderate/low), impact, assignment_group,
                start_date, end_date.
        """
        return await self._post("/table/change_request", params)

    async def get_cmdb_ci(self, **params) -> dict[str, Any]:
        """Get a CMDB configuration item.

        Params: sys_id (required) or name (for search).
        """
        if params.get("sys_id"):
            return await self._get(f"/table/cmdb_ci/{params['sys_id']}")
        return await self._get("/table/cmdb_ci", {"sysparm_query": f"name={params.get('name', '')}", "sysparm_limit": "10"})

    async def check_sla_status(self, **params) -> dict[str, Any]:
        """Check SLA status for a task.

        Params: task_sys_id (required).
        """
        task_id = params["task_sys_id"]
        return await self._get("/table/task_sla", {"sysparm_query": f"task={task_id}"})

    async def get_kb_article(self, **params) -> dict[str, Any]:
        """Search knowledge base articles.

        Params: query (required — search text), limit (default 10).
        """
        return await self._get("/table/kb_knowledge", {
            "sysparm_query": f"textLIKE{params.get('query', '')}",
            "sysparm_limit": str(params.get("limit", 10)),
        })
