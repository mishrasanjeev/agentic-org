"""Object Storage connector — GCS-native with S3-compatible fallback."""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class ObjectStorageConnector(BaseConnector):
    name = "object_storage"
    category = "comms"
    auth_type = "service_account"
    base_url = "https://storage.googleapis.com"
    rate_limit_rpm = 1000

    def _register_tools(self):
        self._tool_registry["upload_document"] = self.upload_document
        self._tool_registry["download_document"] = self.download_document
        self._tool_registry["list_bucket_objects"] = self.list_bucket_objects
        self._tool_registry["generate_presigned_download_url"] = (
            self.generate_presigned_download_url
        )
        self._tool_registry["delete_object"] = self.delete_object
        self._tool_registry["copy_object"] = self.copy_object

    async def _authenticate(self):
        # For GCS: use service account JSON key or workload identity (GKE).
        # For S3-compatible (MinIO): fall back to access-key/secret-key auth.
        endpoint = self.config.get("storage_endpoint")
        if endpoint:
            # S3-compatible mode (e.g., MinIO in local dev)
            access_key = self._get_secret("access_key")
            secret_key = self._get_secret("secret_key")
            self._auth_headers = {"X-Access-Key": access_key, "X-Secret-Key": secret_key}
            self.base_url = endpoint
        else:
            # GCS mode — use google-cloud-storage client credentials.
            # In production the service account key is injected via
            # GOOGLE_APPLICATION_CREDENTIALS or GKE workload identity.
            sa_key_path = self._get_secret("gcs_service_account_key")
            self._auth_headers = {"X-GCS-SA-Key-Path": sa_key_path}

    async def upload_document(self, **params: Any) -> dict[str, Any]:
        """Upload a document to object storage."""
        return await self._post("/upload/document", params)

    async def download_document(self, **params: Any) -> dict[str, Any]:
        """Download a document from object storage."""
        return await self._post("/download/document", params)

    async def list_bucket_objects(self, **params: Any) -> dict[str, Any]:
        """List objects in a storage bucket."""
        return await self._post("/list/bucket/objects", params)

    async def generate_presigned_download_url(self, **params: Any) -> dict[str, Any]:
        """Generate a signed download URL."""
        return await self._post("/generate/presigned/download/url", params)

    async def delete_object(self, **params: Any) -> dict[str, Any]:
        """Delete an object from storage."""
        return await self._post("/delete/object", params)

    async def copy_object(self, **params: Any) -> dict[str, Any]:
        """Copy an object within storage."""
        return await self._post("/copy/object", params)
