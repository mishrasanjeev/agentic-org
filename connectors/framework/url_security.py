"""URL helpers for connector-owned outbound endpoints."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_SAP_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{1,18}[a-z0-9]$")


def require_dns_label(value: object, field: str) -> str:
    """Return a lower-case DNS label or raise when it can alter the host."""
    label = str(value or "").strip().lower()
    if not _DNS_LABEL_RE.fullmatch(label):
        raise ValueError(f"{field} must be a single DNS label")
    return label


def require_sap_region(value: object) -> str:
    region = str(value or "eu10").strip().lower()
    if not _SAP_REGION_RE.fullmatch(region) or "." in region:
        raise ValueError("SAP region must be a single provider region label")
    return region


def require_https_origin(
    value: object,
    *,
    field: str,
    allowed_exact_hosts: Iterable[str] = (),
    allowed_host_suffixes: Iterable[str] = (),
) -> str:
    """Validate a provider-returned HTTPS origin against exact/suffix host rules."""
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").rstrip(".").lower()
    exact_hosts = {item.lower() for item in allowed_exact_hosts}
    suffixes = tuple(item.lower() for item in allowed_host_suffixes)

    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError(f"{field} must be a HTTPS origin")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"{field} must not include path, query, or fragment")
    if host not in exact_hosts and not any(host.endswith(suffix) for suffix in suffixes):
        raise ValueError(f"{field} host is not an allowed provider host")
    return f"https://{host}"
