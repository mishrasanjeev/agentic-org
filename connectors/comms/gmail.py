"""Gmail connector — comms."""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector


class GmailConnector(BaseConnector):
    name = "gmail"
    category = "comms"
    auth_type = "oauth2"
    base_url = "https://gmail.googleapis.com/gmail/v1"
    rate_limit_rpm = 250

    def _register_tools(self):
        self._tool_registry["send_email"] = self.send_email
        self._tool_registry["read_inbox"] = self.read_inbox
        self._tool_registry["search_emails"] = self.search_emails
        self._tool_registry["get_thread"] = self.get_thread

    async def _authenticate(self):
        """Authenticate using OAuth2 client credentials or a provided refresh token."""
        refresh_token = self._get_secret("refresh_token")
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        token_url = self.config.get(
            "token_url", "https://oauth2.googleapis.com/token"
        )

        if refresh_token and client_id and client_secret:
            # Exchange refresh token for access token
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
                resp.raise_for_status()
                token = resp.json()["access_token"]
        else:
            # Fall back to pre-configured access token
            token = self._get_secret("access_token")

        self._auth_headers = {"Authorization": f"Bearer {token}"}

    # ── Tools ────────────────────────────────────────────────────────────────

    async def send_email(
        self,
        to: str = "",
        subject: str = "",
        body: str = "",
        cc: str = "",
        bcc: str = "",
        **_extra: Any,
    ) -> dict[str, Any]:
        """Send an email via the Gmail API."""
        user_id = self.config.get("user_id", "me")

        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        if cc:
            msg["cc"] = cc
        if bcc:
            msg["bcc"] = bcc

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        return await self._post(
            f"/users/{user_id}/messages/send",
            data={"raw": raw},
        )

    async def read_inbox(
        self,
        max_results: int = 20,
        label_ids: list[str] | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """List messages in the user's inbox."""
        user_id = self.config.get("user_id", "me")
        params: dict[str, Any] = {"maxResults": max_results}
        if label_ids:
            params["labelIds"] = ",".join(label_ids)
        else:
            params["labelIds"] = "INBOX"
        return await self._get(f"/users/{user_id}/messages", params=params)

    async def search_emails(
        self,
        query: str = "",
        max_results: int = 20,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Search emails using Gmail query syntax."""
        user_id = self.config.get("user_id", "me")
        params: dict[str, Any] = {
            "q": query,
            "maxResults": max_results,
        }
        return await self._get(f"/users/{user_id}/messages", params=params)

    async def get_thread(
        self,
        thread_id: str = "",
        **_extra: Any,
    ) -> dict[str, Any]:
        """Retrieve a full email thread by ID."""
        user_id = self.config.get("user_id", "me")
        return await self._get(f"/users/{user_id}/threads/{thread_id}")
