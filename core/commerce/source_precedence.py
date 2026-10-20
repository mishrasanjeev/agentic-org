# SPDX-License-Identifier: Apache-2.0
"""Which source wins when the merchant's sources disagree, per fact family.

Fact families: ``price``, ``inventory``, ``policy``, ``catalog`` and
``location``. Sources: ``shopify`` (the one runtime adapter: read-only Admin
API evidence), ``merchant_config`` (the merchant's own settings, such as POS
stores and policy links) and the config-only adapters (``woocommerce``,
``erp``, ``pim``, ``oms``, ``wms``, ``custom_api``). Config-only adapters are
never called: their facts exist only as values the merchant saved in its
config (``provider_policy.source_facts``), so adding one to a precedence list
never makes AgenticOrg fetch anything.

Merchant config, ``provider_policy``::

    "source_precedence": {"price": ["erp", "shopify"], "inventory": ["shopify", "wms"]},
    "source_facts": {
        "erp": {"price": {"TOTE-1": "24.00"}},
        "wms": {"inventory": {"TOTE-1": 7}}
    }

A family absent from ``source_precedence`` uses ``DEFAULT_PRECEDENCE``. A
source missing from a list never wins that family. ``resolve`` returns the
first source in the list that has a value, the value, and every other source
whose value disagrees (for the irregularity log); no source with a value
means no fact (the caller fails closed as it would without that source).
Price values are decimal strings in major units (converted by the caller with
the catalog's exact minor-unit arithmetic), inventory values non-negative
integers, catalog values ``{"title": ...}``, policy and location values
mappings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()

FAMILIES: tuple[str, ...] = ("price", "inventory", "policy", "catalog", "location")
RUNTIME_SOURCES: frozenset[str] = frozenset({"shopify"})
CONFIG_ONLY_SOURCES: frozenset[str] = frozenset({"woocommerce", "erp", "pim", "oms", "wms", "custom_api"})
SOURCES: frozenset[str] = RUNTIME_SOURCES | CONFIG_ONLY_SOURCES | {"merchant_config"}
DEFAULT_PRECEDENCE: dict[str, tuple[str, ...]] = {
    "price": ("shopify", "merchant_config"),
    "inventory": ("shopify", "merchant_config"),
    "catalog": ("shopify", "merchant_config"),
    "policy": ("merchant_config", "shopify"),
    "location": ("merchant_config", "shopify"),
}
MAX_FACTS_PER_FAMILY = 5000


class SourcePrecedenceError(ValueError):
    """An invalid ``source_precedence`` or ``source_facts`` in merchant config."""


@dataclass(frozen=True)
class Resolution:
    family: str
    key: str
    source: str | None
    value: Any
    disagreeing_sources: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.source is not None


def validate_provider_policy(provider_policy: Mapping[str, Any]) -> None:
    """Refuse a malformed precedence table or config-only fact set when the merchant config is saved."""
    raw = provider_policy.get("source_precedence")
    if raw is not None:
        if not isinstance(raw, Mapping):
            raise SourcePrecedenceError("source_precedence must be an object")
        for family, sources in raw.items():
            if family not in FAMILIES:
                raise SourcePrecedenceError(f"source_precedence.{family} is not a fact family")
            if isinstance(sources, str) or not isinstance(sources, Sequence) or not sources:
                raise SourcePrecedenceError(f"source_precedence.{family} must be a non-empty list of sources")
            if len(set(sources)) != len(sources) or any(s not in SOURCES for s in sources):
                raise SourcePrecedenceError(f"source_precedence.{family} names an unknown or repeated source")
    facts = provider_policy.get("source_facts")
    if facts is not None:
        if not isinstance(facts, Mapping):
            raise SourcePrecedenceError("source_facts must be an object")
        for source, families in facts.items():
            if source not in CONFIG_ONLY_SOURCES and source != "merchant_config":
                raise SourcePrecedenceError(f"source_facts.{source}: only config-only sources carry saved facts")
            if not isinstance(families, Mapping):
                raise SourcePrecedenceError(f"source_facts.{source} must be an object")
            for family, values in families.items():
                if family not in FAMILIES or not isinstance(values, Mapping):
                    raise SourcePrecedenceError(f"source_facts.{source}.{family} is not a fact family object")
                if len(values) > MAX_FACTS_PER_FAMILY:
                    raise SourcePrecedenceError(f"source_facts.{source}.{family} has too many entries")
                for key, value in values.items():
                    _check_value(family, f"source_facts.{source}.{family}.{key}", value)


def _check_value(family: str, where: str, value: Any) -> None:
    if family == "price" and not (isinstance(value, str) and value.replace(".", "", 1).isdigit()):
        raise SourcePrecedenceError(f"{where} must be a decimal string")
    if family == "inventory" and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise SourcePrecedenceError(f"{where} must be a non-negative integer")
    if family in ("catalog", "policy", "location") and not isinstance(value, Mapping):
        raise SourcePrecedenceError(f"{where} must be an object")


def precedence(provider_policy: Mapping[str, Any] | None, family: str) -> tuple[str, ...]:
    raw = (provider_policy or {}).get("source_precedence")
    configured = raw.get(family) if isinstance(raw, Mapping) else None
    if isinstance(configured, Sequence) and not isinstance(configured, str) and configured:
        return tuple(str(s) for s in configured if s in SOURCES)
    return DEFAULT_PRECEDENCE[family]


def config_facts(provider_policy: Mapping[str, Any] | None, family: str) -> dict[str, Mapping[str, Any]]:
    """``{source: {key: value}}`` of the saved facts for one family (config-only sources and merchant_config)."""
    raw = (provider_policy or {}).get("source_facts")
    out: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw, Mapping):
        for source, families in raw.items():
            if (source in CONFIG_ONLY_SOURCES or source == "merchant_config") and isinstance(families, Mapping):
                values = families.get(family)
                if isinstance(values, Mapping):
                    out[str(source)] = values
    return out


def resolve(
    family: str,
    key: str,
    observations: Mapping[str, Any],
    *,
    provider_policy: Mapping[str, Any] | None,
) -> Resolution:
    """The winning value for ``key`` among ``observations`` (``{source: value}``, None = no value) and saved facts."""
    if family not in FAMILIES:
        raise SourcePrecedenceError(f"{family} is not a fact family")
    values: dict[str, Any] = {s: v for s, v in observations.items() if v is not None}
    for source, facts in config_facts(provider_policy, family).items():
        if key in facts and source not in values:
            values[source] = facts[key]
    order = precedence(provider_policy, family)
    winner = next((s for s in order if s in values), None)
    if winner is None:
        return Resolution(family=family, key=key, source=None, value=None)
    disagreeing = tuple(
        sorted(s for s, v in values.items() if s != winner and not _same(family, v, values[winner]))
    )
    return Resolution(family=family, key=key, source=winner, value=values[winner], disagreeing_sources=disagreeing)


def _same(family: str, left: Any, right: Any) -> bool:
    if family == "price":
        from decimal import Decimal, InvalidOperation

        try:
            return Decimal(str(left)) == Decimal(str(right))
        except InvalidOperation:
            return False
    return left == right


def has_overrides(provider_policy: Mapping[str, Any] | None) -> bool:
    policy = provider_policy or {}
    return bool(policy.get("source_precedence") or policy.get("source_facts"))


def products_with_precedence(
    products: Sequence[Mapping[str, Any]], provider_policy: Mapping[str, Any] | None
) -> list[Mapping[str, Any]]:
    """Synced product evidence with each variant's price, stock and title taken from the winning source.

    Facts are keyed by SKU (else the source variant id). Without a precedence
    table or saved facts the products are returned unchanged. Disagreements are
    logged as an irregularity with the family and the sources only.
    """
    if not has_overrides(provider_policy):
        return list(products)
    out: list[Mapping[str, Any]] = []
    disagreements: dict[tuple[str, str, tuple[str, ...]], int] = {}
    for product in products:
        variants = []
        for variant in product.get("variants") or ():
            if not isinstance(variant, Mapping):
                variants.append(variant)
                continue
            key = str(variant.get("sku") or variant.get("variant_id") or "")
            updated = dict(variant)
            for family, field_name in (("price", "price"), ("inventory", "inventory_quantity_snapshot")):
                resolution = resolve(family, key, {"shopify": variant.get(field_name)}, provider_policy=provider_policy)
                updated[field_name] = resolution.value
                if resolution.disagreeing_sources:
                    marker = (family, str(resolution.source), resolution.disagreeing_sources)
                    disagreements[marker] = disagreements.get(marker, 0) + 1
            catalog = resolve("catalog", key, {"shopify": None}, provider_policy=provider_policy)
            if isinstance(catalog.value, Mapping) and isinstance(catalog.value.get("title"), str):
                updated["title"] = catalog.value["title"][:255]
            variants.append(updated)
        out.append({**product, "variants": variants})
    for (family, winner, sources), count in disagreements.items():
        logger.info(
            "commerce_fact_source_irregularity", family=family, winner=winner, disagreeing=list(sources), count=count
        )
    return out
