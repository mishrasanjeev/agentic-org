"""Twitter/X connector — real X API v2 integration."""

from __future__ import annotations

from typing import Any

import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class TwitterConnector(BaseConnector):
    name = "twitter"
    category = "comms"
    auth_type = "oauth2"
    base_url = "https://api.x.com/2"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_tweet"] = self.create_tweet
        self._tool_registry["get_tweet"] = self.get_tweet
        self._tool_registry["search_recent"] = self.search_recent
        self._tool_registry["get_user_tweets"] = self.get_user_tweets
        self._tool_registry["get_user_by_username"] = self.get_user_by_username
        self._tool_registry["get_tweet_metrics"] = self.get_tweet_metrics

    async def _authenticate(self):
        """Authenticate using OAuth 2.0.

        Supports two auth modes:
        - Bearer Token (app-only): used for read operations like search and
          fetching tweets/users. Configured via config["bearer_token"].
        - User Access Token (OAuth 2.0 with PKCE): used for write operations
          like creating tweets. Configured via config["access_token"].

        The default client is created with the Bearer Token. Write operations
        override the Authorization header with the user access token when
        available.
        """
        bearer_token = self._get_secret("bearer_token")
        if not bearer_token:
            # Fall back to access_token if no bearer_token is configured
            bearer_token = self._get_secret("access_token")

        self._auth_headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }

    def _get_user_auth_headers(self) -> dict[str, str]:
        """Return Authorization headers using the user access token.

        Used for write operations (create_tweet) that require OAuth 2.0
        User Context rather than app-only Bearer Token auth.
        """
        access_token = self._get_secret("access_token")
        if access_token:
            return {"Authorization": f"Bearer {access_token}"}
        # Fall back to default auth headers if no separate user token
        return {}

    async def _user_post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        """POST with user access token auth for write operations.

        Falls back to the default client auth if no user access token is
        configured separately from the bearer token.
        """
        if not self._client:
            raise RuntimeError("Connector not connected")
        headers = self._get_user_auth_headers()
        resp = await self._client.post(path, json=data, headers=headers or None)
        resp.raise_for_status()
        return resp.json()

    async def health_check(self) -> dict[str, Any]:
        """Check API connectivity.

        Tries GET /users/me with user access token first; falls back to a
        minimal recent-search query with Bearer Token.
        """
        try:
            # Try user-context endpoint first
            access_token = self._get_secret("access_token")
            if access_token and self._client:
                resp = await self._client.get(
                    "/users/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    return {
                        "status": "healthy",
                        "auth_mode": "user_context",
                        "username": data.get("username"),
                        "user_id": data.get("id"),
                    }

            # Fall back to app-only search
            data = await self._get(
                "/tweets/search/recent",
                params={"query": "test", "max_results": 10},
            )
            return {
                "status": "healthy",
                "auth_mode": "bearer_token",
                "result_count": data.get("meta", {}).get("result_count", 0),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # -- create_tweet -------------------------------------------------------

    async def create_tweet(self, **params) -> dict[str, Any]:
        """Create a new tweet on X (formerly Twitter).

        Requires OAuth 2.0 User Context (access_token in config).

        Required params:
            text: The text content of the tweet (max 280 characters).
        Optional params:
            reply_to: Tweet ID to reply to (sets in_reply_to_tweet_id).
            quote_tweet_id: Tweet ID to quote-retweet.
            poll_options: List of poll option strings (2-4 options).
            poll_duration_minutes: Duration of the poll in minutes (5-10080).
        """
        text = params.get("text", "")
        if not text:
            return {"error": "text is required"}

        body: dict[str, Any] = {"text": text}

        if params.get("reply_to"):
            body["reply"] = {"in_reply_to_tweet_id": params["reply_to"]}

        if params.get("quote_tweet_id"):
            body["quote_tweet_id"] = params["quote_tweet_id"]

        if params.get("poll_options"):
            body["poll"] = {
                "options": params["poll_options"],
                "duration_minutes": params.get("poll_duration_minutes", 1440),
            }

        data = await self._user_post("/tweets", body)
        tweet_data = data.get("data", {})
        return {
            "tweet_id": tweet_data.get("id"),
            "text": tweet_data.get("text"),
        }

    # -- get_tweet ----------------------------------------------------------

    async def get_tweet(self, **params) -> dict[str, Any]:
        """Retrieve a single tweet by ID with public metrics.

        Required params:
            id: The tweet ID to retrieve.
        Optional params:
            tweet_fields: Comma-separated tweet fields
                (default: "public_metrics,created_at,author_id").
            expansions: Comma-separated expansions (e.g. "author_id").
        """
        tweet_id = params.get("id", "")
        if not tweet_id:
            return {"error": "id is required"}

        query_params: dict[str, Any] = {
            "tweet.fields": params.get(
                "tweet_fields", "public_metrics,created_at,author_id"
            ),
        }
        if params.get("expansions"):
            query_params["expansions"] = params["expansions"]

        data = await self._get(f"/tweets/{tweet_id}", params=query_params)
        return data.get("data", {})

    # -- search_recent ------------------------------------------------------

    async def search_recent(self, **params) -> dict[str, Any]:
        """Search recent tweets (last 7 days) using X API v2.

        Required params:
            query: Search query string (supports X search operators, max 512 chars).
        Optional params:
            max_results: Number of results per page (10-100, default 10).
            tweet_fields: Comma-separated tweet fields
                (default: "public_metrics,created_at,author_id").
            start_time: ISO 8601 start time (e.g. "2026-04-01T00:00:00Z").
            end_time: ISO 8601 end time.
            next_token: Pagination token from a previous response.
        """
        query = params.get("query", "")
        if not query:
            return {"error": "query is required"}

        query_params: dict[str, Any] = {
            "query": query,
            "max_results": params.get("max_results", 10),
            "tweet.fields": params.get(
                "tweet_fields", "public_metrics,created_at,author_id"
            ),
        }
        if params.get("start_time"):
            query_params["start_time"] = params["start_time"]
        if params.get("end_time"):
            query_params["end_time"] = params["end_time"]
        if params.get("next_token"):
            query_params["next_token"] = params["next_token"]

        data = await self._get("/tweets/search/recent", params=query_params)
        meta = data.get("meta", {})
        return {
            "tweets": data.get("data", []),
            "result_count": meta.get("result_count", 0),
            "next_token": meta.get("next_token"),
        }

    # -- get_user_tweets ----------------------------------------------------

    async def get_user_tweets(self, **params) -> dict[str, Any]:
        """Retrieve tweets authored by a specific user.

        Required params:
            id: The user ID whose tweets to retrieve.
        Optional params:
            max_results: Number of results per page (5-100, default 10).
            tweet_fields: Comma-separated tweet fields
                (default: "public_metrics,created_at").
            pagination_token: Pagination token from a previous response.
        """
        user_id = params.get("id", "")
        if not user_id:
            return {"error": "id is required"}

        query_params: dict[str, Any] = {
            "max_results": params.get("max_results", 10),
            "tweet.fields": params.get(
                "tweet_fields", "public_metrics,created_at"
            ),
        }
        if params.get("pagination_token"):
            query_params["pagination_token"] = params["pagination_token"]

        data = await self._get(f"/users/{user_id}/tweets", params=query_params)
        meta = data.get("meta", {})
        return {
            "tweets": data.get("data", []),
            "result_count": meta.get("result_count", 0),
            "next_token": meta.get("next_token"),
        }

    # -- get_user_by_username -----------------------------------------------

    async def get_user_by_username(self, **params) -> dict[str, Any]:
        """Look up a user by their X username.

        Required params:
            username: The X username (without @ prefix).
        Optional params:
            user_fields: Comma-separated user fields
                (default: "public_metrics,description").
        """
        username = params.get("username", "")
        if not username:
            return {"error": "username is required"}

        # Strip leading @ if provided
        username = username.lstrip("@")

        query_params: dict[str, Any] = {
            "user.fields": params.get(
                "user_fields", "public_metrics,description"
            ),
        }

        data = await self._get(
            f"/users/by/username/{username}", params=query_params
        )
        return data.get("data", {})

    # -- get_tweet_metrics --------------------------------------------------

    async def get_tweet_metrics(self, **params) -> dict[str, Any]:
        """Retrieve public engagement metrics for a tweet.

        Returns retweet_count, reply_count, like_count, quote_count, and
        impression_count (if available).

        Required params:
            id: The tweet ID to get metrics for.
        """
        tweet_id = params.get("id", "")
        if not tweet_id:
            return {"error": "id is required"}

        data = await self._get(
            f"/tweets/{tweet_id}",
            params={"tweet.fields": "public_metrics"},
        )
        tweet_data = data.get("data", {})
        metrics = tweet_data.get("public_metrics", {})
        return {
            "tweet_id": tweet_data.get("id"),
            "retweet_count": metrics.get("retweet_count", 0),
            "reply_count": metrics.get("reply_count", 0),
            "like_count": metrics.get("like_count", 0),
            "quote_count": metrics.get("quote_count", 0),
            "impression_count": metrics.get("impression_count", 0),
        }
