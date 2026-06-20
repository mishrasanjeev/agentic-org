"""Zendesk connector — ops / support.

Integrates with Zendesk Support API v2 for ticket management,
SLA monitoring, CSAT scores, and macro application.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlparse

from connectors.framework.base_connector import BaseConnector
from connectors.framework.url_security import require_dns_label


class ZendeskConnector(BaseConnector):
    name = "zendesk"
    category = "ops"
    auth_type = "api_token"
    base_url = "https://org.zendesk.com/api/v2"
    rate_limit_rpm = 200

    def __init__(self, config: dict[str, Any] | None = None):
        safe_config = dict(config or {})
        raw_base_url = str(safe_config.pop("base_url", "") or "")
        subdomain = safe_config.get("subdomain") or safe_config.get("domain")
        if not subdomain and raw_base_url:
            parsed = urlparse(raw_base_url)
            host = (parsed.hostname or "").rstrip(".").lower()
            if host.endswith(".zendesk.com"):
                subdomain = host.removesuffix(".zendesk.com")
        if subdomain:
            safe_subdomain = require_dns_label(subdomain, "Zendesk subdomain")
            safe_config["subdomain"] = safe_subdomain
            self.base_url = f"https://{safe_subdomain}.zendesk.com/api/v2"
        super().__init__(safe_config)

    def _register_tools(self):
        self._tool_registry["create_ticket"] = self.create_ticket
        self._tool_registry["update_ticket"] = self.update_ticket
        self._tool_registry["get_ticket"] = self.get_ticket
        self._tool_registry["apply_macro"] = self.apply_macro
        self._tool_registry["get_csat_score"] = self.get_csat_score
        self._tool_registry["escalate_ticket"] = self.escalate_ticket
        self._tool_registry["merge_tickets"] = self.merge_tickets
        self._tool_registry["get_sla_status"] = self.get_sla_status

    async def _authenticate(self):
        email = self._get_secret("email")
        api_token = self._get_secret("api_token")
        credentials = base64.b64encode(f"{email}/token:{api_token}".encode()).decode()
        self._auth_headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._get("/tickets", {"per_page": "1"})
            return {"status": "healthy"}
        # enterprise-gate: broad-except-ok reason=connector-health-boundary-reports-unhealthy
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_ticket(self, **params) -> dict[str, Any]:
        """Create a support ticket.

        Params: subject (required), description (required),
                priority (low/normal/high/urgent), type (problem/incident/question/task),
                requester_id or requester ({name, email}), assignee_id, tags (list).
        """
        return await self._post("/tickets", {"ticket": params})

    async def update_ticket(self, **params) -> dict[str, Any]:
        """Update a ticket (status, priority, assignee, comment).

        Params: ticket_id (required), status (open/pending/solved/closed),
                priority, assignee_id, comment ({body, public}).
        """
        ticket_id = params.pop("ticket_id")
        return await self._put(f"/tickets/{ticket_id}", {"ticket": params})

    async def get_ticket(self, **params) -> dict[str, Any]:
        """Get ticket details.

        Params: ticket_id (required).
        """
        ticket_id = params["ticket_id"]
        return await self._get(f"/tickets/{ticket_id}")

    async def apply_macro(self, **params) -> dict[str, Any]:
        """Apply a macro to a ticket (preview the changes).

        Params: ticket_id (required), macro_id (required).
        """
        ticket_id = params["ticket_id"]
        macro_id = params["macro_id"]
        return await self._get(f"/tickets/{ticket_id}/macros/{macro_id}/apply")

    async def get_csat_score(self, **params) -> dict[str, Any]:
        """Get CSAT satisfaction ratings.

        Params: score (good/bad/offered/unoffered, optional),
                start_time (unix timestamp), end_time.
        """
        return await self._get("/satisfaction_ratings", params)

    async def escalate_ticket(self, **params) -> dict[str, Any]:
        """Escalate a ticket to a group.

        Params: ticket_id (required), group_id (required), comment (optional).
        """
        ticket_id = params.pop("ticket_id")
        update: dict[str, Any] = {"group_id": params["group_id"]}
        if params.get("comment"):
            update["comment"] = {"body": params["comment"], "public": False}
        return await self._put(f"/tickets/{ticket_id}", {"ticket": update})

    async def merge_tickets(self, **params) -> dict[str, Any]:
        """Merge tickets into a target ticket.

        Params: target_ticket_id (required), source_ticket_ids (list of IDs),
                target_comment (optional), source_comment (optional).
        """
        target_id = params.pop("target_ticket_id")
        return await self._post(f"/tickets/{target_id}/merge", params)

    async def get_sla_status(self, **params) -> dict[str, Any]:
        """Get SLA policy metrics for a ticket.

        Params: ticket_id (required).
        """
        ticket_id = params["ticket_id"]
        result = await self._get(f"/tickets/{ticket_id}")
        ticket = result.get("ticket", result)
        return {
            "ticket_id": ticket_id,
            "sla_policy": ticket.get("sla_policy", {}),
            "satisfaction_rating": ticket.get("satisfaction_rating", {}),
        }
