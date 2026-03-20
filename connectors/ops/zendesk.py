"""Zendesk connector — ops."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class ZendeskConnector(BaseConnector):
    name = "zendesk"
    category = "ops"
    auth_type = "api_token"
    base_url = "https://org.zendesk.com/api/v2"
    rate_limit_rpm = 200

    def _register_tools(self):
    self._tool_registry["create_ticket"] = self.create_ticket
    self._tool_registry["update_ticket"] = self.update_ticket
    self._tool_registry["apply_macro"] = self.apply_macro
    self._tool_registry["get_csat_score"] = self.get_csat_score
    self._tool_registry["escalate_to_group"] = self.escalate_to_group
    self._tool_registry["merge_tickets"] = self.merge_tickets
    self._tool_registry["get_sla_breach_status"] = self.get_sla_breach_status
    self._tool_registry["get_ticket_history"] = self.get_ticket_history

    async def _authenticate(self):
        self._auth_headers = {"Authorization": "Bearer <token>"}

async def create_ticket(self, **params):
    """Execute create_ticket on zendesk."""
    return await self._post("/create/ticket", params)


async def update_ticket(self, **params):
    """Execute update_ticket on zendesk."""
    return await self._post("/update/ticket", params)


async def apply_macro(self, **params):
    """Execute apply_macro on zendesk."""
    return await self._post("/apply/macro", params)


async def get_csat_score(self, **params):
    """Execute get_csat_score on zendesk."""
    return await self._post("/get/csat/score", params)


async def escalate_to_group(self, **params):
    """Execute escalate_to_group on zendesk."""
    return await self._post("/escalate/to/group", params)


async def merge_tickets(self, **params):
    """Execute merge_tickets on zendesk."""
    return await self._post("/merge/tickets", params)


async def get_sla_breach_status(self, **params):
    """Execute get_sla_breach_status on zendesk."""
    return await self._post("/get/sla/breach/status", params)


async def get_ticket_history(self, **params):
    """Execute get_ticket_history on zendesk."""
    return await self._post("/get/ticket/history", params)

