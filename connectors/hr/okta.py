"""Okta connector — hr."""
from __future__ import annotations
from typing import Any
import httpx
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
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        token_url = self.config.get("token_url", f"{self.base_url}/oauth2/token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            })
            resp.raise_for_status()
            token = resp.json()["access_token"]
        self._auth_headers = {"Authorization": f"Bearer {token}"}

async def provision_user(self, **params):
    """Execute provision_user on okta."""
    return await self._post("/provision/user", params)


async def deactivate_user(self, **params):
    """Execute deactivate_user on okta."""
    return await self._post("/deactivate/user", params)


async def assign_group(self, **params):
    """Execute assign_group on okta."""
    return await self._post("/assign/group", params)


async def remove_group(self, **params):
    """Execute remove_group on okta."""
    return await self._post("/remove/group", params)


async def get_access_log(self, **params):
    """Execute get_access_log on okta."""
    return await self._post("/get/access/log", params)


async def reset_mfa(self, **params):
    """Execute reset_mfa on okta."""
    return await self._post("/reset/mfa", params)


async def list_active_sessions(self, **params):
    """Execute list_active_sessions on okta."""
    return await self._post("/list/active/sessions", params)


async def suspend_user(self, **params):
    """Execute suspend_user on okta."""
    return await self._post("/suspend/user", params)

