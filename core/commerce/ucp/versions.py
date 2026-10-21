# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""UCP release selection and the ``UCP-Agent`` request header.

UCP releases are dated (``YYYY-MM-DD``). A business profile declares one
``ucp.version``; the business serves its newest configured release at
``/.well-known/ucp`` and advertises older ones in ``ucp.supported_versions`` as
leaf profiles (UCP 2026-08-25 overview, Protocol Version). Every response echoes
the negotiated version, and a platform version the business does not serve is
refused with ``version_unsupported`` (overview, Request-Time Validation).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

UCP_2026_04_08 = "2026-04-08"
UCP_2026_08_25 = "2026-08-25"
KNOWN_UCP_VERSIONS: tuple[str, ...] = (UCP_2026_04_08, UCP_2026_08_25)
DEFAULT_UCP_VERSIONS: tuple[str, ...] = (UCP_2026_04_08,)

logger = structlog.get_logger()

_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_ucp_versions(raw: str | None) -> tuple[str, ...]:
    """The served releases from ``AGENTICORG_UCP_VERSIONS``, oldest first.

    Unknown or malformed entries are dropped and logged; an empty result falls
    back to 2026-04-08 so a typo never publishes an unimplemented release.
    """
    requested = [item.strip() for item in (raw or "").split(",") if item.strip()]
    served = sorted({item for item in requested if item in KNOWN_UCP_VERSIONS})
    dropped = sorted({item for item in requested if item not in KNOWN_UCP_VERSIONS})
    if dropped:
        logger.warning("ucp_versions_unknown_dropped", dropped=dropped[:5])
    return tuple(served) or DEFAULT_UCP_VERSIONS


def configured_ucp_versions() -> tuple[str, ...]:
    from core.config import settings

    return parse_ucp_versions(getattr(settings, "ucp_versions", None))


def current_ucp_version(versions: tuple[str, ...]) -> str:
    """The release served at ``/.well-known/ucp``: the newest configured one."""
    return max(versions)


@dataclass(frozen=True)
class UcpAgentHeader:
    """The parsed ``UCP-Agent`` RFC 8941 dictionary (``profile`` and its parameters)."""

    profile: str | None
    version: str | None


class UcpAgentHeaderError(ValueError):
    """The ``UCP-Agent`` header is not a well-formed RFC 8941 dictionary."""


def parse_ucp_agent(value: str | None) -> UcpAgentHeader | None:
    """Parse ``UCP-Agent: profile="<https url>"`` (RFC 8941 dictionary, ``profile`` member).

    A ``version`` parameter on the ``profile`` member (or a ``version`` member)
    is read as the platform's declared release. Returns ``None`` when the header
    is absent.
    """
    if value is None or not value.strip():
        return None
    members = _parse_sf_dictionary(value)
    profile_value, profile_params = members.get("profile", (None, {}))
    version = profile_params.get("version")
    if version is None and "version" in members:
        version = members["version"][0]
    if profile_value is not None and not isinstance(profile_value, str):
        raise UcpAgentHeaderError("profile must be a string")
    if version is not None and (not isinstance(version, str) or not _VERSION_RE.fullmatch(version)):
        raise UcpAgentHeaderError("version must be YYYY-MM-DD")
    return UcpAgentHeader(profile=profile_value, version=version)


def _parse_sf_dictionary(text: str) -> dict[str, tuple[object, dict[str, object]]]:
    """A strict subset of RFC 8941 §4.2.2 (dictionaries of strings, tokens and booleans with parameters)."""
    members: dict[str, tuple[object, dict[str, object]]] = {}
    pos = 0
    length = len(text)

    def skip_ows(index: int) -> int:
        while index < length and text[index] in " \t":
            index += 1
        return index

    def parse_key(index: int) -> tuple[str, int]:
        match = re.compile(r"[a-z*][a-z0-9_\-.*]*").match(text, index)
        if match is None:
            raise UcpAgentHeaderError("invalid dictionary key")
        return match.group(0), match.end()

    def parse_bare_item(index: int) -> tuple[object, int]:
        if index < length and text[index] == '"':
            index += 1
            out: list[str] = []
            while index < length:
                char = text[index]
                if char == "\\":
                    if index + 1 >= length or text[index + 1] not in '"\\':
                        raise UcpAgentHeaderError("invalid string escape")
                    out.append(text[index + 1])
                    index += 2
                    continue
                if char == '"':
                    return "".join(out), index + 1
                if not (0x20 <= ord(char) <= 0x7E):
                    raise UcpAgentHeaderError("invalid string character")
                out.append(char)
                index += 1
            raise UcpAgentHeaderError("unterminated string")
        if index < length and text[index] == "?":
            if text[index + 1 : index + 2] in {"0", "1"}:
                return text[index + 1] == "1", index + 2
            raise UcpAgentHeaderError("invalid boolean")
        match = re.compile(r"[A-Za-z*][A-Za-z0-9!#$%&'*+\-.^_`|~:/]*").match(text, index)
        if match is None:
            raise UcpAgentHeaderError("unsupported item")
        return match.group(0), match.end()

    def parse_params(index: int) -> tuple[dict[str, object], int]:
        params: dict[str, object] = {}
        while index < length and text[index] == ";":
            index = skip_ows(index + 1)
            key, index = parse_key(index)
            value: object = True
            if index < length and text[index] == "=":
                value, index = parse_bare_item(index + 1)
            params[key] = value
        return params, index

    pos = skip_ows(pos)
    while pos < length:
        key, pos = parse_key(pos)
        item: object = True
        if pos < length and text[pos] == "=":
            item, pos = parse_bare_item(pos + 1)
        params, pos = parse_params(pos)
        members[key] = (item, params)
        pos = skip_ows(pos)
        if pos >= length:
            break
        if text[pos] != ",":
            raise UcpAgentHeaderError("expected comma between members")
        pos = skip_ows(pos + 1)
        if pos >= length:
            raise UcpAgentHeaderError("trailing comma")
    return members
