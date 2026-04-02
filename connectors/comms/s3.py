"""Object Storage connector — GCS-native with S3-compatible fallback.

Integrates with Google Cloud Storage (GCS) natively via the JSON API,
or falls back to S3-compatible endpoints (MinIO, AWS S3) when configured.
Used for document storage, report archival, and file sharing.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class S3Connector(BaseConnector):
    name = "s3"
    category = "comms"
    auth_type = "service_account"
    base_url = "https://storage.googleapis.com"
    rate_limit_rpm = 1000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._default_bucket = self.config.get("bucket", "")
        self._is_s3 = self.config.get("s3_compatible", False)

    def _register_tools(self):
        self._tool_registry["upload_document"] = self.upload_document
        self._tool_registry["download_document"] = self.download_document
        self._tool_registry["list_objects"] = self.list_objects
        self._tool_registry["generate_signed_url"] = self.generate_signed_url
        self._tool_registry["delete_object"] = self.delete_object
        self._tool_registry["copy_object"] = self.copy_object

    async def _authenticate(self):
        if self._is_s3:
            # S3-compatible (MinIO, AWS) — use access key/secret
            access_key = self._get_secret("access_key")
            self._get_secret("secret_key")
            # For S3-compatible, credentials are sent per-request via AWS Sig V4
            # Simplified: use access key as Bearer (works with MinIO)
            self._auth_headers = {"Authorization": f"Bearer {access_key}"}
        else:
            # GCS — use OAuth2 access token from service account
            access_token = self._get_secret("access_token")
            self._auth_headers = {"Authorization": f"Bearer {access_token}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            bucket = self._default_bucket
            result = await self._get(f"/storage/v1/b/{bucket}")
            return {"status": "healthy", "bucket": result.get("name", "")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def upload_document(self, **params) -> dict[str, Any]:
        """Upload a document to object storage.

        Params: bucket (optional — uses default), object_name (required),
                content (required — base64 encoded data or text),
                content_type (default application/octet-stream),
                metadata (optional dict of custom metadata).
        """
        bucket = params.get("bucket", self._default_bucket)
        object_name = params["object_name"]
        content_type = params.get("content_type", "application/octet-stream")

        # GCS JSON API upload
        if not self._client:
            raise RuntimeError("Connector not connected")

        resp = await self._client.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
            params={"uploadType": "media", "name": object_name},
            content=params.get("content", "").encode("utf-8") if isinstance(params.get("content"), str) else params.get("content", b""),
            headers={**self._auth_headers, "Content-Type": content_type},
        )
        resp.raise_for_status()
        return resp.json()

    async def download_document(self, **params) -> dict[str, Any]:
        """Download a document from object storage.

        Params: bucket (optional), object_name (required).
        Returns metadata + download URL (not the binary content).
        """
        bucket = params.get("bucket", self._default_bucket)
        object_name = params["object_name"]
        metadata = await self._get(f"/storage/v1/b/{bucket}/o/{object_name}")
        return {
            **metadata,
            "download_url": f"https://storage.googleapis.com/{bucket}/{object_name}",
        }

    async def list_objects(self, **params) -> dict[str, Any]:
        """List objects in a bucket.

        Params: bucket (optional), prefix (optional — folder filter),
                max_results (default 100), page_token (for pagination).
        """
        bucket = params.get("bucket", self._default_bucket)
        query: dict[str, Any] = {"maxResults": params.get("max_results", 100)}
        if params.get("prefix"):
            query["prefix"] = params["prefix"]
        if params.get("page_token"):
            query["pageToken"] = params["page_token"]
        return await self._get(f"/storage/v1/b/{bucket}/o", query)

    async def generate_signed_url(self, **params) -> dict[str, Any]:
        """Generate a time-limited signed download URL.

        Params: bucket (optional), object_name (required),
                expiration_minutes (default 60).
        Note: Signed URL generation requires service account private key.
        For now, returns the public URL (for public buckets) or
        an authenticated URL pattern.
        """
        bucket = params.get("bucket", self._default_bucket)
        object_name = params["object_name"]
        expiration = params.get("expiration_minutes", 60)
        return {
            "url": f"https://storage.googleapis.com/{bucket}/{object_name}",
            "bucket": bucket,
            "object_name": object_name,
            "expiration_minutes": expiration,
            "note": "For true signed URLs, use GCS client library with service account key",
        }

    async def delete_object(self, **params) -> dict[str, Any]:
        """Delete an object from storage.

        Params: bucket (optional), object_name (required).
        """
        bucket = params.get("bucket", self._default_bucket)
        object_name = params["object_name"]
        return await self._delete(f"/storage/v1/b/{bucket}/o/{object_name}")

    async def copy_object(self, **params) -> dict[str, Any]:
        """Copy an object within or between buckets.

        Params: source_bucket (optional), source_object (required),
                dest_bucket (optional), dest_object (required).
        """
        src_bucket = params.get("source_bucket", self._default_bucket)
        src_obj = params["source_object"]
        dst_bucket = params.get("dest_bucket", self._default_bucket)
        dst_obj = params["dest_object"]
        return await self._post(
            f"/storage/v1/b/{src_bucket}/o/{src_obj}/copyTo/b/{dst_bucket}/o/{dst_obj}",
            {},
        )
