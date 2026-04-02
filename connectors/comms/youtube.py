"""YouTube connector — real YouTube Data API v3 integration."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class YouTubeConnector(BaseConnector):
    name = "youtube"
    category = "comms"
    auth_type = "oauth2"
    base_url = "https://www.googleapis.com/youtube/v3"
    rate_limit_rpm = 200

    def _register_tools(self):
        self._tool_registry["list_videos"] = self.list_videos
        self._tool_registry["get_video_stats"] = self.get_video_stats
        self._tool_registry["list_channel_videos"] = self.list_channel_videos
        self._tool_registry["get_channel_stats"] = self.get_channel_stats
        self._tool_registry["list_playlists"] = self.list_playlists
        self._tool_registry["get_video_analytics"] = self.get_video_analytics

    async def _authenticate(self):
        """Authenticate using OAuth2 via Google refresh token exchange.

        Uses the same pattern as Gmail / Google Calendar connectors:
        exchange a refresh token for an access token via the Google OAuth2
        token endpoint.  Falls back to a pre-configured access_token or
        API key if refresh credentials are not available.
        """
        refresh_token = self._get_secret("refresh_token")
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        token_url = self.config.get(
            "token_url", "https://oauth2.googleapis.com/token"
        )

        if refresh_token and client_id and client_secret:
            # Exchange refresh token for a fresh access token
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
            self._auth_headers = {"Authorization": f"Bearer {token}"}
        else:
            # Fall back to pre-configured access token
            access_token = self._get_secret("access_token")
            if access_token:
                self._auth_headers = {"Authorization": f"Bearer {access_token}"}
            else:
                # API-key-only auth (read-only public data)
                api_key = self._get_secret("api_key")
                if api_key:
                    self._api_key = api_key
                    self._auth_headers = {}
                else:
                    self._auth_headers = {}

    def _inject_api_key(self, params: dict[str, Any]) -> dict[str, Any]:
        """Inject the API key into query params if using key-based auth."""
        api_key = getattr(self, "_api_key", None)
        if api_key and not self._auth_headers:
            params["key"] = api_key
        return params

    async def health_check(self) -> dict[str, Any]:
        """Check API connectivity by fetching the authenticated channel.

        Uses GET /channels?part=id&mine=true for OAuth2 auth, or falls back
        to a simple search query for API-key auth.
        """
        try:
            if self._auth_headers:
                data = await self._get(
                    "/channels", params={"part": "id", "mine": "true"}
                )
                items = data.get("items", [])
                return {
                    "status": "healthy",
                    "auth_mode": "oauth2",
                    "channel_id": items[0].get("id") if items else None,
                }
            else:
                # API-key fallback — search for a public video
                params = self._inject_api_key(
                    {"part": "snippet", "q": "test", "type": "video", "maxResults": 1}
                )
                data = await self._get("/search", params=params)
                return {
                    "status": "healthy",
                    "auth_mode": "api_key",
                    "result_count": data.get("pageInfo", {}).get("totalResults", 0),
                }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # -- list_videos --------------------------------------------------------

    async def list_videos(self, **params) -> dict[str, Any]:
        """Search for videos on YouTube.

        Required params:
            q: Search query string.
        Optional params:
            max_results: Maximum number of results (1-50, default 10).
            channel_id: Restrict results to a specific channel.
            order: Sort order — "date", "viewCount", "rating",
                   "relevance" (default "relevance").
            page_token: Pagination token from a previous response.
        """
        query = params.get("q", "")
        if not query:
            return {"error": "q is required"}

        query_params: dict[str, Any] = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": params.get("max_results", 10),
        }
        if params.get("channel_id"):
            query_params["channelId"] = params["channel_id"]
        if params.get("order"):
            query_params["order"] = params["order"]
        if params.get("page_token"):
            query_params["pageToken"] = params["page_token"]

        query_params = self._inject_api_key(query_params)
        data = await self._get("/search", params=query_params)
        return {
            "videos": [
                {
                    "video_id": item.get("id", {}).get("videoId"),
                    "title": item.get("snippet", {}).get("title"),
                    "description": item.get("snippet", {}).get("description"),
                    "channel_title": item.get("snippet", {}).get("channelTitle"),
                    "published_at": item.get("snippet", {}).get("publishedAt"),
                    "thumbnail": item.get("snippet", {})
                    .get("thumbnails", {})
                    .get("high", {})
                    .get("url"),
                }
                for item in data.get("items", [])
            ],
            "total_results": data.get("pageInfo", {}).get("totalResults", 0),
            "next_page_token": data.get("nextPageToken"),
        }

    # -- get_video_stats ----------------------------------------------------

    async def get_video_stats(self, **params) -> dict[str, Any]:
        """Get statistics and snippet data for a single video.

        Returns viewCount, likeCount, commentCount along with the video
        title and description.

        Required params:
            video_id: The YouTube video ID.
        """
        video_id = params.get("video_id", "")
        if not video_id:
            return {"error": "video_id is required"}

        query_params: dict[str, Any] = {
            "part": "statistics,snippet",
            "id": video_id,
        }
        query_params = self._inject_api_key(query_params)
        data = await self._get("/videos", params=query_params)

        items = data.get("items", [])
        if not items:
            return {"error": "video not found", "video_id": video_id}

        item = items[0]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        return {
            "video_id": video_id,
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "channel_title": snippet.get("channelTitle"),
            "published_at": snippet.get("publishedAt"),
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
        }

    # -- list_channel_videos ------------------------------------------------

    async def list_channel_videos(self, **params) -> dict[str, Any]:
        """List recent videos from a specific YouTube channel.

        Required params:
            channel_id: The YouTube channel ID.
        Optional params:
            max_results: Maximum number of results (1-50, default 10).
            order: Sort order — "date" (default), "viewCount", "rating".
            page_token: Pagination token from a previous response.
        """
        channel_id = params.get("channel_id", "")
        if not channel_id:
            return {"error": "channel_id is required"}

        query_params: dict[str, Any] = {
            "part": "snippet",
            "channelId": channel_id,
            "type": "video",
            "order": params.get("order", "date"),
            "maxResults": params.get("max_results", 10),
        }
        if params.get("page_token"):
            query_params["pageToken"] = params["page_token"]

        query_params = self._inject_api_key(query_params)
        data = await self._get("/search", params=query_params)
        return {
            "videos": [
                {
                    "video_id": item.get("id", {}).get("videoId"),
                    "title": item.get("snippet", {}).get("title"),
                    "description": item.get("snippet", {}).get("description"),
                    "published_at": item.get("snippet", {}).get("publishedAt"),
                    "thumbnail": item.get("snippet", {})
                    .get("thumbnails", {})
                    .get("high", {})
                    .get("url"),
                }
                for item in data.get("items", [])
            ],
            "total_results": data.get("pageInfo", {}).get("totalResults", 0),
            "next_page_token": data.get("nextPageToken"),
        }

    # -- get_channel_stats --------------------------------------------------

    async def get_channel_stats(self, **params) -> dict[str, Any]:
        """Get statistics and snippet data for a YouTube channel.

        Returns subscriberCount, videoCount, viewCount along with
        the channel title and description.

        Required params:
            channel_id: The YouTube channel ID.
        """
        channel_id = params.get("channel_id", "")
        if not channel_id:
            return {"error": "channel_id is required"}

        query_params: dict[str, Any] = {
            "part": "statistics,snippet",
            "id": channel_id,
        }
        query_params = self._inject_api_key(query_params)
        data = await self._get("/channels", params=query_params)

        items = data.get("items", [])
        if not items:
            return {"error": "channel not found", "channel_id": channel_id}

        item = items[0]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        return {
            "channel_id": channel_id,
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "published_at": snippet.get("publishedAt"),
            "subscriber_count": int(stats.get("subscriberCount", 0)),
            "video_count": int(stats.get("videoCount", 0)),
            "view_count": int(stats.get("viewCount", 0)),
            "hidden_subscriber_count": stats.get("hiddenSubscriberCount", False),
        }

    # -- list_playlists -----------------------------------------------------

    async def list_playlists(self, **params) -> dict[str, Any]:
        """List playlists for a YouTube channel.

        Required params:
            channel_id: The YouTube channel ID.
        Optional params:
            max_results: Maximum number of results (1-50, default 10).
            page_token: Pagination token from a previous response.
        """
        channel_id = params.get("channel_id", "")
        if not channel_id:
            return {"error": "channel_id is required"}

        query_params: dict[str, Any] = {
            "part": "snippet,contentDetails",
            "channelId": channel_id,
            "maxResults": params.get("max_results", 10),
        }
        if params.get("page_token"):
            query_params["pageToken"] = params["page_token"]

        query_params = self._inject_api_key(query_params)
        data = await self._get("/playlists", params=query_params)
        return {
            "playlists": [
                {
                    "playlist_id": item.get("id"),
                    "title": item.get("snippet", {}).get("title"),
                    "description": item.get("snippet", {}).get("description"),
                    "published_at": item.get("snippet", {}).get("publishedAt"),
                    "item_count": item.get("contentDetails", {}).get("itemCount", 0),
                    "thumbnail": item.get("snippet", {})
                    .get("thumbnails", {})
                    .get("high", {})
                    .get("url"),
                }
                for item in data.get("items", [])
            ],
            "total_results": data.get("pageInfo", {}).get("totalResults", 0),
            "next_page_token": data.get("nextPageToken"),
        }

    # -- get_video_analytics ------------------------------------------------

    async def get_video_analytics(self, **params) -> dict[str, Any]:
        """Batch-fetch statistics for multiple videos at once.

        Accepts a comma-separated list of video IDs and returns statistics
        for each.  Useful for dashboards and comparative analytics.

        Required params:
            video_ids: Comma-separated YouTube video IDs (max 50).
        """
        video_ids = params.get("video_ids", "")
        if not video_ids:
            return {"error": "video_ids is required"}

        query_params: dict[str, Any] = {
            "part": "statistics",
            "id": video_ids,
        }
        query_params = self._inject_api_key(query_params)
        data = await self._get("/videos", params=query_params)

        return {
            "videos": [
                {
                    "video_id": item.get("id"),
                    "view_count": int(
                        item.get("statistics", {}).get("viewCount", 0)
                    ),
                    "like_count": int(
                        item.get("statistics", {}).get("likeCount", 0)
                    ),
                    "comment_count": int(
                        item.get("statistics", {}).get("commentCount", 0)
                    ),
                    "favorite_count": int(
                        item.get("statistics", {}).get("favoriteCount", 0)
                    ),
                }
                for item in data.get("items", [])
            ],
            "total_results": len(data.get("items", [])),
        }
