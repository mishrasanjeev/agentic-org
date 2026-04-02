"""Okta connector — HR / Identity.

Integrates with Okta Management API v1 for user lifecycle,
group management, access logging, and MFA operations.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class OktaConnector(BaseConnector):
    name = "okta"
    category = "hr"
    auth_type = "scim_oauth2"
    base_url = "https://org.okta.com/api/v1"
    rate_limit_rpm = 300

    def _register_tools(self):
        self._tool_registry["provision_user"] = self.provision_user
        self._tool_registry["deactivate_user"] = self.deactivate_user
        self._tool_registry["assign_group"] = self.assign_group
        self._tool_registry["remove_group"] = self.remove_group
        self._tool_registry["get_access_log"] = self.get_access_log
        self._tool_registry["reset_mfa"] = self.reset_mfa
        self._tool_registry["list_active_sessions"] = self.list_active_sessions
        self._tool_registry["suspend_user"] = self.suspend_user

    async def _authenticate(self):
        api_token = self._get_secret("api_token")
        self._auth_headers = {
            "Authorization": f"SSWS {api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._get("/users", {"limit": "1"})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def provision_user(self, **params) -> dict[str, Any]:
        """Create a new user in Okta.

        Params: firstName (required), lastName (required), email (required),
                login (optional — defaults to email), activate (bool, default true),
                groupIds (optional list).
        """
        profile = {
            "firstName": params.get("firstName", ""),
            "lastName": params.get("lastName", ""),
            "email": params.get("email", ""),
            "login": params.get("login", params.get("email", "")),
        }
        body: dict[str, Any] = {"profile": profile}
        if params.get("groupIds"):
            body["groupIds"] = params["groupIds"]
        activate = params.get("activate", True)
        return await self._post(f"/users?activate={str(activate).lower()}", body)

    async def deactivate_user(self, **params) -> dict[str, Any]:
        """Deactivate a user (disable login without deleting).

        Params: user_id (required).
        """
        user_id = params["user_id"]
        return await self._post(f"/users/{user_id}/lifecycle/deactivate", {})

    async def assign_group(self, **params) -> dict[str, Any]:
        """Add a user to a group.

        Params: group_id (required), user_id (required).
        """
        group_id = params["group_id"]
        user_id = params["user_id"]
        return await self._put(f"/groups/{group_id}/users/{user_id}", {})

    async def remove_group(self, **params) -> dict[str, Any]:
        """Remove a user from a group.

        Params: group_id (required), user_id (required).
        """
        group_id = params["group_id"]
        user_id = params["user_id"]
        return await self._delete(f"/groups/{group_id}/users/{user_id}")

    async def get_access_log(self, **params) -> dict[str, Any]:
        """Get system access logs (login events, etc.).

        Params: since (ISO8601), until (ISO8601), filter (optional Okta filter expression),
                limit (default 100).
        """
        params.setdefault("limit", "100")
        result = await self._get("/logs", params)
        return {"events": result if isinstance(result, list) else [result]}

    async def reset_mfa(self, **params) -> dict[str, Any]:
        """Reset all MFA factors for a user.

        Params: user_id (required).
        """
        user_id = params["user_id"]
        return await self._post(f"/users/{user_id}/lifecycle/reset_factors", {})

    async def list_active_sessions(self, **params) -> dict[str, Any]:
        """List active sessions for a user.

        Params: user_id (required).
        """
        user_id = params["user_id"]
        result = await self._get(f"/users/{user_id}/sessions")
        return {"sessions": result if isinstance(result, list) else [result]}

    async def suspend_user(self, **params) -> dict[str, Any]:
        """Suspend a user (temporary lockout).

        Params: user_id (required).
        """
        user_id = params["user_id"]
        return await self._post(f"/users/{user_id}/lifecycle/suspend", {})
