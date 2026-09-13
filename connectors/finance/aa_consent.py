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

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
import structlog

from connectors.finance.aa_consent_types import (
    ConsentArtifact,
    ConsentRequest,
    ConsentStatus,
    FIDataSession,
)
from core.http_retry import DEFAULT_HTTP_TIMEOUT_SECONDS, retry_http_async
from core.security.egress import EgressValidationError, validate_public_url

logger = structlog.get_logger()
_AA_TIMEOUT = httpx.Timeout(DEFAULT_HTTP_TIMEOUT_SECONDS)

# Consent handles outlive the API process that created them: the AA provider
# calls back minutes to days later, possibly to another replica. Records are
# therefore persisted (Redis, tenant-scoped, TTL) instead of a per-process dict.
CONSENT_RECORD_TTL_SECONDS = 7 * 24 * 3600


class ConsentStore(Protocol):
    """Durable consent-handle state, keyed by the provider's consent handle."""

    async def get(self, consent_handle: str) -> dict[str, Any] | None: ...

    async def put(self, consent_handle: str, record: dict[str, Any]) -> None: ...

    async def handles_for_consent_id(self, consent_id: str) -> list[str]: ...


class MemoryConsentStore(dict):
    """Process-local store — tests and single-process dev only."""

    async def get(self, consent_handle: str) -> dict[str, Any] | None:  # type: ignore[override]
        return dict.get(self, consent_handle)

    async def put(self, consent_handle: str, record: dict[str, Any]) -> None:
        self[consent_handle] = record

    async def handles_for_consent_id(self, consent_id: str) -> list[str]:
        return [h for h, rec in self.items() if rec.get("consent_id") == consent_id]


def _serialise_record(record: dict[str, Any]) -> str:
    payload = dict(record)
    status = payload.get("status")
    if isinstance(status, ConsentStatus):
        payload["status"] = status.value
    return json.dumps(payload)


def _deserialise_record(raw: str) -> dict[str, Any]:
    payload = json.loads(raw)
    status = payload.get("status")
    if isinstance(status, str):
        try:
            payload["status"] = ConsentStatus(status)
        except ValueError:
            pass
    return payload


class RedisConsentStore:
    """Tenant-scoped Redis-backed store shared by every API replica.

    Keys:
      aa:consent:{tenant_id}:{handle}          -> JSON record (TTL)
      aa:consent_by_id:{tenant_id}:{consent_id} -> handle (TTL)
      aa:consent_tenant:{handle}               -> tenant_id (TTL) — lets the
        unauthenticated provider callback find the owning tenant.
    """

    def __init__(self, redis: Any, tenant_id: str, ttl_seconds: int = CONSENT_RECORD_TTL_SECONDS) -> None:
        self._redis = redis
        self._tenant_id = str(tenant_id)
        self._ttl = ttl_seconds

    @staticmethod
    def tenant_key(consent_handle: str) -> str:
        return f"aa:consent_tenant:{consent_handle}"

    def _record_key(self, consent_handle: str) -> str:
        return f"aa:consent:{self._tenant_id}:{consent_handle}"

    def _by_id_key(self, consent_id: str) -> str:
        return f"aa:consent_by_id:{self._tenant_id}:{consent_id}"

    async def get(self, consent_handle: str) -> dict[str, Any] | None:
        raw = await self._redis.get(self._record_key(consent_handle))
        if not raw:
            return None
        return _deserialise_record(raw)

    async def put(self, consent_handle: str, record: dict[str, Any]) -> None:
        await self._redis.set(self._record_key(consent_handle), _serialise_record(record), ex=self._ttl)
        await self._redis.set(self.tenant_key(consent_handle), self._tenant_id, ex=self._ttl)
        consent_id = record.get("consent_id")
        if consent_id:
            await self._redis.set(self._by_id_key(str(consent_id)), consent_handle, ex=self._ttl)

    async def handles_for_consent_id(self, consent_id: str) -> list[str]:
        handle = await self._redis.get(self._by_id_key(consent_id))
        return [handle] if handle else []

    @classmethod
    async def tenant_for_handle(cls, redis: Any, consent_handle: str) -> str | None:
        value = await redis.get(cls.tenant_key(consent_handle))
        return str(value) if value else None


class AAConsentManager:
    """Manages Account Aggregator consent lifecycle per RBI NBFC-AA guidelines."""

    def __init__(
        self,
        base_url: str = "https://aa.finvu.in/api/v1",
        client_id: str = "",
        client_secret: str = "",
        callback_url: str = "",
        fiu_id: str = "",
        store: ConsentStore | None = None,
    ):
        cleaned_base_url = base_url.rstrip("/")
        try:
            validate_public_url(
                cleaned_base_url,
                allowed_schemes=("https",),
                allowed_hosts=("aa.finvu.in",),
                require_dns=False,
            )
        except EgressValidationError as exc:
            raise ValueError("AA consent base_url must be the official Finvu HTTPS API host") from exc
        self.base_url = cleaned_base_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url
        self.fiu_id = fiu_id

        # Durable consent state — RedisConsentStore in the API, memory in tests.
        self._consents: ConsentStore = store if store is not None else MemoryConsentStore()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._token: str = ""

    @retry_http_async(max_attempts=3, base_delay=0.25, cap=2.0)
    async def _get_token(self) -> str:
        """Obtain AA API access token via client credentials."""
        if self._token:
            return self._token

        async with httpx.AsyncClient(timeout=_AA_TIMEOUT) as client:
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

        async with httpx.AsyncClient(timeout=_AA_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/Consent",
                json=payload,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        actual_handle = data.get("ConsentHandle", consent_handle)

        # Store consent state
        await self._consents.put(actual_handle, {
            "consent_handle": actual_handle,
            "customer_vua": request.customer_vua,
            "status": ConsentStatus.PENDING,
            "consent_id": "",
            "fi_types": [ft.value for ft in request.fi_types],
            "purpose_code": request.purpose_code.value,
            "from_date": request.from_date,
            "to_date": request.to_date,
            "created_at": datetime.now(UTC).isoformat(),
        })

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
        record = await self._consents.get(consent_handle)
        if not record:
            logger.warning("aa_consent_unknown_handle", handle=consent_handle)
            return {"error": "Unknown consent handle"}

        record["status"] = consent_status
        if consent_id:
            record["consent_id"] = consent_id
        await self._consents.put(consent_handle, record)

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

        async with httpx.AsyncClient(timeout=_AA_TIMEOUT) as client:
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

        async with httpx.AsyncClient(timeout=_AA_TIMEOUT) as client:
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

        async with httpx.AsyncClient(timeout=_AA_TIMEOUT) as client:
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

        async with httpx.AsyncClient(timeout=_AA_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/Consent/revoke/{consent_id}",
                headers=self._auth_headers(),
            )
            resp.raise_for_status()

        # Update durable state
        for handle in await self._consents.handles_for_consent_id(consent_id):
            record = await self._consents.get(handle)
            if record is not None:
                record["status"] = ConsentStatus.REVOKED
                await self._consents.put(handle, record)

        logger.info("aa_consent_revoked", consent_id=consent_id)
        return {"consent_id": consent_id, "status": "REVOKED"}

    async def get_consent_status(self, consent_handle: str) -> dict[str, Any]:
        """Get stored consent state by handle."""
        record = await self._consents.get(consent_handle)
        if not record:
            return {"error": "Not found"}
        return {
            "consent_handle": consent_handle,
            "status": record["status"].value if hasattr(record["status"], "value") else record["status"],
            "consent_id": record.get("consent_id", ""),
        }
