"""SendGrid connector — real SendGrid v3 API integration."""

from __future__ import annotations

from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class SendgridConnector(BaseConnector):
    name = "sendgrid"
    category = "comms"
    auth_type = "api_key"
    base_url = "https://api.sendgrid.com/v3"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["send_email"] = self.send_email
        self._tool_registry["create_template"] = self.create_template
        self._tool_registry["get_stats"] = self.get_stats
        self._tool_registry["get_bounces"] = self.get_bounces
        self._tool_registry["validate_email"] = self.validate_email
        self._tool_registry["send_email_with_tracking"] = self.send_email_with_tracking
        self._tool_registry["get_email_activity"] = self.get_email_activity

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        if not api_key:
            api_key = self._get_secret("access_token")
        self._auth_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self._get("/scopes")
            return {
                "status": "healthy",
                "scopes": data.get("scopes", [])[:10],
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Send Email ─────────────────────────────────────────────────────

    async def send_email(self, **params) -> dict[str, Any]:
        """Send a transactional email via SendGrid v3 mail/send.

        Required params:
            to: Recipient email (string) or list of email strings
            from_email: Sender email address
            subject: Email subject line
        Optional params:
            html: HTML body content
            text: Plain text body content (used if html not provided)
            from_name: Sender display name
            reply_to: Reply-to email address
            cc: CC email or list of emails
            bcc: BCC email or list of emails
            template_id: Dynamic template ID (overrides subject/content)
            dynamic_template_data: dict of merge variables for the template
        """
        to = params.get("to", "")
        from_email = params.get("from_email", "")
        subject = params.get("subject", "")
        if not to or not from_email:
            return {"error": "to and from_email are required"}

        # Normalize recipients to list
        to_list = to if isinstance(to, list) else [to]
        to_addresses = [{"email": addr} for addr in to_list]

        personalization: dict[str, Any] = {"to": to_addresses}
        if params.get("cc"):
            cc = params["cc"] if isinstance(params["cc"], list) else [params["cc"]]
            personalization["cc"] = [{"email": addr} for addr in cc]
        if params.get("bcc"):
            bcc = params["bcc"] if isinstance(params["bcc"], list) else [params["bcc"]]
            personalization["bcc"] = [{"email": addr} for addr in bcc]
        if params.get("dynamic_template_data"):
            personalization["dynamic_template_data"] = params["dynamic_template_data"]

        from_obj: dict[str, Any] = {"email": from_email}
        if params.get("from_name"):
            from_obj["name"] = params["from_name"]

        body: dict[str, Any] = {
            "personalizations": [personalization],
            "from": from_obj,
        }

        if params.get("template_id"):
            body["template_id"] = params["template_id"]
        else:
            if not subject:
                return {"error": "subject is required when not using a template"}
            body["subject"] = subject
            html = params.get("html", "")
            text = params.get("text", "")
            if html:
                body["content"] = [{"type": "text/html", "value": html}]
            elif text:
                body["content"] = [{"type": "text/plain", "value": text}]
            else:
                return {"error": "html or text content is required when not using a template"}

        if params.get("reply_to"):
            body["reply_to"] = {"email": params["reply_to"]}

        # SendGrid returns 202 Accepted with empty body for mail/send
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.post("/mail/send", json=body)
        resp.raise_for_status()

        return {
            "status": "accepted",
            "status_code": resp.status_code,
            "message_id": resp.headers.get("X-Message-Id", ""),
        }

    # ── Create Template ────────────────────────────────────────────────

    async def create_template(self, **params) -> dict[str, Any]:
        """Create a dynamic email template.

        Required params:
            name: Template name
        Optional params:
            generation: Template generation — "dynamic" (default) or "legacy"
        """
        name = params.get("name", "")
        if not name:
            return {"error": "name is required"}

        body = {
            "name": name,
            "generation": params.get("generation", "dynamic"),
        }
        data = await self._post("/templates", body)
        return {
            "template_id": data.get("id"),
            "name": data.get("name"),
            "generation": data.get("generation"),
            "versions": data.get("versions", []),
        }

    # ── Get Stats ──────────────────────────────────────────────────────

    async def get_stats(self, **params) -> dict[str, Any]:
        """Retrieve email sending statistics.

        Required params:
            start_date: Start date (YYYY-MM-DD)
        Optional params:
            end_date: End date (YYYY-MM-DD, defaults to today)
            aggregated_by: Aggregation period — "day", "week", or "month"
        """
        start_date = params.get("start_date", "")
        if not start_date:
            return {"error": "start_date is required"}

        query_params: dict[str, Any] = {"start_date": start_date}
        if params.get("end_date"):
            query_params["end_date"] = params["end_date"]
        if params.get("aggregated_by"):
            query_params["aggregated_by"] = params["aggregated_by"]

        # SendGrid /stats returns a list, not a dict
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.get("/stats", params=query_params)
        resp.raise_for_status()
        raw = resp.json()

        # Normalize: raw is a list of {date, stats: [{metrics: {...}}]}
        if isinstance(raw, list):
            return {
                "stats": [
                    {
                        "date": entry.get("date", ""),
                        "metrics": entry.get("stats", [{}])[0].get("metrics", {}),
                    }
                    for entry in raw
                ],
            }
        return raw if isinstance(raw, dict) else {"raw": raw}

    # ── Get Bounces ────────────────────────────────────────────────────

    async def get_bounces(self, **params) -> dict[str, Any]:
        """Retrieve bounced email addresses from the suppression list.

        Optional params:
            start_time: Start time (Unix timestamp)
            end_time: End time (Unix timestamp)
            limit: Max results (default 50)
            offset: Pagination offset
        """
        query_params: dict[str, Any] = {}
        if params.get("start_time"):
            query_params["start_time"] = params["start_time"]
        if params.get("end_time"):
            query_params["end_time"] = params["end_time"]
        query_params["limit"] = params.get("limit", 50)
        if params.get("offset"):
            query_params["offset"] = params["offset"]

        # /suppression/bounces returns a list
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.get("/suppression/bounces", params=query_params)
        resp.raise_for_status()
        raw = resp.json()

        if isinstance(raw, list):
            return {
                "bounces": [
                    {
                        "email": b.get("email", ""),
                        "reason": b.get("reason", ""),
                        "status": b.get("status", ""),
                        "created": b.get("created", 0),
                    }
                    for b in raw
                ],
                "count": len(raw),
            }
        return raw if isinstance(raw, dict) else {"raw": raw}

    # ── Validate Email ─────────────────────────────────────────────────

    async def validate_email(self, **params) -> dict[str, Any]:
        """Validate an email address using SendGrid Email Validation API.

        Required params:
            email: Email address to validate
        Optional params:
            source: Source identifier for tracking (e.g. "signup")
        """
        email = params.get("email", "")
        if not email:
            return {"error": "email is required"}

        body: dict[str, Any] = {"email": email}
        if params.get("source"):
            body["source"] = params["source"]

        data = await self._post("/validations/email", body)
        result = data.get("result", data)
        return {
            "email": result.get("email", email),
            "verdict": result.get("verdict", ""),
            "score": result.get("score", 0),
            "local": result.get("local", ""),
            "host": result.get("host", ""),
            "has_mx_record": result.get("checks", {}).get("domain", {}).get("has_mx_or_a_record"),
            "is_suspected_role": result.get("checks", {}).get("additional", {}).get("is_suspected_role_address"),
        }

    # ── Send Email with Tracking ──────────────────────────────────────

    async def send_email_with_tracking(self, **params) -> dict[str, Any]:
        """Send an email with open and click tracking enabled.

        Same parameters as send_email, plus:
        Optional params:
            categories: list of category tags for grouping analytics
        Automatically injects tracking_settings for open and click tracking.
        """
        to = params.get("to", "")
        from_email = params.get("from_email", "")
        subject = params.get("subject", "")
        if not to or not from_email:
            return {"error": "to and from_email are required"}

        # Normalize recipients to list
        to_list = to if isinstance(to, list) else [to]
        to_addresses = [{"email": addr} for addr in to_list]

        personalization: dict[str, Any] = {"to": to_addresses}
        if params.get("cc"):
            cc = params["cc"] if isinstance(params["cc"], list) else [params["cc"]]
            personalization["cc"] = [{"email": addr} for addr in cc]
        if params.get("bcc"):
            bcc = params["bcc"] if isinstance(params["bcc"], list) else [params["bcc"]]
            personalization["bcc"] = [{"email": addr} for addr in bcc]
        if params.get("dynamic_template_data"):
            personalization["dynamic_template_data"] = params["dynamic_template_data"]

        from_obj: dict[str, Any] = {"email": from_email}
        if params.get("from_name"):
            from_obj["name"] = params["from_name"]

        body: dict[str, Any] = {
            "personalizations": [personalization],
            "from": from_obj,
            "tracking_settings": {
                "open_tracking": {"enable": True},
                "click_tracking": {"enable": True, "enable_text": True},
            },
        }

        if params.get("template_id"):
            body["template_id"] = params["template_id"]
        else:
            if not subject:
                return {"error": "subject is required when not using a template"}
            body["subject"] = subject
            html = params.get("html_content", params.get("html", ""))
            text = params.get("text", "")
            if html:
                body["content"] = [{"type": "text/html", "value": html}]
            elif text:
                body["content"] = [{"type": "text/plain", "value": text}]
            else:
                return {"error": "html_content or text is required when not using a template"}

        if params.get("reply_to"):
            body["reply_to"] = {"email": params["reply_to"]}

        # Add categories for analytics grouping
        categories = params.get("categories", [])
        if categories:
            if isinstance(categories, str):
                categories = [categories]
            body["categories"] = categories

        # SendGrid returns 202 Accepted with empty body for mail/send
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.post("/mail/send", json=body)
        resp.raise_for_status()

        return {
            "status": "accepted",
            "status_code": resp.status_code,
            "message_id": resp.headers.get("X-Message-Id", ""),
            "tracking": {"open_tracking": True, "click_tracking": True},
        }

    # ── Get Email Activity ────────────────────────────────────────────

    async def get_email_activity(self, **params) -> dict[str, Any]:
        """Retrieve email activity / message history for a recipient.

        Required params:
            email: Recipient email to look up
        Optional params:
            limit: Max results (default 10, max 1000)
        """
        email = params.get("email", "")
        if not email:
            return {"error": "email is required"}

        limit = params.get("limit", 10)

        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.get(
            "/messages",
            params={"query": f"to_email={email}", "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()

        messages = data.get("messages", [])
        return {
            "email": email,
            "messages": [
                {
                    "msg_id": m.get("msg_id", ""),
                    "from_email": m.get("from_email", ""),
                    "subject": m.get("subject", ""),
                    "status": m.get("status", ""),
                    "opens_count": m.get("opens_count", 0),
                    "clicks_count": m.get("clicks_count", 0),
                    "last_event_time": m.get("last_event_time", ""),
                }
                for m in messages
            ],
            "count": len(messages),
        }
