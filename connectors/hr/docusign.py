"""Docusign connector — hr."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class DocusignConnector(BaseConnector):
    name = "docusign"
    category = "hr"
    auth_type = "jwt"
    base_url = "https://na4.docusign.net/restapi/v2.1"
    rate_limit_rpm = 100

    def _register_tools(self):
    self._tool_registry["send_envelope"] = self.send_envelope
    self._tool_registry["void_envelope"] = self.void_envelope
    self._tool_registry["get_status"] = self.get_status
    self._tool_registry["extract_completed_fields"] = self.extract_completed_fields
    self._tool_registry["download_signed_doc"] = self.download_signed_doc
    self._tool_registry["create_template"] = self.create_template

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"Authorization": f"Bearer {api_key}"}

async def send_envelope(self, **params):
    """Execute send_envelope on docusign."""
    return await self._post("/send/envelope", params)


async def void_envelope(self, **params):
    """Execute void_envelope on docusign."""
    return await self._post("/void/envelope", params)


async def get_status(self, **params):
    """Execute get_status on docusign."""
    return await self._post("/get/status", params)


async def extract_completed_fields(self, **params):
    """Execute extract_completed_fields on docusign."""
    return await self._post("/extract/completed/fields", params)


async def download_signed_doc(self, **params):
    """Execute download_signed_doc on docusign."""
    return await self._post("/download/signed/doc", params)


async def create_template(self, **params):
    """Execute create_template on docusign."""
    return await self._post("/create/template", params)

