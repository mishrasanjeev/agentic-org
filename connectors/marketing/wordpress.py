"""WordPress connector — real WordPress REST API v2 integration."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class WordpressConnector(BaseConnector):
    """WordPress connector using the WP REST API v2.

    Requires config keys:
        site         — WordPress site domain (e.g. "example.com" or full URL)
        username     — WordPress admin username
        app_password — Application Password (WordPress 5.6+)

    Optional config overrides:
        base_url — Full base URL if the default template is not suitable
    """

    name = "wordpress"
    category = "marketing"
    auth_type = "basic"
    base_url = ""  # built dynamically from site config
    rate_limit_rpm = 100

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        site = self.config.get("site", "")
        if not self.base_url and site:
            # Strip protocol if present, then build canonical URL
            clean_site = site.rstrip("/")
            if not clean_site.startswith("http"):
                clean_site = f"https://{clean_site}"
            self.base_url = f"{clean_site}/wp-json/wp/v2"

    # ── Tool registration ──────────────────────────────────────────────

    def _register_tools(self):
        self._tool_registry["create_post"] = self.create_post
        self._tool_registry["update_post"] = self.update_post
        self._tool_registry["list_posts"] = self.list_posts
        self._tool_registry["get_post"] = self.get_post
        self._tool_registry["upload_media"] = self.upload_media
        self._tool_registry["list_categories"] = self.list_categories
        self._tool_registry["create_page"] = self.create_page

    # ── Authentication ─────────────────────────────────────────────────

    async def _authenticate(self):
        """Authenticate using WordPress Application Passwords (Basic Auth).

        WordPress 5.6+ supports Application Passwords natively.
        The credentials are base64-encoded as ``username:app_password``.
        """
        username = self._get_secret("username")
        app_password = self._get_secret("app_password")

        if username and app_password:
            credentials = base64.b64encode(
                f"{username}:{app_password}".encode()
            ).decode()
            self._auth_headers = {
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            return

        # Fallback: pre-encoded token or JWT
        access_token = self._get_secret("access_token")
        if access_token:
            self._auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

    # ── Execute with 401 retry ─────────────────────────────────────────

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute tool with automatic 401 retry (re-authenticates and retries once)."""
        try:
            return await super().execute_tool(tool_name, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            logger.info("wordpress_401_retry", tool=tool_name)
            await self._authenticate()
            if self._client:
                await self._client.aclose()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_ms / 1000,
                headers=self._auth_headers,
            )
            return await super().execute_tool(tool_name, params)

    # ── Health check ───────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity by fetching a single post."""
        try:
            data = await self._get("/posts", params={"per_page": 1})
            total = len(data) if isinstance(data, list) else 0
            return {"status": "healthy", "posts_returned": total}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Posts ──────────────────────────────────────────────────────────

    async def create_post(self, **params) -> dict[str, Any]:
        """Create a new WordPress post.

        Required: title, content.
        Optional: status (draft/publish/pending/private, default "draft"),
                  categories (list of category IDs), tags (list of tag IDs),
                  slug, excerpt, featured_media, format.
        """
        title = params.get("title")
        if not title:
            return {"error": "title is required"}
        content = params.get("content")
        if not content:
            return {"error": "content is required"}

        body: dict[str, Any] = {
            "title": title,
            "content": content,
            "status": params.get("status", "draft"),
        }
        for field in ("categories", "tags", "slug", "excerpt", "featured_media", "format"):
            if params.get(field) is not None:
                body[field] = params[field]

        return await self._post("/posts", body)

    async def update_post(self, **params) -> dict[str, Any]:
        """Update an existing WordPress post.

        Required: id (post ID).
        Optional: title, content, status, categories, tags, slug, excerpt.
        """
        post_id = params.get("id") or params.get("post_id")
        if not post_id:
            return {"error": "id is required"}

        body: dict[str, Any] = {}
        for field in ("title", "content", "status", "categories", "tags",
                       "slug", "excerpt", "featured_media", "format"):
            if params.get(field) is not None:
                body[field] = params[field]

        if not body:
            return {"error": "at least one field to update is required"}

        return await self._put(f"/posts/{post_id}", body)

    async def list_posts(self, **params) -> dict[str, Any]:
        """List WordPress posts with filtering and pagination.

        Optional: per_page (default 10, max 100), page (default 1),
                  status (publish/draft/pending/private/any),
                  categories (comma-separated IDs), search (keyword),
                  orderby (date/title/modified), order (asc/desc).
        """
        qp: dict[str, Any] = {
            "per_page": params.get("per_page", 10),
            "page": params.get("page", 1),
        }
        for field in ("status", "categories", "search", "orderby", "order",
                       "tags", "author", "before", "after"):
            if params.get(field) is not None:
                qp[field] = params[field]

        data = await self._get("/posts", params=qp)
        posts = data if isinstance(data, list) else []
        return {
            "posts": [
                {
                    "id": p.get("id"),
                    "title": p.get("title", {}).get("rendered", ""),
                    "slug": p.get("slug"),
                    "status": p.get("status"),
                    "date": p.get("date"),
                    "link": p.get("link"),
                    "excerpt": p.get("excerpt", {}).get("rendered", ""),
                }
                for p in posts
            ],
            "count": len(posts),
        }

    async def get_post(self, **params) -> dict[str, Any]:
        """Get a single WordPress post by ID.

        Required: id (post ID).
        """
        post_id = params.get("id") or params.get("post_id")
        if not post_id:
            return {"error": "id is required"}
        data = await self._get(f"/posts/{post_id}")
        return {
            "id": data.get("id"),
            "title": data.get("title", {}).get("rendered", ""),
            "content": data.get("content", {}).get("rendered", ""),
            "slug": data.get("slug"),
            "status": data.get("status"),
            "date": data.get("date"),
            "modified": data.get("modified"),
            "link": data.get("link"),
            "categories": data.get("categories", []),
            "tags": data.get("tags", []),
            "excerpt": data.get("excerpt", {}).get("rendered", ""),
        }

    # ── Media ──────────────────────────────────────────────────────────

    async def upload_media(self, **params) -> dict[str, Any]:
        """Upload a media file to WordPress.

        Required: filename (name for the upload), content_base64 (base64-encoded file data).
        Optional: alt_text, caption, description.

        The file is sent as binary with Content-Disposition header.
        """
        filename = params.get("filename")
        if not filename:
            return {"error": "filename is required"}
        content_b64 = params.get("content_base64")
        if not content_b64:
            return {"error": "content_base64 is required"}

        import base64 as b64mod

        file_bytes = b64mod.b64decode(content_b64)

        # Determine content type from extension
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        content_types = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
            "pdf": "application/pdf", "mp4": "video/mp4",
        }
        content_type = content_types.get(ext, "application/octet-stream")

        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.post(
            "/media",
            content=file_bytes,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": content_type,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        # Set alt text / caption if provided
        media_id = data.get("id")
        update_body: dict[str, Any] = {}
        if params.get("alt_text"):
            update_body["alt_text"] = params["alt_text"]
        if params.get("caption"):
            update_body["caption"] = params["caption"]
        if params.get("description"):
            update_body["description"] = params["description"]
        if update_body and media_id:
            await self._post(f"/media/{media_id}", update_body)

        return {
            "id": media_id,
            "url": data.get("source_url", ""),
            "title": data.get("title", {}).get("rendered", ""),
            "media_type": data.get("media_type", ""),
        }

    # ── Categories ─────────────────────────────────────────────────────

    async def list_categories(self, **params) -> dict[str, Any]:
        """List WordPress categories.

        Optional: per_page (default 20), search (keyword).
        """
        qp: dict[str, Any] = {
            "per_page": params.get("per_page", 20),
        }
        if params.get("search"):
            qp["search"] = params["search"]

        data = await self._get("/categories", params=qp)
        categories = data if isinstance(data, list) else []
        return {
            "categories": [
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "slug": c.get("slug"),
                    "count": c.get("count", 0),
                    "parent": c.get("parent", 0),
                }
                for c in categories
            ],
            "count": len(categories),
        }

    # ── Pages ──────────────────────────────────────────────────────────

    async def create_page(self, **params) -> dict[str, Any]:
        """Create a new WordPress page.

        Required: title, content.
        Optional: status (draft/publish, default "draft"), parent (page ID),
                  slug, template, menu_order.
        """
        title = params.get("title")
        if not title:
            return {"error": "title is required"}
        content = params.get("content")
        if not content:
            return {"error": "content is required"}

        body: dict[str, Any] = {
            "title": title,
            "content": content,
            "status": params.get("status", "draft"),
        }
        for field in ("parent", "slug", "template", "menu_order"):
            if params.get(field) is not None:
                body[field] = params[field]

        return await self._post("/pages", body)
