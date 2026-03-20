"""Epfo connector — hr."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class EpfoConnector(BaseConnector):
    name = "epfo"
    category = "hr"
    auth_type = "dsc"
    base_url = "https://unifiedportal-emp.epfindia.gov.in/api"
    rate_limit_rpm = 10

    def _register_tools(self):
    self._tool_registry["file_ecr"] = self.file_ecr
    self._tool_registry["get_uan"] = self.get_uan
    self._tool_registry["check_claim_status"] = self.check_claim_status
    self._tool_registry["download_passbook"] = self.download_passbook
    self._tool_registry["generate_trrn"] = self.generate_trrn
    self._tool_registry["verify_member"] = self.verify_member

    async def _authenticate(self):
        dsc_path = self._get_secret("dsc_path")
        api_key = self._get_secret("api_key")
        self._auth_headers = {"X-API-Key": api_key, "X-DSC-Path": dsc_path}

async def file_ecr(self, **params):
    """Execute file_ecr on epfo."""
    return await self._post("/file/ecr", params)


async def get_uan(self, **params):
    """Execute get_uan on epfo."""
    return await self._post("/get/uan", params)


async def check_claim_status(self, **params):
    """Execute check_claim_status on epfo."""
    return await self._post("/check/claim/status", params)


async def download_passbook(self, **params):
    """Execute download_passbook on epfo."""
    return await self._post("/download/passbook", params)


async def generate_trrn(self, **params):
    """Execute generate_trrn on epfo."""
    return await self._post("/generate/trrn", params)


async def verify_member(self, **params):
    """Execute verify_member on epfo."""
    return await self._post("/verify/member", params)

