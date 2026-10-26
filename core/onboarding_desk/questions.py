# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""What an application asks for: the item catalogue, the partner's policy per merchant type, validation.

Every item says, in English and French, what it is and why it is needed. The
partner's policy picks the required and optional items per merchant type; an
applicant is never asked for an item the policy does not list. Answers are
validated and normalised here, the same way on every channel, and
``business_case_application`` turns a complete set of answers into the
``business_case`` application a governed case is opened with.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

from core.onboarding_desk import rules

KINDS: tuple[str, ...] = ("text", "choice", "address", "owners", "url", "document")
#: Item kinds that can be asked and answered in a text message.
SMS_KINDS: frozenset[str] = frozenset({"text", "choice", "url"})
MAX_OWNERS = 20
MAX_MERCHANT_TYPES = 50
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_NAME_CHARS = re.compile(r"^[^\W_](?:[^\W_]|[ &'.,()/:+\-–—])*$")
_US_POSTAL = re.compile(r"^\d{5}(?:-\d{4})?$")
_CA_POSTAL = re.compile(r"^([A-Z]\d[A-Z]) ?(\d[A-Z]\d)$")
_REGISTRATION = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,19}$")
_PARTIAL_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


class AnswerError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    label: dict[str, str]
    reason: dict[str, str]
    choices: tuple[tuple[str, dict[str, str]], ...] = ()

    def view(self, lang: str, *, required: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "required": required,
            "label": self.label[lang],
            "reason": self.reason[lang],
        }
        if self.choices:
            out["choices"] = [{"value": value, "label": labels[lang]} for value, labels in self.choices]
        return out


def _c(value: str, en: str, fr: str) -> tuple[str, dict[str, str]]:
    return value, {"en": en, "fr": fr}


ITEMS: dict[str, Item] = {
    item.id: item
    for item in (
        Item(
            "legal_name",
            "text",
            {"en": "Legal business name", "fr": "Dénomination sociale"},
            {
                "en": "We match it against the official business registry to confirm your business exists.",
                "fr": "Nous le comparons au registre officiel des entreprises pour confirmer que votre entreprise "
                "existe.",
            },
        ),
        Item(
            "trading_name",
            "text",
            {"en": "Name customers know you by, if different", "fr": "Nom commercial, s'il est différent"},
            {
                "en": "It appears on your customers' statements and helps us find your business.",
                "fr": "Il figure sur les relevés de vos clients et nous aide à trouver votre entreprise.",
            },
        ),
        Item(
            "business_structure",
            "choice",
            {"en": "Business structure", "fr": "Forme juridique"},
            {
                "en": "It decides which registry we check and which owners we need to know about.",
                "fr": "Elle détermine le registre consulté et les propriétaires que nous devons connaître.",
            },
            (
                _c("corporation", "Corporation", "Société par actions"),
                _c("llc", "Limited liability company", "Société à responsabilité limitée"),
                _c("partnership", "Partnership", "Société de personnes"),
                _c("sole_proprietorship", "Sole proprietorship", "Entreprise individuelle"),
                _c("nonprofit", "Non-profit organization", "Organisme sans but lucratif"),
                _c("cooperative", "Cooperative", "Coopérative"),
                _c("other", "Other", "Autre"),
            ),
        ),
        Item(
            "registration_number",
            "text",
            {
                "en": "Registration number (state file or corporation number)",
                "fr": "Numéro d'immatriculation (numéro de société ou NEQ)",
            },
            {
                "en": "It lets us find your exact record in the registry.",
                "fr": "Il nous permet de trouver votre fiche exacte au registre.",
            },
        ),
        Item(
            "tax_id",
            "text",
            {"en": "Business tax number (EIN or Business Number)", "fr": "Numéro fiscal de l'entreprise (NE)"},
            {
                "en": "Payment processors must confirm the business's tax identity. We never ask for a personal "
                "Social Security or Social Insurance Number.",
                "fr": "Les processeurs de paiement doivent confirmer l'identité fiscale de l'entreprise. Nous ne "
                "demandons jamais de numéro d'assurance sociale personnel.",
            },
        ),
        Item(
            "registered_address",
            "address",
            {"en": "Registered business address", "fr": "Adresse du siège"},
            {
                "en": "We compare it with the registry and use it for compliance checks.",
                "fr": "Nous la comparons au registre et l'utilisons pour les vérifications de conformité.",
            },
        ),
        Item(
            "website",
            "url",
            {"en": "Website, if you have one", "fr": "Site Web, le cas échéant"},
            {
                "en": "It helps us understand what you sell and confirm the business is active.",
                "fr": "Il nous aide à comprendre ce que vous vendez et à confirmer que l'entreprise est active.",
            },
        ),
        Item(
            "activity",
            "choice",
            {"en": "What the business does", "fr": "Activité de l'entreprise"},
            {
                "en": "Some activities need extra checks under card network rules.",
                "fr": "Certaines activités exigent des vérifications supplémentaires selon les règles des réseaux "
                "de cartes.",
            },
            (
                _c("retail", "Retail", "Commerce de détail"),
                _c("food_service", "Food service", "Restauration"),
                _c("healthcare", "Healthcare", "Soins de santé"),
                _c("consulting", "Professional services", "Services professionnels"),
                _c("construction", "Construction and trades", "Construction et métiers"),
                _c("education", "Education", "Éducation"),
                _c("logistics", "Transport and logistics", "Transport et logistique"),
                _c("manufacturing", "Manufacturing", "Fabrication"),
                _c("real_estate", "Real estate", "Immobilier"),
                _c("software", "Software and online services", "Logiciels et services en ligne"),
                _c("financial_services", "Financial services", "Services financiers"),
                _c("other", "Something else", "Autre"),
            ),
        ),
        Item(
            "owners",
            "owners",
            {"en": "Owners and people in control", "fr": "Propriétaires et personnes qui exercent le contrôle"},
            {
                "en": "The law requires us to know who owns 25% or more of the business and who controls it.",
                "fr": "La loi exige de connaître les personnes qui détiennent 25 % ou plus de l'entreprise et celles "
                "qui la contrôlent.",
            },
        ),
        Item(
            "formation_document",
            "document",
            {
                "en": "Formation document (articles, certificate of formation or incorporation)",
                "fr": "Document constitutif (statuts ou certificat de constitution)",
            },
            {
                "en": "It confirms the business's legal name and registration.",
                "fr": "Il confirme la dénomination et l'immatriculation de l'entreprise.",
            },
        ),
        Item(
            "tax_id_document",
            "document",
            {
                "en": "Tax number letter (IRS EIN letter or CRA Business Number confirmation)",
                "fr": "Lettre du numéro fiscal (confirmation du NE de l'ARC)",
            },
            {
                "en": "It confirms the tax number belongs to this business.",
                "fr": "Elle confirme que le numéro fiscal appartient à cette entreprise.",
            },
        ),
    )
}

DEFAULT_REQUIRED: tuple[str, ...] = (
    "legal_name",
    "business_structure",
    "registration_number",
    "tax_id",
    "registered_address",
    "activity",
    "owners",
    "formation_document",
)
DEFAULT_OPTIONAL: tuple[str, ...] = ("trading_name", "website", "tax_id_document")
#: Items every application needs: a governed case cannot be opened without them.
ALWAYS_REQUIRED: tuple[str, ...] = ("legal_name", "owners")
OWNER_KINDS = ("person", "business")
OWNER_ROLES = ("owner", "controller", "owner_and_controller")


@dataclass(frozen=True)
class QuestionSet:
    required: tuple[str, ...] = DEFAULT_REQUIRED
    optional: tuple[str, ...] = DEFAULT_OPTIONAL

    def items(self) -> tuple[str, ...]:
        return self.required + self.optional

    def to_dict(self) -> dict[str, list[str]]:
        return {"required": list(self.required), "optional": list(self.optional)}


@dataclass(frozen=True)
class ApplicationPolicy:
    default: QuestionSet = QuestionSet()
    merchant_types: dict[str, QuestionSet] | None = None

    def for_type(self, merchant_type: str) -> QuestionSet:
        return (self.merchant_types or {}).get(merchant_type, self.default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "default": self.default.to_dict(),
            "merchant_types": {name: qs.to_dict() for name, qs in (self.merchant_types or {}).items()},
        }


class PolicyError(ValueError):
    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field = field_name
        self.message = message


def _question_set(raw: Any, where: str) -> QuestionSet:
    if not isinstance(raw, dict) or set(raw) - {"required", "optional"}:
        raise PolicyError(where, "must be an object with required and optional item lists")
    required = raw.get("required", list(DEFAULT_REQUIRED))
    optional = raw.get("optional", [])
    for name, value in (("required", required), ("optional", optional)):
        if not isinstance(value, list) or any(not isinstance(i, str) or i not in ITEMS for i in value):
            raise PolicyError(f"{where}.{name}", f"items must be among {sorted(ITEMS)}")
        if len(set(value)) != len(value):
            raise PolicyError(f"{where}.{name}", "items must not repeat")
    if set(required) & set(optional):
        raise PolicyError(where, "an item cannot be both required and optional")
    missing = [i for i in ALWAYS_REQUIRED if i not in required]
    if missing:
        raise PolicyError(f"{where}.required", f"must include {missing}")
    return QuestionSet(tuple(required), tuple(optional))


def application_policy_from(raw: dict[str, Any] | None) -> ApplicationPolicy:
    raw = dict(raw or {})
    if set(raw) - {"default", "merchant_types"}:
        raise PolicyError("application", "may only set default and merchant_types")
    default = _question_set(raw["default"], "default") if "default" in raw else QuestionSet()
    types_raw = raw.get("merchant_types")
    types_raw = {} if types_raw is None else types_raw
    if not isinstance(types_raw, dict) or len(types_raw) > MAX_MERCHANT_TYPES:
        raise PolicyError("merchant_types", f"must be an object of at most {MAX_MERCHANT_TYPES} merchant types")
    types: dict[str, QuestionSet] = {}
    for name, value in types_raw.items():
        if not isinstance(name, str) or not _SLUG_RE.match(name):
            raise PolicyError("merchant_types", "merchant types are lower-case slugs")
        types[name] = _question_set(value, f"merchant_types.{name}")
    return ApplicationPolicy(default, types)


# ── Validation ───────────────────────────────────────────────────────────────


def _clean(value: Any, *, max_length: int) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = " ".join(text.split())
    if len(text) > max_length or any(unicodedata.category(ch).startswith("C") for ch in text):
        raise AnswerError("too_long" if len(text) > max_length else "invalid_characters")
    return text


def _name(value: Any) -> str:
    text = _clean(value, max_length=160)
    if len(text) < 2:
        raise AnswerError("required")
    if not _NAME_CHARS.match(text):
        raise AnswerError("invalid_characters")
    return text


def _choice(item: Item, value: Any) -> str:
    text = _clean(value, max_length=64).lower()
    for index, (choice, labels) in enumerate(item.choices, start=1):
        if text in {choice, str(index), labels["en"].lower(), labels["fr"].lower()}:
            return choice
    raise AnswerError("not_a_choice")


def _registration(value: Any) -> str:
    text = _clean(value, max_length=40).upper().replace(" ", "")
    if not _REGISTRATION.match(text):
        raise AnswerError("registration_invalid")
    return text


def _tax_id(value: Any, country: str) -> str:
    digits = re.sub(r"[\s-]", "", _clean(value, max_length=40)).upper()
    if country == "US":
        if not re.fullmatch(r"\d{9}", digits) or digits.startswith("00"):
            raise AnswerError("ein_invalid")
        return f"{digits[:2]}-{digits[2:]}"
    match = re.fullmatch(r"(\d{9})(?:R[TCPM]\d{4})?", digits)
    if not match:
        raise AnswerError("business_number_invalid")
    return match.group(1)


def _postal(value: Any, country: str) -> str:
    text = _clean(value, max_length=16).upper()
    if country == "US":
        if not _US_POSTAL.match(text):
            raise AnswerError("postal_code_invalid")
        return text
    match = _CA_POSTAL.match(text)
    if not match:
        raise AnswerError("postal_code_invalid")
    return f"{match.group(1)} {match.group(2)}"


def _address(value: Any, country: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"lines", "locality", "region", "postal_code"}:
        raise AnswerError("address_invalid")
    lines_raw = value.get("lines")
    if isinstance(lines_raw, str):
        lines_raw = [lines_raw]
    if not isinstance(lines_raw, list) or not 1 <= len(lines_raw) <= 3:
        raise AnswerError("address_invalid")
    lines = [_clean(line, max_length=120) for line in lines_raw]
    lines = [line for line in lines if line]
    locality = _clean(value.get("locality"), max_length=80)
    region = _clean(value.get("region"), max_length=2).upper()
    if not lines or not locality:
        raise AnswerError("address_invalid")
    if region not in rules.REGIONS[country]:
        raise AnswerError("region_invalid")
    return {
        "lines": lines,
        "locality": locality,
        "region": region,
        "postal_code": _postal(value.get("postal_code"), country),
    }


def _url(value: Any) -> str:
    text = _clean(value, max_length=2048)
    if text and "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or "." not in host or parts.username or parts.password:
        raise AnswerError("url_invalid")
    return text


def _partial_date(value: Any, today: date) -> str | None:
    if value in (None, ""):
        return None
    text = _clean(value, max_length=10)
    match = _PARTIAL_DATE.match(text)
    if not match:
        raise AnswerError("date_invalid")
    year, month, day = int(match.group(1)), match.group(2), match.group(3)
    try:
        born = date(year, int(month or 1), int(day or 1))
    except ValueError as exc:
        raise AnswerError("date_invalid") from exc
    if born > today or year < today.year - 120 or (today.year - year) < 16:
        raise AnswerError("date_invalid")
    return text


def _owners(value: Any, today: date) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_OWNERS:
        raise AnswerError("owners_invalid")
    owners: list[dict[str, Any]] = []
    total = 0.0
    for raw in value:
        if not isinstance(raw, dict) or set(raw) - {"name", "kind", "role", "ownership_pct", "date_of_birth"}:
            raise AnswerError("owners_invalid")
        kind = raw.get("kind") or "person"
        role = raw.get("role") or "owner"
        if kind not in OWNER_KINDS or role not in OWNER_ROLES:
            raise AnswerError("owners_invalid")
        pct = raw.get("ownership_pct")
        if pct is not None:
            if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
                raise AnswerError("ownership_invalid")
            total += float(pct)
        dob = _partial_date(raw.get("date_of_birth"), today) if kind == "person" else None
        owners.append(
            {
                "name": _name(raw.get("name")),
                "kind": kind,
                "role": role,
                "ownership_pct": float(pct) if pct is not None else None,
                "date_of_birth": dob,
            }
        )
    if total > 100.01:
        raise AnswerError("ownership_over_100")
    return owners


def validate_answer(item_id: str, value: Any, *, country: str, today: date | None = None) -> Any:
    """The normalised answer, or ``AnswerError`` with a stable code."""
    item = ITEMS.get(item_id)
    if item is None:
        raise AnswerError("item_unknown")
    if item.kind == "document":
        raise AnswerError("upload_required")
    if country not in rules.REGIONS:
        raise AnswerError("country_not_supported")
    day = today or datetime.now(UTC).date()
    if item_id in ("legal_name", "trading_name"):
        return _name(value)
    if item.kind == "choice":
        return _choice(item, value)
    if item_id == "registration_number":
        return _registration(value)
    if item_id == "tax_id":
        return _tax_id(value, country)
    if item.kind == "address":
        return _address(value, country)
    if item.kind == "url":
        return _url(value)
    return _owners(value, day)


ERRORS: dict[str, dict[str, str]] = {
    "required": {"en": "Please give an answer.", "fr": "Veuillez répondre."},
    "too_long": {"en": "That answer is too long.", "fr": "Cette réponse est trop longue."},
    "invalid_characters": {
        "en": "That answer has characters we can't accept.",
        "fr": "Cette réponse contient des caractères non acceptés.",
    },
    "not_a_choice": {"en": "Please pick one of the options.", "fr": "Veuillez choisir l'une des options."},
    "registration_invalid": {
        "en": "Registration numbers are 3 to 20 letters, digits or dashes.",
        "fr": "Le numéro d'immatriculation compte de 3 à 20 lettres, chiffres ou tirets.",
    },
    "ein_invalid": {"en": "An EIN is 9 digits, like 12-3456789.", "fr": "Un EIN compte 9 chiffres, p. ex. 12-3456789."},
    "business_number_invalid": {
        "en": "A Business Number is 9 digits, like 123456789.",
        "fr": "Un numéro d'entreprise compte 9 chiffres, p. ex. 123456789.",
    },
    "postal_code_invalid": {"en": "That postal code doesn't look right.", "fr": "Ce code postal semble incorrect."},
    "region_invalid": {
        "en": "Please give the state or province as its two-letter code.",
        "fr": "Veuillez indiquer la province ou l'État par son code à deux lettres.",
    },
    "address_invalid": {
        "en": "Please give the street, city, state or province and postal code.",
        "fr": "Veuillez indiquer la rue, la ville, la province ou l'État et le code postal.",
    },
    "url_invalid": {"en": "That web address doesn't look right.", "fr": "Cette adresse Web semble incorrecte."},
    "date_invalid": {
        "en": "Please give a valid date of birth.",
        "fr": "Veuillez indiquer une date de naissance valide.",
    },
    "owners_invalid": {
        "en": "Please list each owner or person in control with their name.",
        "fr": "Veuillez indiquer chaque propriétaire ou personne qui exerce le contrôle avec son nom.",
    },
    "ownership_invalid": {
        "en": "Ownership is a percentage from 0 to 100.",
        "fr": "La part est un pourcentage de 0 à 100.",
    },
    "ownership_over_100": {"en": "Ownership adds up to more than 100%.", "fr": "Les parts dépassent 100 %."},
    "upload_required": {"en": "Please upload this document.", "fr": "Veuillez téléverser ce document."},
    "item_unknown": {"en": "We didn't ask for that.", "fr": "Nous n'avons pas demandé cela."},
    "item_not_requested": {"en": "We didn't ask for that.", "fr": "Nous n'avons pas demandé cela."},
    "country_not_supported": {
        "en": "We can only onboard businesses in the United States and Canada.",
        "fr": "Nous ne pouvons intégrer que des entreprises aux États-Unis et au Canada.",
    },
}


def error_text(code: str, lang: str) -> str:
    return ERRORS.get(code, ERRORS["required"])["fr" if lang == "fr" else "en"]


# ── Progress and the governed-case application ───────────────────────────────


def missing(question_set: QuestionSet, answers: dict[str, Any], documents: set[str]) -> list[str]:
    """Required items with no answer yet, in the policy's order."""
    return [
        item_id
        for item_id in question_set.required
        if (item_id not in documents if ITEMS[item_id].kind == "document" else item_id not in answers)
    ]


def view(question_set: QuestionSet, lang: str) -> list[dict[str, Any]]:
    lang = "fr" if lang == "fr" else "en"
    return [ITEMS[i].view(lang, required=True) for i in question_set.required] + [
        ITEMS[i].view(lang, required=False) for i in question_set.optional
    ]


def _identifiers(answers: dict[str, Any], country: str, region: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if "tax_id" in answers:
        out.append({"scheme": "us_ein" if country == "US" else "ca_business_number", "value": answers["tax_id"]})
    if "registration_number" in answers:
        if country == "US":
            scheme = "us_state_file_number"
        else:
            scheme = "ca_qc_neq" if region == "QC" else "ca_corporation_number"
        out.append({"scheme": scheme, "value": answers["registration_number"]})
    return out


def business_case_application(answers: dict[str, Any], *, country: str, region: str) -> dict[str, Any]:
    """The ``business_case`` application for a governed case; the declared roles stay with the desk.

    The jurisdiction is the registered address's state or province, where the business is registered,
    or the lead's region when no address was asked for.
    """
    address = answers.get("registered_address")
    activity = answers.get("activity")
    region = address["region"] if address else region
    return {
        "legal_name": answers["legal_name"],
        "trading_names": [answers["trading_name"]] if answers.get("trading_name") else [],
        "jurisdiction": f"{country}-{region}",
        "identifiers": _identifiers(answers, country, region),
        "registered_address": (
            {
                "lines": address["lines"],
                "locality": address["locality"],
                "region": address["region"],
                "postal_code": address["postal_code"],
                "country": country,
            }
            if address
            else None
        ),
        "website": answers.get("website"),
        "declared_activity": activity if activity and activity != "other" else None,
        "declared_owners": [
            {
                "name": owner["name"],
                "kind": owner["kind"],
                "date_of_birth": owner.get("date_of_birth"),
                "ownership_pct": owner.get("ownership_pct"),
            }
            for owner in answers.get("owners", [])
        ],
    }
