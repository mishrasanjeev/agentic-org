"""Account Aggregator consent lifecycle manager.

Implements the full RBI NBFC-AA consent flow for Finvu:
1. Create consent request → get consent_handle + redirect URL
2. User approves on Finvu consent UI
3. Callback received → consent_id available
4. Fetch signed consent artifact
5. Create FI data session using consent_id
6. Fetch financial data using session_id
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from connectors.finance.aa_consent_types import (
    ConsentArtifact,
    ConsentRequest,
    ConsentStatus,
    FIDataSession,
)

logger = structlog.get_logger()


class AAConsentManager:
    """Manages Account Aggregator consent lifecycle per RBI NBFC-AA guidelines."""

    def __init__(
        self,
        base_url: str = "https://aa.finvu.in/api/v1",
        client_id: str = "",
        client_secret: str = "",
        callback_url: str = "",
        fiu_id: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url
        self.fiu_id = fiu_id

        # In-memory consent store (DB-backed in production)
        self._consents: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._token: str = ""

    async def _get_token(self) -> str:
        """Obtain AA API access token via client credentials."""
        if self._token:
            return self._token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
        return self._token

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "x-fiu-id": self.fiu_id,
        }

    async def create_consent_request(
        self,
        request: ConsentRequest,
    ) -> dict[str, str]:
        """Create a consent request with Finvu AA.

        Returns:
            dict with consent_handle and redirect_url for user approval.
        """
        await self._get_token()

        consent_handle = str(uuid.uuid4())
        payload = {
            "ver": "2.0.0",
            "txnid": str(uuid.uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "ConsentDetail": {
                "consentStart": datetime.now(UTC).isoformat(),
                "consentExpiry": "9999-12-31T00:00:00.000Z",
                "consentMode": request.consent_mode,
                "fetchType": request.fetch_type,
                "consentTypes": ["PROFILE", "SUMMARY", "TRANSACTIONS"],
                "fiTypes": [ft.value for ft in request.fi_types],
                "DataConsumer": {"id": self.fiu_id},
                "Customer": {"id": request.customer_vua},
                "Purpose": {
                    "code": str(request.purpose_code.value),
                    "refUri": "https://api.rebit.org.in/aa/purpose/101.xml",
                    "text": request.purpose_code.name.replace("_", " ").title(),
                    "Category": {"type": "string"},
                },
                "FIDataRange": {
                    "from": f"{request.from_date}T00:00:00.000Z",
                    "to": f"{request.to_date}T23:59:59.000Z",
                },
                "DataLife": {
                    "unit": request.data_life_unit,
                    "value": request.data_life_value,
                },
                "Frequency": {
                    "unit": request.frequency_unit,
                    "value": request.frequency_value,
                },
                "DataFilter": [],
            },
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/Consent",
                json=payload,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        actual_handle = data.get("ConsentHandle", consent_handle)

        # Store consent state
        self._consents[actual_handle] = {
            "consent_handle": actual_handle,
            "customer_vua": request.customer_vua,
            "status": ConsentStatus.PENDING,
            "consent_id": "",
            "fi_types": [ft.value for ft in request.fi_types],
            "purpose_code": request.purpose_code.value,
            "from_date": request.from_date,
            "to_date": request.to_date,
            "created_at": datetime.now(UTC).isoformat(),
        }

        redirect_url = (
            f"https://finvu.in/consent/{actual_handle}"
            f"?redirect={self.callback_url}"
        )

        logger.info(
            "aa_consent_created",
            consent_handle=actual_handle,
            customer=request.customer_vua,
        )

        return {
            "consent_handle": actual_handle,
            "redirect_url": redirect_url,
        }

    async def handle_consent_callback(
        self,
        consent_handle: str,
        consent_status: ConsentStatus,
        consent_id: str = "",
    ) -> dict[str, Any]:
        """Process consent callback from Finvu AA."""
        record = self._consents.get(consent_handle)
        if not record:
            logger.warning("aa_consent_unknown_handle", handle=consent_handle)
            return {"error": "Unknown consent handle"}

        record["status"] = consent_status
        if consent_id:
            record["consent_id"] = consent_id

        logger.info(
            "aa_consent_callback",
            consent_handle=consent_handle,
            status=consent_status.value,
            consent_id=consent_id,
        )

        return {
            "consent_handle": consent_handle,
            "status": consent_status.value,
            "consent_id": consent_id,
        }

    async def fetch_consent_artifact(self, consent_id: str) -> ConsentArtifact:
        """Fetch the signed consent artifact from Finvu."""
        await self._get_token()

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/Consent/{consent_id}",
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        artifact = ConsentArtifact(
            consent_id=consent_id,
            consent_handle=data.get("ConsentHandle", ""),
            status=ConsentStatus(data.get("ConsentStatus", "APPROVED")),
            customer_vua=data.get("Customer", {}).get("id", ""),
            fi_types=data.get("fiTypes", []),
            purpose_code=int(data.get("Purpose", {}).get("code", 103)),
            from_date=data.get("FIDataRange", {}).get("from", ""),
            to_date=data.get("FIDataRange", {}).get("to", ""),
            created_at=data.get("createTimestamp", ""),
            signed_consent=data.get("ConsentSignature", ""),
            consent_expiry=data.get("consentExpiry", ""),
        )

        logger.info("aa_consent_artifact_fetched", consent_id=consent_id)
        return artifact

    async def create_fi_session(
        self,
        consent_id: str,
        from_date: str,
        to_date: str,
    ) -> FIDataSession:
        """Create an FI data fetch session using an approved consent."""
        await self._get_token()

        session_id = str(uuid.uuid4())
        payload = {
            "ver": "2.0.0",
            "txnid": str(uuid.uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "FIDataRange": {
                "from": f"{from_date}T00:00:00.000Z",
                "to": f"{to_date}T23:59:59.000Z",
            },
            "Consent": {"id": consent_id},
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/FI/request",
                json=payload,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        actual_session_id = data.get("sessionId", session_id)
        session = FIDataSession(
            session_id=actual_session_id,
            consent_id=consent_id,
            status="ACTIVE",
            created_at=datetime.now(UTC).isoformat(),
            data_ranges=[{"from": from_date, "to": to_date}],
        )

        self._sessions[actual_session_id] = session.model_dump()
        logger.info("aa_fi_session_created", session_id=actual_session_id)
        return session

    async def fetch_fi_data(self, session_id: str) -> dict[str, Any]:
        """Fetch financial data using an active FI session."""
        await self._get_token()

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/FI/fetch/{session_id}",
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        logger.info("aa_fi_data_fetched", session_id=session_id)
        return data

    async def revoke_consent(self, consent_id: str) -> dict[str, str]:
        """Revoke a previously granted consent."""
        await self._get_token()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/Consent/revoke/{consent_id}",
                headers=self._auth_headers(),
            )
            resp.raise_for_status()

        # Update local state
        for record in self._consents.values():
            if record.get("consent_id") == consent_id:
                record["status"] = ConsentStatus.REVOKED

        logger.info("aa_consent_revoked", consent_id=consent_id)
        return {"consent_id": consent_id, "status": "REVOKED"}

    def get_consent_status(self, consent_handle: str) -> dict[str, Any]:
        """Get local consent state by handle."""
        record = self._consents.get(consent_handle)
        if not record:
            return {"error": "Not found"}
        return {
            "consent_handle": consent_handle,
            "status": record["status"].value if hasattr(record["status"], "value") else record["status"],
            "consent_id": record.get("consent_id", ""),
        }
