# SPDX-License-Identifier: Apache-2.0
"""The business console: the rules, thresholds and routing a bank changes without a release, per tenant.

The catalogue fixes what may be changed and within what bounds: the
confidence floors that route a document to review and the types always
reviewed, the draft kinds that wait for approval, how many bad answers a
conversation takes before it hands over and how many negative turns
before it offers a person, the ceiling an amount slot accepts per intent,
and the rules that raise an item's priority in the review queue. A value
is kept per tenant in ``business_settings`` with its previous value and
who changed it, and every change writes an audit row. Readers take the
effective value: the tenant's where one is set and the console is on,
the catalogue's default otherwise, so a deployment with the console off
behaves exactly as before.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from core.config import settings

logger = structlog.get_logger()

MAX_LIST = 50
MAX_RULES = 50
MAX_AMOUNT = 1_000_000_000
PRIORITIES = ("critical", "high", "normal", "low")
RULE_OPS = (">=", "<=", "==", "contains")
RULE_KINDS = ("any", "approval", "document", "draft", "case")


class ConsoleError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Setting:
    key: str
    title: str
    description: str
    group: str
    kind: str  # number | integer | boolean | list | mapping | rules
    default: Any
    applies: str  # where the value takes effect
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    options: tuple[str, ...] = ()  # the values a list may hold or the keys a mapping may name

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "group": self.group,
            "kind": self.kind,
            "default": self.default,
            "applies": self.applies,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "unit": self.unit,
            "options": list(self.options),
        }


def _document_types() -> tuple[str, ...]:
    from core.idp.classify import CATALOGUE

    return tuple(item.name for item in CATALOGUE)


def _draft_kinds() -> tuple[str, ...]:
    from core.content.drafting import KINDS

    return tuple(KINDS)


def _disclosure_keys() -> tuple[str, ...]:
    from core.speech.disclosures import CATALOGUE

    return tuple(item.key for item in CATALOGUE)


def _amount_intents() -> tuple[str, ...]:
    from core.conversation.intents import CATALOGUE

    return tuple(
        intent.name
        for intent in CATALOGUE
        if intent.max_amount is not None and any(slot.kind == "amount" for slot in intent.slots)
    )


GROUPS: tuple[tuple[str, str], ...] = (
    ("documents", "Document processing"),
    ("content", "Content services"),
    ("conversations", "Conversations"),
    ("queue", "Review queue"),
    ("speech", "Speech"),
)


def _speech_on() -> bool:
    return bool(getattr(settings, "speech_intelligence_enabled", False))


def catalogue() -> tuple[Setting, ...]:
    """The settings, built on demand so the catalogues they draw options from load lazily."""
    base: tuple[Setting, ...] = (
        Setting(
            "documents.type_confidence_floor",
            "Document type confidence floor",
            "A document whose type is recognised below this confidence goes to review.",
            "documents",
            "number",
            0.6,
            "core/idp/pipeline.py review_of",
            minimum=0.0,
            maximum=1.0,
        ),
        Setting(
            "documents.field_confidence_floor",
            "Field confidence floor",
            "A required field read below this confidence sends the document to review.",
            "documents",
            "number",
            0.7,
            "core/idp/pipeline.py review_of",
            minimum=0.0,
            maximum=1.0,
        ),
        Setting(
            "documents.always_review_types",
            "Document types always reviewed",
            "Documents of these types go to review whatever their confidence.",
            "documents",
            "list",
            [],
            "core/idp/pipeline.py review_of",
            options=_document_types(),
        ),
        Setting(
            "content.approval_kinds",
            "Draft kinds that wait for approval",
            "A draft of one of these kinds waits in the drafts queue until a second person approves it.",
            "content",
            "list",
            ["notice", "circular"],
            "core/content/drafting.py requires_approval",
            options=_draft_kinds(),
        ),
        Setting(
            "conversations.slot_retries",
            "Answers tried before handing over",
            "How many answers for one question may fail before the conversation is handed to a person.",
            "conversations",
            "integer",
            3,
            "core/conversation/dialogue.py advance",
            minimum=1,
            maximum=10,
        ),
        Setting(
            "conversations.negative_turns_before_handoff",
            "Negative turns before offering a person",
            "After this many negative messages in a row the assistant offers to connect a person.",
            "conversations",
            "integer",
            2,
            "core/conversation/runtime.py finish_turn",
            minimum=1,
            maximum=10,
        ),
        Setting(
            "conversations.amount_limits",
            "Amount ceilings by intent",
            "The most an amount slot accepts for an intent; an intent not named keeps the catalogue's ceiling.",
            "conversations",
            "mapping",
            {},
            "core/conversation/dialogue.py parse_slot",
            minimum=1,
            maximum=MAX_AMOUNT,
            unit="INR",
            options=_amount_intents(),
        ),
        Setting(
            "queue.priority_rules",
            "Review queue priority rules",
            "Rules that set an item's priority from its facts: kind, field, operator, value and the priority to give.",
            "queue",
            "rules",
            [],
            "core/workbench/queue.py list_items",
        ),
    )
    if _speech_on():
        # Speech settings exist only while speech intelligence is on, so the console of a
        # deployment without it is unchanged.
        base = base + (
            Setting(
                "speech.required_disclosures",
                "Disclosures required on calls",
                "The scripts an agent must say; each applies to the call types it names.",
                "speech",
                "list",
                ["recorded_line", "identity_verification"],
                "core/speech/disclosures.py required_for",
                options=_disclosure_keys(),
            ),
        )
    return base


def definitions() -> dict[str, Setting]:
    return {item.key: item for item in catalogue()}


def definition(key: str) -> Setting:
    found = definitions().get(key)
    if found is None:
        raise ConsoleError(404, "setting_unknown", f"No setting {key!r}")
    return found


def _number(setting: Setting, raw: Any, *, integer: bool) -> float | int:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ConsoleError(422, "value_invalid", f"{setting.key} is a number")
    value = int(raw) if integer else float(raw)
    if integer and float(raw) != value:
        raise ConsoleError(422, "value_invalid", f"{setting.key} is a whole number")
    if setting.minimum is not None and value < setting.minimum:
        raise ConsoleError(422, "value_invalid", f"{setting.key} is at least {setting.minimum}")
    if setting.maximum is not None and value > setting.maximum:
        raise ConsoleError(422, "value_invalid", f"{setting.key} is at most {setting.maximum}")
    return value


def check_rule(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConsoleError(422, "value_invalid", "each rule is an object")
    kind = raw.get("kind", "any")
    if kind not in RULE_KINDS:
        raise ConsoleError(422, "value_invalid", f"a rule's kind is one of {', '.join(RULE_KINDS)}")
    field_name = raw.get("field")
    if not isinstance(field_name, str) or not field_name.strip() or len(field_name) > 64:
        raise ConsoleError(422, "value_invalid", "a rule names a field")
    op = raw.get("op", "==")
    if op not in RULE_OPS:
        raise ConsoleError(422, "value_invalid", f"a rule's op is one of {', '.join(RULE_OPS)}")
    value = raw.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ConsoleError(422, "value_invalid", "a rule's value is a number or text")
    if isinstance(value, str) and len(value) > 200:
        raise ConsoleError(422, "value_invalid", "a rule's text value is at most 200 characters")
    if op in (">=", "<=") and isinstance(value, str):
        raise ConsoleError(422, "value_invalid", "a numeric comparison needs a number")
    priority = raw.get("priority", "high")
    if priority not in PRIORITIES:
        raise ConsoleError(422, "value_invalid", f"a rule's priority is one of {', '.join(PRIORITIES)}")
    return {"kind": kind, "field": field_name.strip(), "op": op, "value": value, "priority": priority}


def check(key: str, raw: Any) -> Any:
    """The value a setting may hold, normalised, or why it may not."""
    setting = definition(key)
    if setting.kind == "number":
        return _number(setting, raw, integer=False)
    if setting.kind == "integer":
        return _number(setting, raw, integer=True)
    if setting.kind == "boolean":
        if not isinstance(raw, bool):
            raise ConsoleError(422, "value_invalid", f"{key} is true or false")
        return raw
    if setting.kind == "list":
        if not isinstance(raw, list) or len(raw) > MAX_LIST:
            raise ConsoleError(422, "value_invalid", f"{key} is a list of up to {MAX_LIST} names")
        out: list[str] = []
        for item in raw:
            if not isinstance(item, str) or (setting.options and item not in setting.options):
                raise ConsoleError(422, "value_invalid", f"{key} holds only {', '.join(setting.options) or 'names'}")
            if item not in out:
                out.append(item)
        return out
    if setting.kind == "mapping":
        if not isinstance(raw, dict) or len(raw) > MAX_LIST:
            raise ConsoleError(422, "value_invalid", f"{key} is an object of up to {MAX_LIST} entries")
        mapped: dict[str, float] = {}
        for name, amount in raw.items():
            if not isinstance(name, str) or (setting.options and name not in setting.options):
                raise ConsoleError(
                    422, "value_invalid", f"{key} names only {', '.join(setting.options) or 'known keys'}"
                )
            mapped[name] = _number(setting, amount, integer=False)
        return mapped
    if not isinstance(raw, list) or len(raw) > MAX_RULES:
        raise ConsoleError(422, "value_invalid", f"{key} is a list of up to {MAX_RULES} rules")
    return [check_rule(item) for item in raw]


def enabled() -> bool:
    return bool(getattr(settings, "workbench_v2_enabled", False))


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "value": row.value,
        "previous": row.previous,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def values(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every setting with its definition, the tenant's value where set, and the default."""
    from core.database import get_tenant_session
    from core.models.business_setting import BusinessSetting

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (await session.execute(select(BusinessSetting).where(BusinessSetting.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
    kept = {row.key: _row_dict(row) for row in rows}
    out = []
    for setting in catalogue():
        entry = {
            **setting.to_dict(),
            "value": setting.default,
            "source": "default",
            "updated_by": None,
            "updated_at": None,
            "previous": None,
        }
        if setting.key in kept:
            entry.update(kept[setting.key])
            entry["source"] = "set"
        out.append(entry)
    return out


async def effective(tenant_id: uuid.UUID | str, keys: list[str]) -> dict[str, Any]:
    """The values in force: the tenant's where set and the console is on, the defaults otherwise.

    A store that cannot be read gives the defaults and a warning, never a refusal of the caller's request.
    """
    defs = definitions()
    out = {key: defs[key].default for key in keys if key in defs}
    tenant = _tenant(tenant_id)
    if not enabled() or not out or tenant is None:
        return out
    from core.database import get_tenant_session
    from core.models.business_setting import BusinessSetting

    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (
                (
                    await session.execute(
                        select(BusinessSetting).where(
                            BusinessSetting.tenant_id == tenant, BusinessSetting.key.in_(list(out))
                        )
                    )
                )
                .scalars()
                .all()
            )
    except (RuntimeError, OSError, SQLAlchemyError) as exc:
        logger.warning("business_settings_unavailable", error_type=type(exc).__name__)
        return out
    for row in rows:
        try:
            out[row.key] = check(row.key, row.value)
        except ConsoleError:
            logger.warning("business_setting_ignored", key=row.key)
    return out


def _tenant(tenant_id: uuid.UUID | str) -> uuid.UUID | None:
    """The tenant id as a UUID; None when it is not one, and then only the defaults apply."""
    if isinstance(tenant_id, uuid.UUID):
        return tenant_id
    try:
        return uuid.UUID(str(tenant_id))
    except (TypeError, ValueError):
        return None


async def value(tenant_id: uuid.UUID | str, key: str) -> Any:
    return (await effective(tenant_id, [key]))[key]


def _audit(session: Any, tenant_id: uuid.UUID, *, actor: str, event: str, key: str, previous: Any, new: Any) -> None:
    from core.models.audit import AuditLog

    session.add(
        AuditLog(
            tenant_id=tenant_id,
            event_type=event,
            actor_type="user",
            actor_id=str(actor)[:255] or "unknown",
            action=key,
            outcome="success",
            resource_type="business_setting",
            resource_id=key,
            details={"key": key, "previous": previous, "value": new},
        )
    )


async def put(tenant_id: uuid.UUID, key: str, raw: Any, *, actor: str) -> dict[str, Any]:
    """Set a tenant's value for a setting, keeping the one before and writing an audit row."""
    from core.database import get_tenant_session
    from core.models.business_setting import BusinessSetting

    checked = check(key, raw)
    setting = definition(key)
    now = datetime.now(UTC)
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(BusinessSetting)
                .where(BusinessSetting.tenant_id == tenant_id, BusinessSetting.key == key)
                .with_for_update()
            )
        ).scalar_one_or_none()
        previous = row.value if row is not None else None
        if row is None:
            row = BusinessSetting(
                tenant_id=tenant_id, key=key, value=checked, updated_by=str(actor)[:128] or None, updated_at=now
            )
            session.add(row)
        else:
            row.previous = row.value
            row.value = checked
            row.updated_by = str(actor)[:128] or None
            row.updated_at = now
        _audit(session, tenant_id, actor=actor, event="workbench.console.set", key=key, previous=previous, new=checked)
        await session.flush()
        answer = {**setting.to_dict(), **_row_dict(row), "source": "set"}
    logger.info("business_setting_changed", key=key)
    return answer


async def reset(tenant_id: uuid.UUID, key: str, *, actor: str) -> dict[str, Any]:
    """Remove a tenant's value so the default applies again, with an audit row."""
    from core.database import get_tenant_session
    from core.models.business_setting import BusinessSetting

    setting = definition(key)
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(BusinessSetting)
                .where(BusinessSetting.tenant_id == tenant_id, BusinessSetting.key == key)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is not None:
            _audit(
                session, tenant_id, actor=actor, event="workbench.console.reset", key=key, previous=row.value, new=None
            )
            await session.delete(row)
            await session.flush()
    return {
        **setting.to_dict(),
        "value": setting.default,
        "source": "default",
        "updated_by": None,
        "updated_at": None,
        "previous": None,
    }


# ---------------------------------------------------------------- readers for the services


async def document_rules(tenant_id: uuid.UUID | str) -> Any:
    found = await effective(
        tenant_id,
        ["documents.type_confidence_floor", "documents.field_confidence_floor", "documents.always_review_types"],
    )
    from core.idp.pipeline import ReviewRules

    return ReviewRules(
        type_floor=float(found["documents.type_confidence_floor"]),
        field_floor=float(found["documents.field_confidence_floor"]),
        always_review=tuple(found["documents.always_review_types"]),
    )


async def approval_kinds(tenant_id: uuid.UUID | str) -> tuple[str, ...]:
    return tuple(await value(tenant_id, "content.approval_kinds"))


@dataclass(frozen=True)
class ConversationRules:
    retries: int = 3
    negative_turns: int = 2
    amount_limits: dict[str, float] = field(default_factory=dict)


async def conversation_rules(tenant_id: uuid.UUID | str) -> ConversationRules:
    found = await effective(
        tenant_id,
        ["conversations.slot_retries", "conversations.negative_turns_before_handoff", "conversations.amount_limits"],
    )
    return ConversationRules(
        retries=int(found["conversations.slot_retries"]),
        negative_turns=int(found["conversations.negative_turns_before_handoff"]),
        amount_limits={k: float(v) for k, v in dict(found["conversations.amount_limits"]).items()},
    )


def _matches(rule: dict[str, Any], item: dict[str, Any]) -> bool:
    if rule["kind"] != "any" and item.get("kind") != rule["kind"]:
        return False
    facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
    actual = facts.get(rule["field"], item.get(rule["field"]))
    if actual is None:
        return False
    op, wanted = rule["op"], rule["value"]
    if op == "contains":
        if isinstance(actual, list | tuple | set):
            return any(str(wanted).lower() == str(a).lower() for a in actual)
        return str(wanted).lower() in str(actual).lower()
    if op == "==":
        if isinstance(actual, int | float) and not isinstance(actual, bool) and isinstance(wanted, int | float):
            return float(actual) == float(wanted)
        return str(actual).lower() == str(wanted).lower()
    try:
        number = float(actual)
    except (TypeError, ValueError):
        return False
    return number >= float(wanted) if op == ">=" else number <= float(wanted)


def apply_priority_rules(items: list[dict[str, Any]], rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each item's priority from the first rule that matches it; items without a match keep their own."""
    if not rules:
        return items
    for item in items:
        for rule in rules:
            if _matches(rule, item):
                item["priority"] = rule["priority"]
                item["priority_rule"] = f"{rule['field']} {rule['op']} {rule['value']}"
                break
    return items
