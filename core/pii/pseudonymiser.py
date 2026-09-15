# SPDX-License-Identifier: Apache-2.0
"""Reversible pseudonymisation of personal data before model calls (PRD F-5).

When the per-tenant flag ``pseudonymisation.pre_model`` is on, names, dates of
birth, addresses and identifiers are replaced with tokens such as
``[[PERSON_1:3fa9c2]]`` before any text reaches a model, on both model paths:
LangGraph agents (``core.langgraph``) and ``core.llm.router.LLMRouter``. The
system prompt is included. Tool calls are restored at the tool boundary, so
connectors receive the true values while prompts never contain them.

Tokens are stable for a case: a value maps to the same token for the whole
case, across model turns, tool results, a human-in-the-loop pause and a
process restart. The map is persisted per (tenant, case) in
``case_pseudonym_maps``, encrypted with the tenant's key; plaintext values
never leave process memory unencrypted.

The six hex characters after the colon are a random tag per case. Text from
outside the case (a web page, a filing) cannot forge a token that restores to
one of the case's values without knowing it.

Failure handling is closed:

* the map cannot be read or written → the model call is not made;
* a tool argument carries a token that is unknown for the case, belongs to
  another case, or is malformed → the tool call is refused with
  ``pseudonym_restore_failed``; it is never sent with the token or partially
  restored.

See ``docs/security/pseudonymisation.md`` for what is detected and the limits.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from langchain_core.messages import AIMessage, BaseMessage
from prometheus_client import Counter

from core.pii.international_recognizers import ENTITY_TYPES as INTERNATIONAL_ENTITY_TYPES
from core.pii.international_recognizers import IdentifierMatch, find_identifiers, resolve_overlaps

logger = structlog.get_logger()

FLAG_KEY = "pseudonymisation.pre_model"
REFUSAL_CODE = "E1012"

PERSON = "PERSON"
DATE_OF_BIRTH = "DATE_OF_BIRTH"
ADDRESS = "ADDRESS"
EMAIL_ADDRESS = "EMAIL_ADDRESS"
PHONE_NUMBER = "PHONE_NUMBER"
AADHAAR = "AADHAAR"
PAN = "PAN"
GSTIN = "GSTIN"
UPI = "UPI"
PASSPORT_NUMBER = "PASSPORT_NUMBER"
TAX_ID = "TAX_ID"
BANK_ACCOUNT = "BANK_ACCOUNT"

ENTITY_TYPES: frozenset[str] = INTERNATIONAL_ENTITY_TYPES | {
    PERSON, DATE_OF_BIRTH, ADDRESS, EMAIL_ADDRESS, PHONE_NUMBER, AADHAAR, PAN, GSTIN, UPI, PASSPORT_NUMBER, TAX_ID,
    BANK_ACCOUNT,
}  # fmt: skip

MODEL_GUIDANCE = (
    "<pseudonymised_data>\n"
    "Personal data in this conversation is replaced by placeholders written as [[TYPE_N:xxxxxx]], "
    "for example [[PERSON_1:xxxxxx]]. Copy a placeholder exactly as written wherever you need that value, "
    "including in tool arguments; the real value is substituted when the tool runs. "
    "Never guess, alter or invent placeholders.\n"
    "</pseudonymised_data>"
)

_MAP_FORMAT = 1
_MAX_PASSES = 3
_MEMO_LIMIT = 1024
_CASE_ID_RE = re.compile(r"[A-Za-z0-9._:@-]{1,200}")
_TAG_RE = re.compile(r"[0-9a-f]{6}")
_TOKEN_RE = re.compile(r"\[\[([A-Z][A-Z0-9_]*?)_([1-9]\d*):([0-9a-f]{6})\]\]")
# Anything a model could have meant as a token, including damaged ones.
_TOKEN_LIKE_RE = re.compile(r"\[\[\s*[A-Za-z][A-Za-z0-9_]*_\d+", re.IGNORECASE)

_pseudonymised_total = Counter(
    "agenticorg_pii_pseudonymised_total",
    "Distinct values given a pseudonym before a model call, by entity type",
    ["entity_type"],
)
_restore_refused_total = Counter(
    "agenticorg_pii_pseudonym_restore_refused_total",
    "Tool calls refused because a pseudonym could not be restored, by reason",
    ["reason"],
)
_store_failures_total = Counter(
    "agenticorg_pii_pseudonym_store_failures_total",
    "Pseudonym map reads or writes that failed (the model call was not made), by reason",
    ["reason"],
)


class PseudonymisationError(RuntimeError):
    """Pseudonymisation could not be applied safely; ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


class PseudonymRestoreError(PseudonymisationError):
    """A tool argument carries a pseudonym that cannot be restored."""


# ── Map ────────────────────────────────────────────────────────────────────


@dataclass
class PseudonymMap:
    """Token ↔ value mapping for one case. Append-only."""

    tag: str
    by_token: dict[str, str] = field(default_factory=dict)
    by_value: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    _pattern: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def new(cls) -> PseudonymMap:
        return cls(tag=secrets.token_hex(3))

    def __len__(self) -> int:
        return len(self.by_token)

    def token_for(self, value: str, entity_type: str) -> str:
        existing = self.by_value.get(value)
        if existing is not None:
            return existing
        entity = entity_type if re.fullmatch(r"[A-Z][A-Z0-9_]*", entity_type) else "PII"
        self.counters[entity] = self.counters.get(entity, 0) + 1
        token = f"[[{entity}_{self.counters[entity]}:{self.tag}]]"
        self.by_token[token] = value
        self.by_value[value] = token
        self._pattern = None
        return token

    def known_values_pattern(self) -> re.Pattern[str] | None:
        """Existing tokens, or any known value as a whole word (longest first)."""
        if not self.by_value:
            return None
        if self._pattern is None:
            values = sorted(self.by_value, key=len, reverse=True)
            alternatives = "|".join(re.escape(value) for value in values)
            self._pattern = re.compile(rf"({_TOKEN_RE.pattern})|(?<!\w)(?:{alternatives})(?!\w)")
        return self._pattern

    def to_json(self) -> str:
        entries = [[token, value] for token, value in self.by_token.items()]
        return json.dumps({"format": _MAP_FORMAT, "tag": self.tag, "entries": entries}, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> PseudonymMap:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise PseudonymisationError("map_unreadable", "not JSON") from exc
        if not isinstance(data, dict) or data.get("format") != _MAP_FORMAT:
            raise PseudonymisationError("map_unreadable", "unknown format")
        tag, entries = data.get("tag"), data.get("entries")
        if not isinstance(tag, str) or not _TAG_RE.fullmatch(tag) or not isinstance(entries, list):
            raise PseudonymisationError("map_unreadable", "malformed header")
        pmap = cls(tag=tag)
        for entry in entries:
            if not (isinstance(entry, list) and len(entry) == 2 and all(isinstance(part, str) for part in entry)):
                raise PseudonymisationError("map_unreadable", "malformed entry")
            token, value = entry
            match = _TOKEN_RE.fullmatch(token)
            if match is None or match[3] != tag or token in pmap.by_token or value in pmap.by_value:
                raise PseudonymisationError("map_unreadable", "inconsistent entry")
            pmap.by_token[token] = value
            pmap.by_value[value] = token
            pmap.counters[match[1]] = max(pmap.counters.get(match[1], 0), int(match[2]))
        return pmap


# ── Detection ──────────────────────────────────────────────────────────────

_CONTACT_AND_INDIA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (EMAIL_ADDRESS, re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w-])")),
    (AADHAAR, re.compile(r"(?<![\w-])\d{4}[ -]\d{4}[ -]\d{4}(?![\w-])")),
    (GSTIN, re.compile(r"(?<![\w-])\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9](?![\w-])")),
    (PAN, re.compile(r"(?<![\w-])[A-Z]{5}\d{4}[A-Z](?![\w-])")),
    # A handle at a bank name with no dot after the @, so e-mail addresses are never UPI ids.
    (UPI, re.compile(r"(?<![\w.-])[A-Za-z0-9._-]{2,}@[A-Za-z][A-Za-z0-9]{2,}(?![\w.@-])")),
    (PHONE_NUMBER, re.compile(r"(?<![\w+])(?:\+91[ -]?)?[6-9]\d{9}(?!\w)")),
)

_DOB_RE = re.compile(
    r"(?i)(?<![a-z])(?:date\s+of\s+birth|d\.?o\.?b\.?|born(?:\s+on)?|birth\s*date)\s*[:=\-]?\s*\"?"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[a-z]{3,9}\.?\s+\d{4}|[a-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"
)

# Structured keys (lower case, separators removed) whose string values are
# pseudonymised wherever they appear in the case.
_KEY_ENTITIES: dict[str, str] = {
    **dict.fromkeys(
        (
            "fullname", "firstname", "lastname", "givenname", "familyname", "surname", "middlename", "maidenname",
            "personname", "contactname", "applicantname", "customername", "employeename", "candidatename",
            "ownername", "directorname", "beneficialownername", "accountholdername", "cardholdername",
            "beneficiaryname", "patientname", "guardianname", "spousename", "nomineename",
        ),
        PERSON,
    ),
    **dict.fromkeys(("dob", "dateofbirth", "birthdate", "birthday"), DATE_OF_BIRTH),
    **dict.fromkeys(
        (
            "address", "streetaddress", "residentialaddress", "homeaddress", "postaladdress", "mailingaddress",
            "registeredaddress", "addressline1", "addressline2", "street", "postcode", "postalcode", "zip",
            "zipcode",
        ),
        ADDRESS,
    ),
    **dict.fromkeys(("email", "emailaddress"), EMAIL_ADDRESS),
    **dict.fromkeys(("phone", "phonenumber", "mobile", "mobilenumber", "telephone"), PHONE_NUMBER),
    **dict.fromkeys(("ssn", "socialsecuritynumber"), "US_SSN"),
    "itin": "US_ITIN",
    **dict.fromkeys(("ein", "employeridentificationnumber"), "US_EIN"),
    **dict.fromkeys(("nino", "nationalinsurancenumber"), "UK_NINO"),
    **dict.fromkeys(("companynumber", "companieshousenumber", "crn"), "UK_COMPANY_NUMBER"),
    **dict.fromkeys(("vat", "vatnumber", "vatid"), "EU_VAT"),
    "iban": "IBAN_CODE",
    **dict.fromkeys(("aadhaar", "aadhaarnumber"), AADHAAR),
    **dict.fromkeys(("pan", "pannumber"), PAN),
    "gstin": GSTIN,
    **dict.fromkeys(("upi", "upiid", "vpa"), UPI),
    **dict.fromkeys(("passport", "passportnumber"), PASSPORT_NUMBER),
    **dict.fromkeys(("taxid", "tin", "taxidentificationnumber"), TAX_ID),
    **dict.fromkeys(("accountnumber", "bankaccount", "bankaccountnumber"), BANK_ACCOUNT),
}  # fmt: skip

# Inside an address object these parts are not identifying on their own.
_ADDRESS_PARTS_KEPT = frozenset({"country", "countrycode", "state", "region", "province", "county"})


def _normalise_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower()) if isinstance(key, str) else ""


def _worth_a_pseudonym(value: str) -> bool:
    """Skip values too short to identify anyone whose pseudonym would also hide ordinary numbers elsewhere.

    A known value is replaced wherever it appears in the case, so a five-digit
    ZIP code would otherwise also replace an amount of 12345.
    """
    stripped = value.strip()
    if len(stripped) < 2 or not any(char.isalnum() for char in stripped):
        return False
    return not (stripped.isdigit() and len(stripped) < 6)


def structured_values(value: Any) -> list[tuple[str, str]]:
    """``(value, entity_type)`` for string values held under personal-data keys, in document order."""
    found: list[tuple[str, str]] = []

    def walk(node: Any, entity: str | None) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                normalised = _normalise_key(key)
                if entity == ADDRESS:
                    walk(child, None if normalised in _ADDRESS_PARTS_KEPT else ADDRESS)
                else:
                    walk(child, _KEY_ENTITIES.get(normalised))
        elif isinstance(node, list | tuple):
            for child in node:
                walk(child, entity)
        elif isinstance(node, str) and entity is not None and _worth_a_pseudonym(node):
            found.append((node, entity))

    walk(value, None)
    return found


def _people(text: str) -> list[IdentifierMatch]:
    """Multi-word person names from the NLP analyser, when it is installed."""
    from core.pii.redactor import PIIRedactor  # noqa: PLC0415 - keeps the NLP stack off import paths that never use it

    spans = PIIRedactor().find_entities(text, ["PERSON"])
    return [
        IdentifierMatch(start, end, PERSON, text[start:end])
        for start, end, _ in spans
        if " " in text[start:end].strip()
    ]


def _detect(text: str) -> list[IdentifierMatch]:
    found: list[IdentifierMatch] = list(find_identifiers(text))
    for entity, pattern in _CONTACT_AND_INDIA_PATTERNS:
        found.extend(IdentifierMatch(m.start(), m.end(), entity, m[0]) for m in pattern.finditer(text))
    found.extend(IdentifierMatch(m.start(1), m.end(1), DATE_OF_BIRTH, m[1]) for m in _DOB_RE.finditer(text))
    found.extend(_people(text))
    token_spans = [(m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]
    if token_spans:
        found = [f for f in found if all(f.end <= start or f.start >= end for start, end in token_spans)]
    return resolve_overlaps(found)


def _apply(text: str, pmap: PseudonymMap | None) -> tuple[str, list[tuple[str, str]]]:
    """Pseudonymise ``text`` with ``pmap``; also return detected values the map does not hold yet."""
    pattern = pmap.known_values_pattern() if pmap is not None else None
    if pattern is not None and pmap is not None:
        by_value = pmap.by_value
        text = pattern.sub(lambda m: m[0] if m[1] else by_value[m[0]], text)
    missing: list[tuple[str, str]] = []
    pieces: list[str] = []
    cursor = 0
    for finding in _detect(text):
        token = pmap.by_value.get(finding.text) if pmap is not None else None
        if token is None:
            missing.append((finding.text, finding.entity_type))
            continue
        pieces += [text[cursor : finding.start], token]
        cursor = finding.end
    if missing:
        return text, missing
    return "".join(pieces) + text[cursor:], []


# ── Case ids and the flag ──────────────────────────────────────────────────


def resolve_case_id(task_input: Any, fallback: str) -> str:
    """The case a run belongs to: an explicit ``case_id`` in the task, else ``fallback`` (a run or thread id).

    Raises ``PseudonymisationError("case_id_invalid")`` for a malformed id
    rather than silently starting a separate map.
    """
    candidate: Any = None
    if isinstance(task_input, Mapping):
        for container in (task_input, task_input.get("inputs"), task_input.get("context")):
            if isinstance(container, Mapping) and container.get("case_id") not in (None, ""):
                candidate = container["case_id"]
                break
    case_id = str(candidate) if candidate is not None else fallback
    if not _CASE_ID_RE.fullmatch(case_id):
        raise PseudonymisationError("case_id_invalid", "case_id must be 1-200 characters of [A-Za-z0-9._:@-]")
    return case_id


async def pseudonymisation_enabled(tenant_id: str | uuid.UUID | None) -> bool:
    """Whether ``pseudonymisation.pre_model`` is on for this tenant (off by default and without a tenant)."""
    if not tenant_id:
        return False
    try:
        tenant_uuid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    except ValueError:
        return False
    from core.feature_flags import is_enabled  # noqa: PLC0415 - avoids a database import for callers without a tenant

    return await is_enabled(FLAG_KEY, tenant_id=tenant_uuid)


def with_model_guidance(system_prompt: str) -> str:
    if "<pseudonymised_data>" in (system_prompt or ""):
        return system_prompt
    return f"{(system_prompt or '').rstrip()}\n\n{MODEL_GUIDANCE}".strip()


# ── Storage ────────────────────────────────────────────────────────────────


class PseudonymMapStore(Protocol):
    async def load(self, tenant_id: str, case_id: str) -> PseudonymMap | None: ...

    async def add(self, tenant_id: str, case_id: str, additions: Sequence[tuple[str, str]]) -> PseudonymMap:
        """Add ``(value, entity_type)`` pairs under a lock and return the resulting map."""
        ...


def _tenant_uuid(tenant_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(tenant_id))
    except ValueError as exc:
        raise PseudonymisationError("tenant_invalid") from exc


async def _decode_stored(stored: Any) -> PseudonymMap | None:
    if stored in (None, {}):
        return None
    ciphertext = stored.get("_encrypted") if isinstance(stored, dict) else None
    if not isinstance(ciphertext, str) or not ciphertext:
        raise PseudonymisationError("map_unreadable", "no ciphertext")
    from core.crypto.tenant_secrets import decrypt_for_tenant  # noqa: PLC0415

    try:
        # KMS-backed decrypt is synchronous; keep it off the event loop.
        plaintext = await asyncio.to_thread(decrypt_for_tenant, ciphertext)
    # enterprise-gate: broad-except-ok reason=map-decrypt-failure-raises-and-refuses-the-model-call
    except Exception as exc:
        raise PseudonymisationError("map_unreadable", "decryption failed") from exc
    return PseudonymMap.from_json(plaintext)


class DatabasePseudonymMapStore:
    """``case_pseudonym_maps`` rows, encrypted with ``encrypt_for_tenant`` under row-level security."""

    async def load(self, tenant_id: str, case_id: str) -> PseudonymMap | None:
        from sqlalchemy import select  # noqa: PLC0415
        from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415

        from core.database import get_tenant_session  # noqa: PLC0415
        from core.models.case_pseudonym_map import CasePseudonymMap  # noqa: PLC0415

        tid = _tenant_uuid(tenant_id)
        try:
            async with get_tenant_session(tid) as session:
                stored = (
                    await session.execute(
                        select(CasePseudonymMap.mapping_encrypted).where(
                            CasePseudonymMap.tenant_id == tid, CasePseudonymMap.case_id == case_id
                        )
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise PseudonymisationError("map_store_unavailable") from exc
        return await _decode_stored(stored)

    async def add(self, tenant_id: str, case_id: str, additions: Sequence[tuple[str, str]]) -> PseudonymMap:
        from sqlalchemy import select  # noqa: PLC0415
        from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415
        from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415

        from core.crypto.tenant_secrets import encrypt_for_tenant  # noqa: PLC0415
        from core.database import get_tenant_session  # noqa: PLC0415
        from core.models.case_pseudonym_map import CasePseudonymMap  # noqa: PLC0415

        tid = _tenant_uuid(tenant_id)
        try:
            async with get_tenant_session(tid) as session:
                await session.execute(
                    insert(CasePseudonymMap)
                    .values(id=uuid.uuid4(), tenant_id=tid, case_id=case_id, mapping_encrypted={}, entry_count=0)
                    .on_conflict_do_nothing(index_elements=["tenant_id", "case_id"])
                )
                # The row lock serialises concurrent writers on the same case, so
                # a token is never assigned to two different values.
                row = (
                    await session.execute(
                        select(CasePseudonymMap)
                        .where(CasePseudonymMap.tenant_id == tid, CasePseudonymMap.case_id == case_id)
                        .with_for_update()
                    )
                ).scalar_one()
                pmap = await _decode_stored(row.mapping_encrypted) or PseudonymMap.new()
                for value, entity in additions:
                    pmap.token_for(value, entity)
                try:
                    ciphertext = await encrypt_for_tenant(pmap.to_json(), tid)
                # enterprise-gate: broad-except-ok reason=map-encrypt-failure-raises-and-refuses-the-model-call
                except Exception as exc:
                    raise PseudonymisationError("map_store_failed", "encryption failed") from exc
                row.mapping_encrypted = {"_encrypted": ciphertext}
                row.entry_count = len(pmap)
        except SQLAlchemyError as exc:
            raise PseudonymisationError("map_store_failed") from exc
        return pmap


# ── Session ────────────────────────────────────────────────────────────────


class PseudonymSession:
    """Pseudonymises and restores values for one case of one tenant."""

    def __init__(self, tenant_id: str, case_id: str, store: PseudonymMapStore) -> None:
        if not _CASE_ID_RE.fullmatch(case_id or ""):
            raise PseudonymisationError("case_id_invalid")
        self.tenant_id = str(tenant_id)
        self.case_id = case_id
        self._store = store
        self._map: PseudonymMap | None = None
        self._lock = asyncio.Lock()
        # Each model turn re-sends the whole conversation; remember texts
        # already pseudonymised against the current map size.
        self._memo: dict[str, tuple[int, str]] = {}

    async def load(self) -> None:
        try:
            self._map = await self._store.load(self.tenant_id, self.case_id)
        except PseudonymisationError as exc:
            _store_failures_total.labels(reason=exc.reason).inc()
            raise

    # Pseudonymise --------------------------------------------------------

    async def pseudonymise_text(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        size = len(self._map) if self._map is not None else 0
        remembered = self._memo.get(text)
        if remembered is not None and remembered[0] == size:
            return remembered[1]
        for _ in range(_MAX_PASSES):
            masked, missing = _apply(text, self._map)
            if not missing:
                if len(self._memo) >= _MEMO_LIMIT:
                    self._memo.clear()
                self._memo[text] = (len(self._map) if self._map is not None else 0, masked)
                return masked
            await self._add(missing)
        raise PseudonymisationError("pseudonymisation_unstable")

    async def register_structured(self, value: Any) -> None:
        """Give values under personal-data keys (``full_name``, ``dob``, ``address``, ``ssn`` ...) a pseudonym."""
        known = self._map.by_value if self._map is not None else {}
        additions = [(text, entity) for text, entity in structured_values(value) if text not in known]
        if additions:
            await self._add(additions)

    async def pseudonymise_value(self, value: Any) -> Any:
        """Pseudonymise every string in a JSON-like value; keys are left as they are."""
        await self.register_structured(value)
        return await self._pseudonymise_leaves(value)

    async def _pseudonymise_leaves(self, value: Any) -> Any:
        if isinstance(value, str):
            return await self.pseudonymise_text(value)
        if isinstance(value, Mapping):
            return {key: await self._pseudonymise_leaves(child) for key, child in value.items()}
        if isinstance(value, list | tuple):
            return [await self._pseudonymise_leaves(child) for child in value]
        return value

    async def pseudonymise_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """LangChain messages as they must be sent to a model: every text and tool-call argument pseudonymised."""
        result: list[BaseMessage] = []
        for message in messages:
            update: dict[str, Any] = {}
            content = await self._pseudonymise_content(message.content)
            if content != message.content:
                update["content"] = content
            if isinstance(message, AIMessage) and message.tool_calls:
                calls = [{**call, "args": await self._pseudonymise_leaves(call["args"])} for call in message.tool_calls]
                if calls != message.tool_calls:
                    update["tool_calls"] = calls
            result.append(message.model_copy(update=update) if update else message)
        return result

    async def _pseudonymise_content(self, content: Any) -> Any:
        if isinstance(content, str):
            return await self.pseudonymise_text(content)
        if isinstance(content, list):
            blocks: list[Any] = []
            for block in content:
                if isinstance(block, str):
                    blocks.append(await self.pseudonymise_text(block))
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    blocks.append({**block, "text": await self.pseudonymise_text(block["text"])})
                else:
                    blocks.append(block)
            return blocks
        return content

    async def pseudonymise_router_messages(self, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """``LLMRouter`` role/content messages with every content string pseudonymised."""
        return [
            {**message, "content": await self._pseudonymise_content(message.get("content"))} for message in messages
        ]

    async def _add(self, additions: Iterable[tuple[str, str]]) -> None:
        unique = list(dict.fromkeys(additions))
        async with self._lock:
            before = set(self._map.by_token) if self._map is not None else set()
            try:
                self._map = await self._store.add(self.tenant_id, self.case_id, unique)
            except PseudonymisationError as exc:
                _store_failures_total.labels(reason=exc.reason).inc()
                raise
        for token in self._map.by_token.keys() - before:
            match = _TOKEN_RE.fullmatch(token)
            entity = match[1] if match is not None and match[1] in ENTITY_TYPES else "other"
            _pseudonymised_total.labels(entity_type=entity).inc()
        logger.info("pii_pseudonyms_added", count=len(self._map) - len(before), case_entries=len(self._map))

    # Restore --------------------------------------------------------------

    def restore_text(self, text: str) -> str:
        """Replace known tokens with their values; anything else is left as it is. For output shown to people."""
        if not isinstance(text, str) or self._map is None or "[[" not in text:
            return text
        by_token = self._map.by_token
        return _TOKEN_RE.sub(lambda m: by_token.get(m[0], m[0]), text)

    def restore_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore_text(value)
        if isinstance(value, Mapping):
            return {key: self.restore_value(child) for key, child in value.items()}
        if isinstance(value, list | tuple):
            return [self.restore_value(child) for child in value]
        return value

    async def restore_arguments(self, value: Any) -> Any:
        """Tool arguments with every token restored.

        Raises ``PseudonymRestoreError`` when any token-like text cannot be
        restored exactly, so a tool never receives a token or a partial value.
        """
        try:
            try:
                return self._restore_strict(value)
            except PseudonymRestoreError as exc:
                if exc.reason != "unknown_pseudonym":
                    raise
                await self.load()  # another worker on this case may have added it
                return self._restore_strict(value)
        except PseudonymisationError as exc:
            _restore_refused_total.labels(reason=exc.reason).inc()
            logger.warning("pii_pseudonym_restore_refused", reason=exc.reason)
            if isinstance(exc, PseudonymRestoreError):
                raise
            raise PseudonymRestoreError(exc.reason) from exc

    def _restore_strict(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._restore_text_strict(value)
        if isinstance(value, Mapping):
            restored: dict[Any, Any] = {}
            for key, child in value.items():
                if isinstance(key, str) and _TOKEN_LIKE_RE.search(key):
                    raise PseudonymRestoreError("pseudonym_in_argument_name")
                restored[key] = self._restore_strict(child)
            return restored
        if isinstance(value, list | tuple):
            return [self._restore_strict(child) for child in value]
        return value

    def _restore_text_strict(self, text: str) -> str:
        if not _TOKEN_LIKE_RE.search(text):
            return text
        by_token = self._map.by_token if self._map is not None else {}
        pieces: list[str] = []
        cursor = 0
        for match in _TOKEN_RE.finditer(text):
            value = by_token.get(match[0])
            if value is None:
                raise PseudonymRestoreError("unknown_pseudonym")
            pieces += [text[cursor : match.start()], value]
            cursor = match.end()
        pieces.append(text[cursor:])
        # Anything token-like left between exact tokens is damaged.
        if any(_TOKEN_LIKE_RE.search(piece) for piece in pieces[0::2]):
            raise PseudonymRestoreError("malformed_pseudonym")
        return "".join(pieces)


async def open_session(
    tenant_id: str | None,
    case_id: str,
    *,
    store: PseudonymMapStore | None = None,
) -> PseudonymSession:
    """Open and load the session for a case. Callers check ``pseudonymisation_enabled`` first.

    A resumed case whose state already holds tokens opens its session whatever
    the flag says now, because its tool calls cannot be restored otherwise.
    """
    if not tenant_id:
        raise PseudonymisationError("tenant_invalid")
    session = PseudonymSession(str(tenant_id), case_id, store or DatabasePseudonymMapStore())
    await session.load()
    return session


def refusal(exc: PseudonymisationError) -> dict[str, Any]:
    """Tool result for a call refused because its arguments could not be restored."""
    return {"error": {"code": REFUSAL_CODE, "message": f"pseudonym_restore_failed: {exc.reason}"}}
