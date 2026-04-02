"""PagerDuty connector — ops.

Integrates with PagerDuty REST API v2 for incident management,
on-call scheduling, alerting, and postmortem generation.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class PagerdutyConnector(BaseConnector):
    name = "pagerduty"
    category = "ops"
    auth_type = "api_key"
    base_url = "https://api.pagerduty.com"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_incident"] = self.create_incident
        self._tool_registry["acknowledge_incident"] = self.acknowledge_incident
        self._tool_registry["resolve_incident"] = self.resolve_incident
        self._tool_registry["get_on_call"] = self.get_on_call
        self._tool_registry["list_incidents"] = self.list_incidents
        self._tool_registry["create_postmortem"] = self.create_postmortem

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {
            "Authorization": f"Token token={api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2",
            "Content-Type": "application/json",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._get("/abilities")
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_incident(self, **params) -> dict[str, Any]:
        """Create an incident.

        Params: title (required), service_id (required),
                urgency (high/low), body (description),
                escalation_policy_id (optional).
        """
        incident: dict[str, Any] = {
            "type": "incident",
            "title": params["title"],
            "service": {"id": params["service_id"], "type": "service_reference"},
        }
        if params.get("urgency"):
            incident["urgency"] = params["urgency"]
        if params.get("body"):
            incident["body"] = {"type": "incident_body", "details": params["body"]}
        if params.get("escalation_policy_id"):
            incident["escalation_policy"] = {
                "id": params["escalation_policy_id"],
                "type": "escalation_policy_reference",
            }
        return await self._post("/incidents", {"incident": incident})

    async def acknowledge_incident(self, **params) -> dict[str, Any]:
        """Acknowledge an incident.

        Params: incident_id (required), from_email (required — PagerDuty user email).
        """
        incident_id = params["incident_id"]
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.put(
            f"/incidents/{incident_id}",
            json={"incident": {"type": "incident_reference", "status": "acknowledged"}},
            headers={**self._auth_headers, "From": params["from_email"]},
        )
        resp.raise_for_status()
        return resp.json()

    async def resolve_incident(self, **params) -> dict[str, Any]:
        """Resolve an incident.

        Params: incident_id (required), from_email (required),
                resolution (optional description).
        """
        incident_id = params["incident_id"]
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.put(
            f"/incidents/{incident_id}",
            json={"incident": {"type": "incident_reference", "status": "resolved"}},
            headers={**self._auth_headers, "From": params["from_email"]},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_on_call(self, **params) -> dict[str, Any]:
        """Get current on-call users.

        Params: schedule_ids (list, optional), escalation_policy_ids (list, optional).
        """
        return await self._get("/oncalls", params)

    async def list_incidents(self, **params) -> dict[str, Any]:
        """List incidents with filters.

        Params: statuses (list: triggered/acknowledged/resolved),
                service_ids (list), since (ISO8601), until, limit (default 25).
        """
        params.setdefault("limit", 25)
        return await self._get("/incidents", params)

    async def create_postmortem(self, **params) -> dict[str, Any]:
        """Create a postmortem report for an incident.

        Params: incident_id (required), description, detected_at, resolved_at,
                root_cause, remediation.
        Note: PagerDuty uses the postmortem feature (requires Postmortems add-on).
        """
        incident_id = params.pop("incident_id")
        return await self._post(f"/incidents/{incident_id}/postmortems", {"postmortem": params})
