# SPDX-License-Identifier: Apache-2.0
"""The closed sets of spend intelligence and the normalisers every input passes through.

Every closed set here is mirrored by a CHECK constraint in the migrations.
Units come in two kinds: a record unit is what one usage record counts
(``input_token``, ``ocr_page``), a card unit is what a rate card prices
(``1m_input_tokens``, ``ocr_page``). ``PRICE_PATHS`` names, for a record unit,
every card unit and price field that can price it; the pricing engine ranks
the candidates of all paths together (``core/spend/pricing.py``).

The normalisers refuse with ``SpendError`` (422) rather than repair: a code,
a SKU, a currency, a number or a free-text field that does not fit is
refused whole, never cut or guessed.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, DecimalException
from typing import Any

from core.spend.errors import SpendError

USAGE_TYPES = ("llm_tokens", "embedding_tokens", "ocr_pages", "speech_minutes", "tool_calls", "storage", "gpu_hours")
RECORD_UNITS: dict[str, tuple[str, ...]] = {
    "llm_tokens": ("input_token", "output_token", "cached_input_token", "token"),
    "embedding_tokens": ("embedding_token",),
    "ocr_pages": ("ocr_page",),
    "speech_minutes": ("audio_minute",),
    "tool_calls": ("call",),
    "storage": ("gb_day",),
    "gpu_hours": ("gpu_node_hour",),
}
CARD_UNITS: dict[str, tuple[str, ...]] = {
    "llm_tokens": ("1m_input_tokens", "1m_output_tokens", "1m_cached_input_tokens", "1m_tokens"),
    "embedding_tokens": ("1m_embedding_tokens",),
    "ocr_pages": ("ocr_page",),
    "speech_minutes": ("audio_minute",),
    "tool_calls": ("call",),
    "storage": ("gb_month", "gb_day"),
    "gpu_hours": ("gpu_node_hour",),
}
# Record unit -> pricing paths (card unit, price field, divisor), indexed from 0.
# Candidates of all paths are ranked together (core/spend/pricing.py).
# Divisor "month" = days in the billing month of the record's billing date.
PRICE_PATHS: dict[str, tuple[tuple[str, str, int | str], ...]] = {
    "input_token": (("1m_input_tokens", "unit_price", 1_000_000),),
    "output_token": (("1m_output_tokens", "unit_price", 1_000_000),),
    "cached_input_token": (
        ("1m_cached_input_tokens", "unit_price", 1_000_000),
        ("1m_input_tokens", "cached_unit_price", 1_000_000),
        ("1m_input_tokens", "unit_price", 1_000_000),  # path 2 = "no discount"
    ),
    "token": (("1m_tokens", "unit_price", 1_000_000), ("blend", "", 1_000_000)),
    "embedding_token": (("1m_embedding_tokens", "unit_price", 1_000_000),),
    "ocr_page": (("ocr_page", "unit_price", 1),),
    "audio_minute": (("audio_minute", "unit_price", 1),),
    "call": (("call", "unit_price", 1),),
    "gb_day": (("gb_day", "unit_price", 1), ("gb_month", "unit_price", "month")),
    "gpu_node_hour": (("gpu_node_hour", "unit_price", 1),),
}
NO_DISCOUNT_PATHS: dict[str, frozenset[int]] = {"cached_input_token": frozenset({2})}
# The unit a commitment, an invoice line and a reconciliation item use for a record unit,
# whichever card priced the record (cached input stays cached input even when the input
# card's cached price priced it).
CANONICAL_UNIT = {
    "input_token": "1m_input_tokens",
    "cached_input_token": "1m_cached_input_tokens",
    "output_token": "1m_output_tokens",
    "token": "1m_tokens",
    "embedding_token": "1m_embedding_tokens",
    "ocr_page": "ocr_page",
    "audio_minute": "audio_minute",
    "call": "call",
    "gb_day": "gb_day",
    "gpu_node_hour": "gpu_node_hour",
}
CANONICAL_DIVISOR = {
    "1m_input_tokens": 1_000_000,
    "1m_output_tokens": 1_000_000,
    "1m_cached_input_tokens": 1_000_000,
    "1m_tokens": 1_000_000,
    "1m_embedding_tokens": 1_000_000,
    "ocr_page": 1,
    "audio_minute": 1,
    "call": 1,
    "gb_day": 1,
    "gpu_node_hour": 1,
}
CARD_SOURCES = ("list", "contract")
CARD_STATUSES = ("active", "retired")
TIER_MODES = ("graduated", "all_units")
MAX_TIERS = 20
PRICE_SOURCES = ("contract", "list", "fallback_override", "fallback_list", "in_house", "none")
NODE_KINDS = ("group", "business_unit", "department", "team", "cost_centre")
PARENT_KINDS: dict[str, tuple[str | None, ...]] = {  # kind -> allowed parent kinds (None = may be a root)
    "group": (None, "group"),
    "business_unit": ("group", "business_unit"),
    "department": ("group", "business_unit", "department"),
    "team": ("department", "team"),
    "cost_centre": ("group", "business_unit", "department", "team"),
}
GATE_NODE_KINDS = ("business_unit", "department", "team", "cost_centre")  # a group node is reported, not counted
MAX_DEPTH = 16
SOURCE_TYPES = ("agent", "application", "workflow", "cost_center", "department")
APPLICATIONS = (
    "agents",
    "chat",
    "voice",
    "workflows",
    "a2a",
    "mcp",
    "api",
    "console",
    "knowledge",
    "documents",
    "speech",
    "content",
    "txn",
    "system",
)
ATTRIBUTION_PATHS = (
    "agent_mapping",
    "cost_centre_mapping",
    "cost_centre_code",
    "workflow_mapping",
    "application_mapping",
    "department_mapping",
    "department_code",
)
UNATTRIBUTED_REASONS = ("no_source", "no_mapping", "unknown_label", "inactive_node", "resolver_failed")
IN_HOUSE_PROVIDERS = ("ollama", "vllm", "local_embeddings", "tei", "tesseract", "faster_whisper")
ZERO_PRICED_IN_HOUSE = ("llm_tokens", "embedding_tokens", "ocr_pages", "speech_minutes")
STORAGE_PROVIDER = "platform_storage"
BILLING_ACCOUNTS = ("tenant_key", "platform_key", "in_house")
# Spend provider -> (credential provider, credential kind) in tenant_ai_credentials.
BILLING_CREDENTIAL_KEYS = {
    "openai": ("openai", "llm"),
    "anthropic": ("anthropic", "llm"),
    "gemini": ("gemini", "llm"),
    "azure_openai": ("azure_openai", "llm"),
    "openai_compatible": ("openai_compatible", "llm"),
    "deepgram": ("stt_deepgram", "stt"),
}
CREDENTIAL_SOURCE_ACCOUNT = {"tenant": "tenant_key", "platform_env": "platform_key"}  # ResolvedCredential.source
PROVIDER_BILLING_TZ_DEFAULTS = {"gemini": "America/Los_Angeles"}
# LangChain class -> provider, for direct callers that declare no provider (Azure kept
# apart from OpenAI, because Azure bills separately).
CLASS_PROVIDERS = {
    "ChatGoogleGenerativeAI": "gemini",
    "ChatVertexAI": "gemini",
    "ChatAnthropic": "anthropic",
    "ChatOpenAI": "openai",
    "AzureChatOpenAI": "azure_openai",
    "ChatOllama": "ollama",
}
FX_SOURCES = ("reference", "manual", "import")
COMMITMENT_KINDS = ("quantity", "money")
COMMITMENT_STATUSES = ("active", "closed")
RISK_TIERS = ("low", "medium", "high", "critical")
LINE_KINDS = ("usage", "credit", "tax", "fee", "commitment")
ITEM_KINDS = ("usage", "non_usage_line")
ITEM_STATUSES = ("within_tolerance", "needs_review", "accepted", "informational")
RUN_STATUSES = ("within_tolerance", "needs_review", "accepted")
INVOICE_SOURCES = ("csv", "json")
INVOICE_STATUSES = ("current", "superseded")
# Every card unit, in one order (the invoice-line unit CHECK lists them so).
ALL_CARD_UNITS = tuple(dict.fromkeys(unit for units in CARD_UNITS.values() for unit in units))
GAP_REASONS = (
    "queue_full",
    "spill_failed",
    "shutdown_lost",
    "paused",
    "tenant_mismatch",
    "failed_no_usage",
    "timeout_estimated",
    "unpriced_tool",
)
JOB_KINDS = ("rebuild", "backfill", "restate", "settle_fx", "reattribute", "recompute_commitments")
JOB_STATUSES = ("queued", "running", "succeeded", "failed")
REPORTING_CURRENCY = "INR"
AMOUNT_QUANT = Decimal("0.0000000001")  # 10 dp, NUMERIC(24,10)
QTY_QUANT = Decimal("0.000001")  # 6 dp, NUMERIC(24,6)
SHARE_QUANT = Decimal("0.000001")  # display only, ROUND_DOWN
FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")
SKU_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 ._:/@-]{0,127}$"

# Bounds every numeric input is held to.
MAX_UNIT_PRICE = Decimal("1e9")
MAX_TIER_QUANTITY = Decimal("1e15")
MAX_COMMITTED_QUANTITY = Decimal("1e15")
MAX_COMMITTED_AMOUNT = Decimal("1e12")
MAX_FX_RATE = Decimal("1e6")
MAX_QUOTE_QUANTITY = Decimal("1e12")
PRICE_PLACES = 10
QUANTITY_PLACES = 6
FX_PLACES = 8
PCT_PLACES = 2

_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:/-]{0,63}$")
_SKU_RE = re.compile(SKU_PATTERN)
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MAX_NUMBER_TEXT = 64
_TRUE = frozenset({"true", "1", "yes", "y"})
_FALSE = frozenset({"false", "0", "no", "n"})


def label(value: Any) -> str:
    """A bounded, lower-cased identifier (``core.finops.attribution.label``); empty when nothing is usable."""
    from core.finops.attribution import label as finops_label

    return finops_label(value)


def norm_code(value: Any) -> str:
    """An organisation's own code (``CC-4120``): trimmed, upper-cased, 1 to 64 of A-Z 0-9 _ . : / -."""
    code = str(value or "").strip().upper()
    if not _CODE_RE.fullmatch(code):
        raise SpendError(
            422, "invalid_code", "a code is 1 to 64 of A-Z 0-9 _ . : / - and starts with a letter or digit"
        )
    return code


def norm_provider(value: Any) -> str:
    """A provider id as the catalogue spells it (``gpt`` is ``openai``), as a bounded label."""
    from core.governance.model_gateway import normalise_provider

    provider = label(normalise_provider(value) or "")
    if not provider:
        raise SpendError(422, "invalid_reference", "provider is required")
    return provider


def norm_sku(value: Any, *, allow_empty: bool = False) -> str:
    """A model or SKU name as cards and invoices use it, lower-cased; ``''`` only where a default is meant."""
    sku = str(value if value is not None else "").strip()
    if not sku and allow_empty:
        return ""
    if not _SKU_RE.fullmatch(sku):
        raise SpendError(
            422,
            "invalid_sku",
            "a model or SKU is 1 to 128 of A-Z a-z 0-9 space . _ : / @ - starting with a letter or digit",
        )
    return sku.lower()


def free_text(value: Any, *, field: str, max_len: int, required: bool = False) -> str:
    """Trimmed text that cannot act as a spreadsheet formula and carries no control character."""
    text = str(value if value is not None else "").strip()
    if not text:
        if required:
            raise SpendError(422, "invalid_text", f"{field} is required")
        return ""
    if len(text) > max_len:
        raise SpendError(422, "invalid_text", f"{field} is at most {max_len} characters")
    if text.startswith(FORMULA_LEADS):
        raise SpendError(422, "invalid_text", f"{field} may not start with = + - @ or a tab")
    if any(unicodedata.category(ch) == "Cc" for ch in text):
        raise SpendError(422, "invalid_text", f"{field} may not contain control characters")
    return text


def norm_currency(value: Any) -> str:
    """A three-letter ISO 4217 code, upper-case."""
    currency = str(value or "").strip().upper()
    if not _CURRENCY_RE.fullmatch(currency):
        raise SpendError(422, "invalid_currency", "currency is a three-letter code such as USD or INR")
    return currency


def choice(value: Any, allowed: tuple[str, ...], *, field: str, code: str = "invalid_value") -> str:
    """``value`` trimmed and lower-cased when it is one of ``allowed``."""
    text = str(value or "").strip().lower()
    if text not in allowed:
        raise SpendError(422, code, f"{field} is one of {', '.join(allowed)}")
    return text


def _decimal_places(number: Decimal) -> int:
    """Decimals ``number`` really carries (trailing zeros do not count), without any context arithmetic."""
    _sign, digits, exponent = number.as_tuple()
    if not isinstance(exponent, int) or exponent >= 0 or not any(digits):
        return 0
    trailing = 0
    for digit in reversed(digits):
        if digit != 0 or trailing >= -exponent:
            break
        trailing += 1
    return max(0, -(exponent + trailing))


def parse_decimal(
    value: Any,
    *,
    field: str,
    minimum: Decimal | int | None = None,
    maximum: Decimal | int | None = None,
    places: int,
    strict_minimum: bool = False,
) -> Decimal:
    """A finite decimal within the bounds and with at most ``places`` decimals; 422 ``invalid_number`` otherwise.

    Text, ints, floats (through their shortest text) and decimals are accepted;
    booleans, NaN, infinities and malformed text are refused. Nothing here
    does arithmetic that a huge exponent could overflow.
    """
    if value is None or isinstance(value, bool):
        raise SpendError(422, "invalid_number", f"{field} is a number")
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        else:
            text = str(value).strip()
            if not text or len(text) > _MAX_NUMBER_TEXT:
                raise SpendError(422, "invalid_number", f"{field} is a number")
            number = Decimal(text)
        if not number.is_finite():
            raise SpendError(422, "invalid_number", f"{field} is a finite number")
        if minimum is not None:
            low = Decimal(minimum)
            if number < low or (strict_minimum and number == low):
                raise SpendError(422, "invalid_number", f"{field} is {'above' if strict_minimum else 'at least'} {low}")
        if maximum is not None and number > Decimal(maximum):
            raise SpendError(422, "invalid_number", f"{field} is at most {Decimal(maximum)}")
        if _decimal_places(number) > places:
            raise SpendError(422, "invalid_number", f"{field} has at most {places} decimals")
    except (DecimalException, ValueError, TypeError):
        raise SpendError(422, "invalid_number", f"{field} is a number") from None
    return number


def dec_str(value: Decimal | None) -> str | None:
    """A decimal as plain text (never exponent notation) for JSON; ``None`` stays ``None``."""
    if value is None:
        return None
    return format(value, "f")


def parse_date(value: Any, *, field: str) -> date:
    """An ISO date (``YYYY-MM-DD``)."""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text) if len(text) == 10 else _bad_date(field)
    except ValueError:
        return _bad_date(field)


def _bad_date(field: str) -> date:
    raise SpendError(422, "invalid_date", f"{field} is a date YYYY-MM-DD")


def parse_bool(value: Any, *, field: str) -> bool | None:
    """``true``/``false`` (also 1/0, yes/no) from a cell; an empty cell is ``None`` (not given)."""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise SpendError(422, "invalid_boolean", f"{field} is true or false")
