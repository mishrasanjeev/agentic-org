# SPDX-License-Identifier: Apache-2.0
"""Sandboxed extraction worker. Runs in its own process; never imported to extract in-process.

The parent (``core.extraction.sandbox``) starts this file by path with
``python -I -B`` and a minimal environment, writes one JSON request to stdin
and reads one JSON response from stdout. This module uses the standard library
only and imports nothing from the application, so it can run isolated.

Before reading any content the worker locks itself down (``_install_sandbox``):

* POSIX resource limits: address space, CPU seconds, open files, no file
  writes, no new processes.
* Linux: a new user and network namespace when the kernel allows it (no
  interfaces but loopback), and a seccomp filter that makes ``socket``,
  ``socketpair``, ``execve``, ``execveat``, ``ptrace`` and io_uring fail with
  ``EACCES``.
* Every platform: an audit hook that refuses socket creation and use, name
  resolution, process creation, ``ctypes`` and opening files for writing.
  Audit hooks cannot be removed once installed.

The response reports which layers were active. Content is decoded as UTF-8
only, parsed with pure-Python parsers, and reduced to the typed fields in
``FIELDS``; every string is normalised, length-capped and checked against a
character class, and anything that does not fit is dropped and listed in
``rejected_fields``. Short source excerpts are returned separately for human
reviewers.

Functions marked ``pragma: no cover`` run only inside the worker process, where
in-process coverage cannot see them; tests/unit/extraction/test_extraction_sandbox.py
exercises them through real worker processes.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import html.parser
import json
import os
import platform
import re
import sys
import unicodedata
from typing import Any
from urllib.parse import urlsplit

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_EXCERPT_CHARS = 300
MAX_EXCERPTS = 50
MAX_LINE_CHARS = 2000

# ── Field schema (shared with the parent, which re-validates every response) ──

NAME_PATTERN = r"[^\W_](?:[^\W_]|[ &'.,()/|:!+\u2013\u2014-])*"
DOMAIN_PATTERN = r"(?=.{4,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
COMPANY_NUMBER_PATTERN = r"[A-Z0-9]{1,12}"
JURISDICTION_PATTERN = r"[a-z]{2}(?:-[a-z0-9]{1,3})?"
DATE_PATTERN = r"\d{4}-\d{2}-\d{2}"

ACTIVITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "construction": ("construction", "builder", "builders", "joinery", "carpentry", "roofing"),
    "consulting": ("consulting", "consultancy", "advisory"),
    "education": ("education", "tutoring", "training", "school"),
    "financial_services": ("bank", "banking", "lending", "loans", "payments", "brokerage", "insurance"),
    "food_service": ("restaurant", "cafe", "catering", "bakery"),
    "healthcare": ("clinic", "healthcare", "pharmacy", "dental"),
    "logistics": ("logistics", "freight", "courier", "shipping", "warehousing"),
    "manufacturing": ("manufacturing", "manufacturer", "factory", "fabrication"),
    "real_estate": ("property", "lettings", "realty", "estate"),
    "retail": ("retail", "shop", "store", "boutique"),
    "software": ("software", "saas", "app", "platform"),
}
ACTIVITY_CATEGORIES = tuple(sorted(ACTIVITY_KEYWORDS))
REGISTRY_STATUSES = ("active", "dissolved", "dormant", "inactive", "liquidation")
FILING_TYPES = ("accounts", "annual_report", "articles", "change_of_officers", "confirmation_statement", "other")

SOURCE_KINDS = ("website", "registry_document", "applicant_upload")
CONTENT_TYPES: dict[str, tuple[str, ...]] = {
    "website": ("text/html",),
    "registry_document": ("text/plain", "application/json"),
    "applicant_upload": ("text/plain", "application/json"),
}


def _string(pattern: str, max_length: int, *, untrusted_text: bool) -> dict[str, Any]:
    return {"type": "string", "pattern": pattern, "max_length": max_length, "untrusted_text": untrusted_text}


def _string_list(pattern: str, max_length: int, max_items: int, *, untrusted_text: bool) -> dict[str, Any]:
    return {
        "type": "string_list",
        "pattern": pattern,
        "max_length": max_length,
        "max_items": max_items,
        "untrusted_text": untrusted_text,
    }


FIELDS: dict[str, dict[str, dict[str, Any]]] = {
    "website": {
        "site_name": _string(NAME_PATTERN, 120, untrusted_text=True),
        "page_title": _string(NAME_PATTERN, 120, untrusted_text=True),
        "activity_categories": {"type": "enum_list", "choices": ACTIVITY_CATEGORIES, "max_items": 8},
        "contact_email_domains": _string_list(DOMAIN_PATTERN, 253, 5, untrusted_text=True),
        "outbound_link_domains": _string_list(DOMAIN_PATTERN, 253, 20, untrusted_text=True),
        "has_privacy_policy": {"type": "bool"},
        "has_terms_of_service": {"type": "bool"},
        "copyright_year": {"type": "int", "minimum": 1990, "maximum": 2100},
        "phone_number_count": {"type": "int", "minimum": 0, "maximum": 50},
        "company_number_mentions": _string_list(COMPANY_NUMBER_PATTERN, 12, 5, untrusted_text=False),
    },
    "registry_document": {
        "company_name": _string(NAME_PATTERN, 160, untrusted_text=True),
        "company_number": _string(COMPANY_NUMBER_PATTERN, 12, untrusted_text=False),
        "status": {"type": "enum", "choices": REGISTRY_STATUSES},
        "incorporation_date": {"type": "date"},
        "jurisdiction": _string(JURISDICTION_PATTERN, 6, untrusted_text=False),
        "filing_type": {"type": "enum", "choices": FILING_TYPES},
        "officer_count": {"type": "int", "minimum": 0, "maximum": 1000},
    },
    "applicant_upload": {
        "legal_name": _string(NAME_PATTERN, 160, untrusted_text=True),
        "trading_name": _string(NAME_PATTERN, 160, untrusted_text=True),
        "declared_company_number": _string(COMPANY_NUMBER_PATTERN, 12, untrusted_text=False),
        "declared_jurisdiction": _string(JURISDICTION_PATTERN, 6, untrusted_text=False),
        "declared_activity_categories": {"type": "enum_list", "choices": ACTIVITY_CATEGORIES, "max_items": 8},
        "declared_owner_count": {"type": "int", "minimum": 0, "maximum": 100},
        "declared_owner_names": _string_list(NAME_PATTERN, 160, 20, untrusted_text=True),
        "website_domain": _string(DOMAIN_PATTERN, 253, untrusted_text=True),
        "tax_identifier_present": {"type": "bool"},
    },
}

# ── Sandbox ──────────────────────────────────────────────────────────────────

_DENIED_EVENTS = frozenset(
    {
        "socket.__new__",
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.gethostname",
        "socket.getnameinfo",
        "socket.sendmsg",
        "socket.sendto",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
        "os.startfile",
        "_winapi.CreateProcess",
        "ctypes.dlopen",
        "ctypes.dlsym",
        "ctypes.cdata",
        "ctypes.call_function",
        "urllib.Request",
        "http.client.connect",
        "webbrowser.open",
    }
)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    if event in _DENIED_EVENTS:
        raise PermissionError(f"extraction sandbox: {event} denied")
    if event == "open" and len(args) >= 3:
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS
        ):
            raise PermissionError("extraction sandbox: opening files for writing denied")


def _set_rlimits(
    cpu_seconds: int,
) -> bool:  # pragma: no cover - worker process only
    try:
        import resource  # noqa: PLC0415 - POSIX only
    except ImportError:
        return False
    limits = (
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_AS, 1024 * 1024 * 1024),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_NOFILE, 64),
    )
    applied = True
    try:
        import signal  # noqa: PLC0415

        # Exceeding RLIMIT_FSIZE then fails the write with EFBIG instead of killing the worker.
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    except (AttributeError, ValueError, OSError):
        applied = False
    for which, value in limits:
        try:
            resource.setrlimit(which, (value, value))
        except (OSError, ValueError):
            applied = False
    if hasattr(resource, "RLIMIT_NPROC"):
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        except (OSError, ValueError):
            applied = False
    return applied


_SECCOMP_DENY: dict[str, tuple[int, tuple[int, ...]]] = {
    # machine: (AUDIT_ARCH, (socket, socketpair, execve, execveat, ptrace, io_uring_setup/enter/register))
    "x86_64": (0xC000003E, (41, 53, 59, 322, 101, 425, 426, 427)),
    "aarch64": (0xC00000B7, (198, 199, 221, 281, 117, 425, 426, 427)),
}


def _linux_isolation() -> list[str]:  # pragma: no cover - worker process only
    active: list[str] = []
    if not sys.platform.startswith("linux"):
        return active
    import ctypes  # noqa: PLC0415 - loaded before the audit hook forbids ctypes

    libc = ctypes.CDLL(None, use_errno=True)

    clone_newuser, clone_newnet = 0x10000000, 0x40000000
    if libc.unshare(clone_newuser | clone_newnet) == 0 or libc.unshare(clone_newnet) == 0:
        active.append("netns")

    machine = platform.machine().lower()
    if machine not in _SECCOMP_DENY:
        return active
    arch, denied = _SECCOMP_DENY[machine]

    class SockFilter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8), ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]

    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(SockFilter))]

    ld_abs, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06
    allow, deny = 0x7FFF0000, 0x00050000 | 13  # SECCOMP_RET_ERRNO | EACCES
    count = len(denied)
    # Layout: [0] load arch, [1] arch check, [2] load nr, [3] x32 check,
    # [4..4+count) denied numbers, [4+count] allow, [5+count] deny.
    program = [
        (ld_abs, 0, 0, 4),
        (jeq, 0, count + 3, arch),
        (ld_abs, 0, 0, 0),
        (jge, count + 1, 0, 0x40000000),
    ]
    for index, number in enumerate(denied):
        program.append((jeq, count - index, 0, number))
    program += [(ret, 0, 0, allow), (ret, 0, 0, deny)]
    filters = (SockFilter * len(program))(*[SockFilter(*item) for item in program])
    fprog = SockFprog(len(program), filters)
    pr_set_no_new_privs, pr_set_seccomp, seccomp_mode_filter = 38, 22, 2
    if libc.prctl(pr_set_no_new_privs, 1, 0, 0, 0) == 0 and (
        libc.prctl(pr_set_seccomp, seccomp_mode_filter, ctypes.byref(fprog), 0, 0) == 0
    ):
        active.append("seccomp")
    return active


def _install_sandbox(
    *, cpu_seconds: int, audit_hook: bool = True
) -> list[str]:  # pragma: no cover - worker process only
    active: list[str] = []
    try:
        active.extend(_linux_isolation())
    except (OSError, AttributeError, ValueError):
        pass
    if _set_rlimits(cpu_seconds):
        active.append("rlimits")
    if audit_hook:
        sys.addaudithook(_audit_hook)
        active.append("audit_hook")
    return sorted(active)


# ── Normalisation ────────────────────────────────────────────────────────────

_SPACE_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """NFKC, control and format characters to spaces, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(" " if unicodedata.category(ch).startswith(("C", "Z")) else ch for ch in text)
    return _SPACE_RE.sub(" ", text).strip()


class _Collector:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.spec = FIELDS[kind]
        self.fields: dict[str, Any] = {}
        self.rejected: set[str] = set()
        self.excerpts: list[dict[str, str]] = []
        for name, spec in self.spec.items():
            if spec["type"] in ("string_list", "enum_list"):
                self.fields[name] = []
            elif spec["type"] == "bool":
                self.fields[name] = False
            else:
                self.fields[name] = None

    def excerpt(self, field: str, text: str) -> None:
        snippet = _normalise(text)[:MAX_EXCERPT_CHARS]
        if snippet and len(self.excerpts) < MAX_EXCERPTS:
            self.excerpts.append({"field": field, "text": snippet})

    def valid_string(self, field: str, value: str) -> str | None:
        spec = self.spec[field]
        value = _normalise(value)
        if value and len(value) <= spec["max_length"] and re.fullmatch(spec["pattern"], value):
            return value
        self.rejected.add(field)
        return None

    def set_string(self, field: str, raw: str, *, excerpt: str | None = None) -> None:
        value = self.valid_string(field, raw)
        self.fields[field] = value
        if value is not None:
            self.excerpt(field, excerpt if excerpt is not None else raw)

    def add_to_list(self, field: str, raw: str) -> None:
        value = self.valid_string(field, raw)
        if value is not None and value not in self.fields[field]:
            self.fields[field].append(value)

    def result(self) -> dict[str, Any]:
        for name, spec in self.spec.items():
            if spec["type"] in ("string_list", "enum_list"):
                items = sorted(set(self.fields[name]))
                if len(items) > spec["max_items"]:
                    self.rejected.add(name)
                self.fields[name] = items[: spec["max_items"]]
        excerpts = sorted({(e["field"], e["text"]) for e in self.excerpts})
        return {
            "fields": self.fields,
            "rejected_fields": sorted(self.rejected),
            "excerpts": [{"field": field, "text": text} for field, text in excerpts],
        }


def _categories(text: str) -> list[str]:
    words = set(re.findall(r"[a-z]+", _normalise(text).casefold()))
    return [category for category, keywords in ACTIVITY_KEYWORDS.items() if words.intersection(keywords)]


# ── Websites ─────────────────────────────────────────────────────────────────


class _HtmlCollector(html.parser.HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.site_name: str | None = None
        self.description: str = ""
        self.heading_depth = 0
        self.heading_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag in self._SKIP:
            self.skip_depth += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            prop = (values.get("property") or values.get("name") or "").lower()
            if prop in ("og:site_name", "application-name") and self.site_name is None:
                self.site_name = values.get("content", "")
            elif prop == "description" and not self.description:
                self.description = values.get("content", "")
        elif tag in ("h1", "h2", "h3"):
            self.heading_depth += 1
        elif tag == "a":
            self.links.append((values.get("href", ""), []))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag == "title":
            self.in_title = False
        elif tag in ("h1", "h2", "h3") and self.heading_depth:
            self.heading_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        self.text_parts.append(data)
        if self.heading_depth:
            self.heading_parts.append(data)
        if self.links:
            self.links[-1][1].append(data)


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63})")
_COPYRIGHT_RE = re.compile(r"(?:\u00a9|\(c\)|copyright)\s*(?:\d{4}\s*[-\u2013]\s*)?(\d{4})", re.IGNORECASE)
_PHONE_RE = re.compile(r"\+?\d[\d ().-]{7,20}\d")
_COMPANY_NUMBER_MENTION_RE = re.compile(
    r"\b(?:company|registration|registered)\s+(?:no|number|num)\.?\s*[:#]?\s*([A-Za-z0-9]{6,10})\b",
    re.IGNORECASE,
)


def _extract_website(text: str) -> dict[str, Any]:
    out = _Collector("website")
    parser = _HtmlCollector()
    parser.feed(text)
    parser.close()

    if parser.site_name:
        out.set_string("site_name", parser.site_name)
    title = "".join(parser.title_parts)
    if title.strip():
        out.set_string("page_title", title)

    visible = _normalise(" ".join(parser.text_parts))
    # Declared activity comes from the title, meta description and headings only,
    # not from body copy, so text buried in a page cannot reclassify the business.
    headings = " ".join(parser.heading_parts)
    out.fields["activity_categories"] = _categories(f"{title} {parser.description} {headings}")

    for match in _EMAIL_RE.finditer(visible):
        out.add_to_list("contact_email_domains", match.group(1).lower())
    for href, link_text in parser.links:
        href = href.strip()
        label = _normalise(" ".join(link_text)).casefold()
        if href.lower().startswith("mailto:"):
            address = href[7:].split("?", 1)[0]
            if "@" in address:
                out.add_to_list("contact_email_domains", address.rsplit("@", 1)[1].lower())
        elif href.lower().startswith(("http://", "https://")):
            try:
                host = urlsplit(href).hostname or ""
            except ValueError:
                host = ""
            if host:
                out.add_to_list("outbound_link_domains", host.lower())
        lowered = href.casefold()
        if "privacy" in label or "privacy" in lowered:
            out.fields["has_privacy_policy"] = True
        if "terms" in label or "terms" in lowered:
            out.fields["has_terms_of_service"] = True

    years = [int(year) for year in _COPYRIGHT_RE.findall(visible) if 1990 <= int(year) <= 2100]
    out.fields["copyright_year"] = max(years) if years else None

    phones = {re.sub(r"\D", "", match) for match in _PHONE_RE.findall(visible)}
    out.fields["phone_number_count"] = min(50, sum(1 for digits in phones if 10 <= len(digits) <= 15))

    for match in _COMPANY_NUMBER_MENTION_RE.finditer(visible):
        before = len(out.fields["company_number_mentions"])
        out.add_to_list("company_number_mentions", match.group(1).upper())
        if len(out.fields["company_number_mentions"]) > before:
            out.excerpt("company_number_mentions", match.group(0))
    return out.result()


# ── Key/value documents: registry filings and applicant uploads ──────────────

_REGISTRY_KEYS = {
    "company name": "company_name",
    "registered name": "company_name",
    "name": "company_name",
    "company number": "company_number",
    "registration number": "company_number",
    "file number": "company_number",
    "status": "status",
    "company status": "status",
    "incorporated": "incorporation_date",
    "incorporated on": "incorporation_date",
    "incorporation date": "incorporation_date",
    "date of incorporation": "incorporation_date",
    "jurisdiction": "jurisdiction",
    "filing type": "filing_type",
    "document type": "filing_type",
    "officers": "officer_count",
    "number of officers": "officer_count",
    "officer count": "officer_count",
}
_UPLOAD_KEYS = {
    "legal name": "legal_name",
    "trading name": "trading_name",
    "company number": "declared_company_number",
    "registration number": "declared_company_number",
    "jurisdiction": "declared_jurisdiction",
    "business activity": "declared_activity_categories",
    "number of owners": "declared_owner_count",
    "owner": "declared_owner_names",
    "website": "website_domain",
    "tax identifier": "tax_identifier_present",
}
_REPEATABLE = frozenset({"declared_owner_names"})
_STATUS_WORDS = {
    "active": "active",
    "live": "active",
    "dissolved": "dissolved",
    "liquidation": "liquidation",
    "in liquidation": "liquidation",
    "dormant": "dormant",
    "inactive": "inactive",
}
_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z ]{0,40}?)\s*:\s*(.*)$")


def _key(raw: str) -> str:
    return _SPACE_RE.sub(" ", raw.replace("_", " ")).strip().casefold()


class _AmbiguousJsonError(ValueError):
    pass


def _json_object(text: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise _AmbiguousJsonError(key)
            out[key] = value
        return out

    try:
        document = json.loads(text, object_pairs_hook=pairs)
    except _AmbiguousJsonError:
        raise
    except ValueError as exc:
        raise _ParseError("extraction_parse_failed") from exc
    if not isinstance(document, dict):
        raise _ParseError("extraction_parse_failed")
    return document


def _pairs_from_text(text: str, *, header_only: bool) -> list[tuple[str, str, str]]:
    """``(key, value, line)`` for ``Key: value`` lines; a filing's header ends at its first blank line."""
    pairs: list[tuple[str, str, str]] = []
    started = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if header_only and started:
                break
            continue
        started = True
        if len(line) > MAX_LINE_CHARS:
            continue
        match = _KEY_RE.match(line)
        if match:
            pairs.append((_key(match.group(1)), match.group(2), line))
    return pairs


def _pairs_from_json(text: str) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for key, value in _json_object(text).items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, bool) or not isinstance(item, str | int):
                pairs.append((_key(key), "\x00invalid", ""))
            else:
                pairs.append((_key(key), str(item), f"{key}: {item}"))
    return pairs


def _apply_pairs(out: _Collector, pairs: list[tuple[str, str, str]], aliases: dict[str, str]) -> None:
    seen: dict[str, int] = {}
    for key, _value, _line in pairs:
        field = aliases.get(key)
        if field is not None:
            seen[field] = seen.get(field, 0) + 1

    for key, value, line in pairs:
        field = aliases.get(key)
        if field is None:
            continue
        if seen[field] > 1 and field not in _REPEATABLE:
            out.rejected.add(field)  # ambiguous: the same field declared twice
            continue
        if value == "\x00invalid":
            out.rejected.add(field)
            continue
        spec = out.spec[field]
        kind = spec["type"]
        cleaned = _normalise(value)
        if kind == "string":
            if field in ("company_number", "declared_company_number"):
                cleaned = cleaned.replace(" ", "").upper()
            elif field in ("jurisdiction", "declared_jurisdiction"):
                cleaned = cleaned.lower()
            elif field == "website_domain":
                host = cleaned
                if "://" in cleaned:
                    try:
                        host = urlsplit(cleaned).hostname or ""
                    except ValueError:
                        host = ""
                cleaned = host.lower().removeprefix("www.") if host else ""
            out.set_string(field, cleaned, excerpt=line)
        elif kind == "string_list":
            before = len(out.fields[field])
            out.add_to_list(field, cleaned)
            if len(out.fields[field]) > before:
                out.excerpt(field, line)
        elif kind == "enum":
            if field == "status":
                choice = _STATUS_WORDS.get(cleaned.casefold())
            else:
                token = re.sub(r"[^a-z]+", "_", cleaned.casefold()).strip("_")
                choice = token if token in spec["choices"] else ("other" if token else None)
            if choice is None:
                out.rejected.add(field)
            else:
                out.fields[field] = choice
                # Only a recognised value is quoted, so the excerpt of a constrained
                # field never carries free text ("other" is not quoted).
                if choice != "other":
                    out.excerpt(field, line)
        elif kind == "enum_list":
            out.fields[field] = sorted(set(out.fields[field]) | set(_categories(cleaned)))
        elif kind == "int":
            if re.fullmatch(r"\d{1,6}", cleaned) and spec["minimum"] <= int(cleaned) <= spec["maximum"]:
                out.fields[field] = int(cleaned)
                out.excerpt(field, line)
            else:
                out.rejected.add(field)
        elif kind == "date":
            try:
                if not re.fullmatch(DATE_PATTERN, cleaned):
                    raise ValueError(cleaned)
                out.fields[field] = datetime.date.fromisoformat(cleaned).isoformat()
                out.excerpt(field, line)
            except ValueError:
                out.rejected.add(field)
        elif kind == "bool":
            out.fields[field] = bool(cleaned)


def _extract_key_value(kind: str, text: str, content_type: str) -> dict[str, Any]:
    out = _Collector(kind)
    aliases = _REGISTRY_KEYS if kind == "registry_document" else _UPLOAD_KEYS
    try:
        if content_type == "application/json":
            pairs = _pairs_from_json(text)
        else:
            pairs = _pairs_from_text(text, header_only=kind == "registry_document")
    except _AmbiguousJsonError as exc:
        field = aliases.get(_key(str(exc)))
        pairs = []
        if field is not None:
            out.rejected.add(field)
        else:
            raise _ParseError("extraction_parse_failed") from exc
    _apply_pairs(out, pairs, aliases)
    return out.result()


# ── Protocol ─────────────────────────────────────────────────────────────────


class _ParseError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _handle(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("v") != PROTOCOL_VERSION:
        raise _ParseError("extraction_request_invalid")
    kind = request.get("kind")
    content_type = request.get("content_type")
    if kind not in SOURCE_KINDS or content_type not in CONTENT_TYPES[kind]:
        raise _ParseError("extraction_unsupported_content_type")
    try:
        content = base64.b64decode(request.get("content_b64", ""), validate=True)
        text = content.decode("utf-8")
    except (binascii.Error, ValueError) as exc:
        raise _ParseError("extraction_decode_failed") from exc
    if kind == "website":
        return _extract_website(text)
    return _extract_key_value(kind, text, content_type)


def _probe(*, audit_hook: bool) -> dict[str, Any]:  # pragma: no cover - worker process only
    """Report which isolation layers are active and whether escapes are refused."""
    isolation = _install_sandbox(cpu_seconds=10, audit_hook=audit_hook)
    checks: dict[str, str] = {}

    def attempt(name: str, action: Any) -> None:
        try:
            action()
        except (OSError, RuntimeError, ValueError) as exc:
            checks[name] = f"denied:{type(exc).__name__}"
        else:
            checks[name] = "allowed"

    import socket  # noqa: PLC0415

    def connect() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(("192.0.2.1", 9))

    def raw_socket() -> None:
        import _socket  # noqa: PLC0415

        _socket.socket(socket.AF_INET, socket.SOCK_DGRAM).close()

    def resolve() -> None:
        socket.getaddrinfo("example.com", 443)

    def spawn() -> None:
        import subprocess  # noqa: PLC0415

        subprocess.run([sys.executable, "-c", "pass"], check=True, timeout=5)  # noqa: S603

    def write_file() -> None:
        with open("sandbox-probe.tmp", "w", encoding="utf-8") as handle:
            handle.write("x")

    if audit_hook or {"netns", "seccomp"} & set(isolation):
        attempt("socket_connect", connect)
        attempt("raw_socket", raw_socket)
        attempt("name_resolution", resolve)
        attempt("process_spawn", spawn)
    else:
        # Never make a real network attempt from a probe that has nothing to stop it.
        for name in ("socket_connect", "raw_socket", "name_resolution", "process_spawn"):
            checks[name] = "skipped:no_isolation"
    attempt("file_write", write_file)
    return {"v": PROTOCOL_VERSION, "ok": True, "isolation": isolation, "checks": checks}


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    sys.stdout.flush()


def main(
    argv: list[str],
) -> int:  # pragma: no cover - worker process only
    args = argv[1:]
    if args[:1] == ["--probe"]:
        _emit(_probe(audit_hook="--without-audit-hook" not in args))
        return 0
    cpu_seconds = 30
    if "--cpu-seconds" in args:
        index = args.index("--cpu-seconds") + 1
        if index < len(args) and args[index].isdigit():
            cpu_seconds = max(1, min(300, int(args[index])))
    isolation = _install_sandbox(cpu_seconds=cpu_seconds)
    try:
        if "--require-os-isolation" in args and "seccomp" not in isolation:
            raise _ParseError("extraction_isolation_unavailable")
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise _ParseError("extraction_input_too_large")
        try:
            request = json.loads(raw)
        except ValueError as exc:
            raise _ParseError("extraction_request_invalid") from exc
        result = _handle(request)
    except _ParseError as exc:
        _emit({"v": PROTOCOL_VERSION, "ok": False, "reason": exc.reason, "isolation": isolation})
        return 3
    except RecursionError:
        _emit({"v": PROTOCOL_VERSION, "ok": False, "reason": "extraction_parse_failed", "isolation": isolation})
        return 3
    _emit({"v": PROTOCOL_VERSION, "ok": True, "isolation": isolation, **result})
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
