# SPDX-License-Identifier: Apache-2.0
"""Merchant custom domains: DNS checks, the records a merchant publishes, the
domain-mapping step and the certificate probe.

A merchant serves its agent surfaces (``/.well-known/ucp``, ``/ucp/*``) on a
subdomain it owns, for example ``agent.merchant.example``:

1. ``CNAME agent.merchant.example -> ghs.googlehosted.com.`` routes the host to
   the Cloud Run domain mapping (a CNAME cannot sit at a zone apex, RFC 1034
   §3.6.2 and RFC 1912 §2.4, so an apex host is refused with a pointer to the
   apex guide).
2. ``TXT _agenticorg-ucp.agent.merchant.example`` carries the serving-host
   challenge (``core.commerce.ucp.authority``) and proves control of the host.
   It sits on its own label, so it never collides with the CNAME.
3. Google only creates a domain mapping for an account that is a verified
   Search Console owner of the base domain, so the merchant adds the mapping
   account as an owner there.

The mapping itself is created by an operator running the generated command, or,
with ``AGENTICORG_CUSTOM_DOMAIN_CLOUD_RUN_AUTOMAP_ENABLED``, through the Cloud
Run Admin API (``domains.cloudrun.com/v1`` DomainMapping). Without the Admin API
the certificate state is read from the outside: an HTTPS request to
``/.well-known/agenticorg-host`` on the host with full certificate
verification, over the egress guard's DNS-pinned transport (public addresses
only).
"""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.commerce.ucp.authority import normalize_host
from core.config import settings

logger = structlog.get_logger()

HOST_CHECK_PATH = "/.well-known/agenticorg-host"
HOST_CHECK_SERVICE = "agenticorg"
CLOUD_RUN_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
DNS_LIFETIME_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 8.0
API_TIMEOUT_SECONDS = 15.0

# Merchant-facing error codes. Messages never echo secrets; the TXT value is public.
ERROR_MESSAGES: dict[str, str] = {
    "invalid_host": "Enter a host name such as agent.merchant.example (no scheme, path, port or IP address).",
    "apex_domain_needs_subdomain": (
        "This name is the apex of its DNS zone, where a CNAME record is not allowed. Use a subdomain such as "
        "agent.<your domain>, then follow the apex guide to serve /.well-known/ucp from the apex."
    ),
    "host_in_use": "This host is already connected to another merchant. Remove it there first.",
    "host_bound_to_another_tenant": "This host is already connected to another AgenticOrg organization.",
    "ownership_txt_missing": (
        "The TXT ownership record was not found. Check its name and value; DNS changes can take up to an hour."
    ),
    "cname_missing": (
        "No CNAME record was found for the host. If your DNS provider proxies or flattens records, set this "
        "record to DNS only."
    ),
    "cname_wrong_target": "The CNAME record points somewhere else. Point it at the target shown.",
    "dns_lookup_failed": "DNS did not answer in time. Try again in a few minutes.",
    "search_console_owner_not_confirmed": (
        "Confirm that you added the mapping account as an owner of your domain in Google Search Console."
    ),
    "mapping_failed": "AgenticOrg could not connect the domain. Check the details, fix the records and retry.",
    "certificate_pending": "Google is issuing the certificate. This usually takes about 15 minutes, at most 24 hours.",
    "host_not_routed": "The host answers but does not reach AgenticOrg yet. Check the CNAME record.",
}


def error_message(code: str | None) -> str | None:
    return ERROR_MESSAGES.get(code or "") if code else None


def custom_domains_enabled() -> bool:
    return bool(getattr(settings, "custom_domains_enabled", False))


def cname_target() -> str:
    target = str(getattr(settings, "custom_domain_cname_target", "") or "ghs.googlehosted.com")
    return target.rstrip(".").lower()


def cloud_run_project() -> str:
    return str(getattr(settings, "custom_domain_cloud_run_project", "") or os.getenv("GOOGLE_CLOUD_PROJECT") or "")


def cloud_run_region() -> str:
    return str(getattr(settings, "custom_domain_cloud_run_region", "") or "asia-southeast1")


def cloud_run_service() -> str:
    return str(getattr(settings, "custom_domain_cloud_run_service", "") or "agenticorg-api")


def automap_enabled() -> bool:
    return bool(getattr(settings, "custom_domain_cloud_run_automap_enabled", False)) and bool(cloud_run_project())


def mapping_account() -> str | None:
    """The account merchants add as a Search Console owner (the one that creates mappings)."""
    value = str(getattr(settings, "custom_domain_mapping_account", "") or "").strip()
    return value or None


def normalize_custom_host(value: str | None) -> str | None:
    """A registered-domain host of at most 64 characters (the Cloud Run mapping limit), or None."""
    host = normalize_host(value)
    if host is None or len(host) > 64 or host.startswith("_"):
        return None
    return host


def parent_domain(host: str) -> str:
    labels = host.split(".")
    return ".".join(labels[1:]) if len(labels) > 2 else host


def dns_records(host: str, challenge: Mapping[str, Any] | None, *, target: str | None = None) -> list[dict[str, str]]:
    """The records the merchant publishes, exactly as a DNS provider asks for them."""
    records = [
        {
            "purpose": "routing",
            "type": "CNAME",
            "name": host,
            "value": f"{target or cname_target()}.",
            "note": "DNS only (not proxied). Do not add other records with this exact name.",
        }
    ]
    if isinstance(challenge, Mapping) and isinstance(challenge.get("record_name"), str):
        records.append(
            {
                "purpose": "ownership",
                "type": "TXT",
                "name": str(challenge["record_name"]),
                "value": str(challenge.get("record_value") or ""),
                "note": "Keep this record while the domain is connected.",
            }
        )
    return records


def search_console_step(host: str) -> dict[str, Any]:
    account = mapping_account()
    return {
        "account": account,
        "property": parent_domain(host),
        "instructions": (
            "In Google Search Console, verify the domain property that contains this host (for example "
            f"{parent_domain(host)}), then add {account or 'the AgenticOrg mapping account'} under Settings > "
            "Users and permissions > Verified owners > Add an owner. Keep it while the domain is connected."
        ),
    }


def mapping_commands(host: str) -> dict[str, str]:
    """The exact gcloud commands an operator runs for ``host`` (values are quoted for a POSIX shell)."""
    project = cloud_run_project() or "PROJECT_ID"
    common = f"--project={shlex.quote(project)} --region={shlex.quote(cloud_run_region())}"
    domain = shlex.quote(host)
    return {
        "create": (
            f"gcloud beta run domain-mappings create --service={shlex.quote(cloud_run_service())} "
            f"--domain={domain} {common}"
        ),
        "describe": f"gcloud beta run domain-mappings describe --domain={domain} {common}",
        "delete": f"gcloud beta run domain-mappings delete --domain={domain} {common} --quiet",
    }


# --- DNS -------------------------------------------------------------------

CnameResolver = Callable[[str], Awaitable[str | None]]
ApexChecker = Callable[[str], Awaitable[bool]]


class DnsLookupError(RuntimeError):
    """DNS timed out or no name server answered (retryable, not a wrong record)."""


def _resolver() -> Any:
    import dns.asyncresolver  # noqa: PLC0415

    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = DNS_LIFETIME_SECONDS
    resolver.timeout = DNS_LIFETIME_SECONDS
    return resolver


async def resolve_cname(host: str) -> str | None:
    """The CNAME target at ``host`` (lowercased, no trailing dot), or None when there is none."""
    import dns.exception  # noqa: PLC0415
    import dns.resolver  # noqa: PLC0415

    try:
        answer = await _resolver().resolve(host, "CNAME")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return None
    except (dns.exception.Timeout, dns.resolver.NoNameservers) as exc:
        raise DnsLookupError("cname lookup failed") from exc
    for rdata in answer:
        return str(rdata.target).rstrip(".").lower()
    return None


async def is_zone_apex(host: str) -> bool:
    """Whether ``host`` itself carries an SOA record (a zone apex, where a CNAME is not allowed)."""
    import dns.exception  # noqa: PLC0415
    import dns.resolver  # noqa: PLC0415

    try:
        answer = await _resolver().resolve(host, "SOA", raise_on_no_answer=False)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers, dns.exception.Timeout):
        return False
    rrset = answer.rrset
    return rrset is not None and str(rrset.name).rstrip(".").lower() == host


@dataclass(frozen=True)
class DnsCheck:
    ownership_ok: bool
    cname_ok: bool
    observed_cname: str | None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.ownership_ok and self.cname_ok and not self.errors


async def check_routing(host: str, *, resolver: CnameResolver = resolve_cname) -> tuple[bool, str | None, str | None]:
    """``(ok, observed_cname, error_code)`` for the host's CNAME record."""
    try:
        observed = await resolver(host)
    except DnsLookupError:
        return False, None, "dns_lookup_failed"
    if observed is None:
        return False, None, "cname_missing"
    if observed != cname_target():
        return False, observed, "cname_wrong_target"
    return True, observed, None


# --- Certificate and routing probe -----------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    state: str  # live | certificate_pending | host_not_routed
    detail: str | None = None


ClientFactory = Callable[[], Any]


def _probe_client() -> Any:
    import httpx  # noqa: PLC0415

    from core.security.egress import build_pinned_async_transport  # noqa: PLC0415

    return httpx.AsyncClient(
        transport=build_pinned_async_transport(require_dns=True),
        timeout=PROBE_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"User-Agent": "AgenticOrg-DomainCheck/1"},
    )


async def probe_host(host: str, *, client_factory: ClientFactory = _probe_client) -> ProbeResult:
    """HTTPS GET of ``/.well-known/agenticorg-host`` on ``host`` with certificate verification.

    ``live`` only when TLS verifies for ``host`` and this service answers with
    the same host (the ``Host`` header reached the API intact).
    """
    import httpx  # noqa: PLC0415

    from core.security.egress import EgressValidationError  # noqa: PLC0415

    try:
        async with client_factory() as client:
            response = await client.get(f"https://{host}{HOST_CHECK_PATH}")
    except EgressValidationError as exc:
        return ProbeResult("host_not_routed", f"host resolves to a refused address ({exc.reason})")
    except httpx.ConnectError as exc:
        # A Google front end without the host's certificate fails verification here.
        return ProbeResult("certificate_pending", type(exc).__name__)
    except httpx.HTTPError as exc:
        return ProbeResult("certificate_pending", type(exc).__name__)
    if response.status_code != 200:
        return ProbeResult("host_not_routed", f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        return ProbeResult("host_not_routed", "unexpected response")
    if not isinstance(body, Mapping) or body.get("service") != HOST_CHECK_SERVICE or body.get("host") != host:
        return ProbeResult("host_not_routed", "unexpected response")
    return ProbeResult("live")


# --- Cloud Run Admin API (optional automation) ------------------------------


@dataclass(frozen=True)
class MappingState:
    exists: bool
    ready: bool
    failed: bool
    message: str | None = None


def _api_base() -> str:
    # v1 needs a locational endpoint (Cloud Run REST reference, "Service endpoint").
    return (
        f"https://{cloud_run_region()}-run.googleapis.com/apis/domains.cloudrun.com/v1/namespaces/{cloud_run_project()}"
    )


def _access_token_sync() -> str:
    import google.auth  # noqa: PLC0415
    import google.auth.transport.requests  # noqa: PLC0415

    credentials, _project = google.auth.default(scopes=[CLOUD_RUN_SCOPE])
    credentials.refresh(google.auth.transport.requests.Request())
    return str(credentials.token)


async def _access_token() -> str:
    # google-auth refreshes synchronously; keep it off the event loop.
    return await asyncio.to_thread(_access_token_sync)


def parse_mapping_state(document: Mapping[str, Any]) -> MappingState:
    """Ready when ``observedGeneration`` caught up and the ``Ready`` condition is ``True``."""
    status = document.get("status") or {}
    metadata = document.get("metadata") or {}
    conditions = status.get("conditions") or []
    ready = next((c for c in conditions if isinstance(c, Mapping) and c.get("type") == "Ready"), None)
    caught_up = status.get("observedGeneration") == metadata.get("generation")
    message = str(ready.get("message"))[:300] if isinstance(ready, Mapping) and ready.get("message") else None
    if not caught_up or not isinstance(ready, Mapping):
        return MappingState(exists=True, ready=False, failed=False, message=message)
    return MappingState(
        exists=True,
        ready=ready.get("status") == "True",
        failed=ready.get("status") == "False",
        message=message,
    )


async def get_domain_mapping(host: str, *, client: Any | None = None) -> MappingState:
    import httpx  # noqa: PLC0415

    token = await _access_token()
    owned = client is None
    client = client or httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS)
    try:
        response = await client.get(
            f"{_api_base()}/domainmappings/{host}", headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        if owned:
            await client.aclose()
    if response.status_code == 404:
        return MappingState(exists=False, ready=False, failed=False)
    response.raise_for_status()
    return parse_mapping_state(response.json())


async def create_domain_mapping(host: str, *, client: Any | None = None) -> MappingState:
    """Create the mapping for ``host`` on the configured service; an existing one is read instead."""
    import httpx  # noqa: PLC0415

    token = await _access_token()
    body = {
        "apiVersion": "domains.cloudrun.com/v1",
        "kind": "DomainMapping",
        "metadata": {"name": host, "namespace": cloud_run_project()},
        "spec": {"routeName": cloud_run_service(), "certificateMode": "AUTOMATIC"},
    }
    owned = client is None
    client = client or httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS)
    try:
        response = await client.post(
            f"{_api_base()}/domainmappings", json=body, headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code == 409:
            response = await client.get(
                f"{_api_base()}/domainmappings/{host}", headers={"Authorization": f"Bearer {token}"}
            )
    finally:
        if owned:
            await client.aclose()
    if response.status_code >= 400:
        detail = ""
        try:
            detail = str(((response.json() or {}).get("error") or {}).get("message") or "")
        except ValueError:
            detail = ""
        return MappingState(
            exists=False, ready=False, failed=True, message=(detail or f"HTTP {response.status_code}")[:300]
        )
    return parse_mapping_state(response.json())
