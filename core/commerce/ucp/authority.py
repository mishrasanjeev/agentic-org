# SPDX-License-Identifier: Apache-2.0
"""UCP namespace authority binding and the serving-host attestation.

Authority binding (UCP 2026-08-25 overview, Authority Binding, Derivation
algorithm): an entity named ``name`` with a remote ``schema`` URL is bound when
the URL is ``https`` without userinfo, its host is a registered domain of at
least two labels (no IP literal, no single label), and ``name`` equals the
host's labels reversed (lowercased, trailing dot stripped, IDNs as A-labels) or
starts with that prefix plus ``"."``. The Public Suffix List is not consulted.
In 2026-04-08 both ``spec`` and ``schema`` origins must match the namespace
(overview, Spec URL Binding); 2026-08-25 binds ``schema`` only.

The serving-host attestation proves the merchant controls the host its UCP
profile is served on: a DNS TXT record ``_agenticorg-ucp.<host>`` carrying
``agenticorg-ucp-verify=<challenge>`` (the ACME dns-01 pattern, RFC 8555
§8.4). It is looked up asynchronously (dnspython ``dns.asyncresolver``).
The merchant's vendor namespace (``com.example.shop`` for
``shop.example.com``) must be authority-bound to the attested host before any
capability, service or payment handler in that namespace is published.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import structlog

from core.commerce.ucp.versions import UCP_2026_04_08

logger = structlog.get_logger()

UCP_GOVERNED_PREFIX = "dev.ucp"
UCP_SCHEMA_HOST = "ucp.dev"
DNS_CHALLENGE_LABEL = "_agenticorg-ucp"
DNS_CHALLENGE_VALUE_PREFIX = "agenticorg-ucp-verify="
ATTESTATION_METHOD_DNS_TXT = "dns_txt"

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_REVERSE_DNS_RE = re.compile(r"^[a-z](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9_-]*[a-z0-9_])?)+$")


def normalize_host(value: str | None) -> str | None:
    """A registered-domain host name from a ``Host`` header or URL host, or None.

    Lowercased, port and trailing dot stripped, IDN labels converted to
    A-labels; IP literals, single-label names and malformed labels are refused.
    """
    if value is None:
        return None
    text = value.strip()
    if not text or "@" in text or "/" in text or "\\" in text:
        return None
    if text.startswith("["):
        return None
    if text.count(":") == 1:
        text, _, port = text.partition(":")
        if not port.isdigit():
            return None
    elif ":" in text:
        return None
    text = text.rstrip(".").lower()
    if not text:
        return None
    try:
        ipaddress.ip_address(text)
    except ValueError:
        pass
    else:
        return None
    try:
        ascii_host = text.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = ascii_host.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.fullmatch(label) for label in labels):
        return None
    if labels[-1].isdigit():
        return None
    return ascii_host


def authority_prefix(host: str) -> str:
    """The reversed labels of a normalized host (``shop.example.com`` -> ``com.example.shop``)."""
    return ".".join(reversed(host.split(".")))


def is_reverse_domain_name(name: str) -> bool:
    return bool(_REVERSE_DNS_RE.fullmatch(name))


def name_bound_to_host(name: str, host: str | None) -> bool:
    if host is None:
        return False
    prefix = authority_prefix(host)
    return name == prefix or name.startswith(prefix + ".")


def name_bound_to_url(name: str, url: str | None) -> bool:
    """UCP Derivation algorithm: ``url`` is https, no userinfo, and its host authorizes ``name``."""
    if not url:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
        return False
    return name_bound_to_host(name, normalize_host(parsed.hostname))


def is_ucp_governed(name: str) -> bool:
    return name == UCP_GOVERNED_PREFIX or name.startswith(UCP_GOVERNED_PREFIX + ".")


def entry_authority_valid(name: str, entry: Mapping[str, Any], version: str) -> bool:
    """Whether a profile entry's URLs are authority-bound to its name for ``version``.

    ``dev.ucp.*`` entries must point at ``ucp.dev``; vendor entries at their own
    registrable domain. 2026-04-08 checks ``spec`` and ``schema``, 2026-08-25
    ``schema`` only. An entry without ``schema`` is not bound.
    """
    if not is_reverse_domain_name(name):
        return False
    fields = ("spec", "schema") if version == UCP_2026_04_08 else ("schema",)
    for field_name in fields:
        url = entry.get(field_name)
        if field_name == "spec" and url is None:
            continue
        if not isinstance(url, str) or not name_bound_to_url(name, url):
            return False
    return isinstance(entry.get("schema"), str)


def merchant_namespace_for_host(host: str, requested: str | None = None) -> str:
    """The merchant's reverse-DNS namespace: the attested host reversed, or a label-aligned extension of it.

    Raises ``ValueError`` when ``requested`` is not authority-bound to ``host``
    (for example ``com.example`` for ``shop.example.com``).
    """
    prefix = authority_prefix(host)
    if requested is None or requested == "":
        return prefix
    namespace = requested.strip().lower()
    if not is_reverse_domain_name(namespace) or is_ucp_governed(namespace):
        raise ValueError("namespace must be a reverse-DNS name outside dev.ucp")
    if not name_bound_to_host(namespace, host):
        raise ValueError("namespace is not authority-bound to the serving host")
    return namespace


def new_dns_challenge(host: str) -> dict[str, str]:
    """A fresh DNS TXT challenge for ``host`` (the value is public; it is published in DNS)."""
    value = DNS_CHALLENGE_VALUE_PREFIX + secrets.token_urlsafe(32)
    return {
        "method": ATTESTATION_METHOD_DNS_TXT,
        "host": host,
        "record_name": f"{DNS_CHALLENGE_LABEL}.{host}",
        "record_type": "TXT",
        "record_value": value,
    }


def challenge_value_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


TxtResolver = Callable[[str], Awaitable[list[str]]]


async def resolve_txt_records(record_name: str) -> list[str]:
    """TXT strings at ``record_name`` (async DNS, 5 s lifetime); empty on NXDOMAIN or no answer."""
    import dns.asyncresolver
    import dns.exception
    import dns.resolver

    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = 5.0
    resolver.timeout = 5.0
    try:
        answer = await resolver.resolve(record_name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.Timeout:
        logger.warning("ucp_dns_challenge_lookup_timeout")
        return []
    values: list[str] = []
    for rdata in answer:
        strings = getattr(rdata, "strings", ())
        values.append(b"".join(strings).decode("utf-8", errors="replace"))
    return values


async def dns_challenge_satisfied(
    challenge: Mapping[str, Any],
    *,
    resolver: TxtResolver = resolve_txt_records,
) -> bool:
    """Whether the TXT record named by ``challenge`` currently carries its value."""
    record_name = challenge.get("record_name")
    expected = challenge.get("record_value")
    if not isinstance(record_name, str) or not isinstance(expected, str):
        return False
    values = await resolver(record_name)
    expected_bytes = expected.encode("utf-8")
    return any(hmac.compare_digest(value.strip().encode("utf-8"), expected_bytes) for value in values)
