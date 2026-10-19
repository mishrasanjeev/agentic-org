# SPDX-License-Identifier: Apache-2.0
"""UCP quantities: integer step counts of an item's sale basis (UCP 2026-08-25 overview, Quantities and units).

``line_items[].quantity`` is an integer from 1 to 2^53-1 counting *steps*. The
item's ``quantity_unit`` (``unit.json`` + ``increment``) says what one step is:
``unit`` (a UN/CEFACT Recommendation 20 code such as ``C62`` each, ``KGM``,
``MTR``; Recommendation 21 ``X*`` package codes are forbidden), ``scale``
(0 to 15; one step is 10^-scale of the unit; ``C62`` has scale 0) and
``increment`` (advisory ordering granularity in steps). An item without a
``quantity_unit`` is sold each at scale 0.

When the platform asserts a unit that differs from the business's
(``(unit, scale)`` is the identity), the business converts visibly (a
warning at the line) when the conversion is exact, or rejects recoverably;
silent conversion is forbidden. Arithmetic is exact integer arithmetic.

The program text described the quantity as ``int | Measure``; the published
schema has no such union (``measure.json`` is used for ``unit_price`` and
order adjustments only), so quantities stay integers here.
2026-04-08 has none of this: its ``quantity`` is a plain integer >= 1.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MAX_QUANTITY = 2**53 - 1
MAX_SCALE = 15
EACH = "C62"
_REC21_PACKAGE = re.compile(r"^X[0-9A-Z]{2}$")
_UNIT_CODE = re.compile(r"^[0-9A-Z]{2,3}$")


class QuantityError(ValueError):
    """An invalid quantity or unit; ``code`` is the UCP message code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class QuantityUnit:
    unit: str = EACH
    scale: int = 0
    display_text: str = "each"
    increment: int = 1

    @property
    def identity(self) -> tuple[str, int]:
        return (self.unit, self.scale)

    @property
    def is_each(self) -> bool:
        return self.unit == EACH and self.scale == 0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"unit": self.unit, "display_text": self.display_text}
        if self.scale:
            out["scale"] = self.scale
        if self.increment != 1:
            out["increment"] = self.increment
        return out


EACH_UNIT = QuantityUnit()


def parse_quantity_unit(value: Any) -> QuantityUnit:
    """Validate a ``quantity_unit`` object; raises ``QuantityError``."""
    if not isinstance(value, Mapping):
        raise QuantityError("invalid_quantity_unit", "quantity_unit must be an object")
    unit = value.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        raise QuantityError("invalid_quantity_unit", "quantity_unit.unit is required")
    unit = unit.strip()
    if _REC21_PACKAGE.match(unit):
        raise QuantityError("invalid_quantity_unit", "Recommendation 21 package codes are not sale units")
    scale = value.get("scale", 0)
    if isinstance(scale, bool) or not isinstance(scale, int) or not 0 <= scale <= MAX_SCALE:
        raise QuantityError("invalid_quantity_unit", "quantity_unit.scale must be an integer from 0 to 15")
    if unit == EACH and scale != 0:
        raise QuantityError("invalid_quantity_unit", "C62 (each) has scale 0")
    increment = value.get("increment", 1)
    if isinstance(increment, bool) or not isinstance(increment, int) or not 1 <= increment <= MAX_QUANTITY:
        raise QuantityError("invalid_quantity_unit", "quantity_unit.increment must be a positive integer")
    display = value.get("display_text")
    if not isinstance(display, str) or not display.strip():
        raise QuantityError("invalid_quantity_unit", "quantity_unit.display_text is required")
    return QuantityUnit(unit=unit, scale=scale, display_text=display.strip(), increment=increment)


def parse_quantity(value: Any) -> int:
    """A step count: an integer (never a float or a boolean) from 1 to 2^53-1."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise QuantityError("invalid_quantity", "quantity must be an integer step count")
    if not 1 <= value <= MAX_QUANTITY:
        raise QuantityError("invalid_quantity", "quantity must be between 1 and 9007199254740991")
    return value


def line_amount(unit_price_minor: int, quantity: int, scale: int) -> int:
    """``unit_price_minor`` x ``quantity`` steps of 10^-scale units, exact, rounded half to even."""
    numerator = unit_price_minor * quantity
    denominator = 10**scale
    quotient, remainder = divmod(numerator, denominator)
    twice = remainder * 2
    if twice > denominator or (twice == denominator and quotient % 2 == 1):
        quotient += 1
    return quotient


@dataclass(frozen=True)
class Reconciled:
    quantity: int
    warning: dict[str, Any] | None = None


def reconcile(asserted: QuantityUnit | None, authoritative: QuantityUnit, quantity: int) -> Reconciled:
    """Apply the asserted-vs-authoritative unit rule; raises ``QuantityError`` for a recoverable rejection."""
    if asserted is None or asserted.identity == authoritative.identity:
        return Reconciled(quantity=quantity)
    if asserted.unit != authoritative.unit:
        raise QuantityError(
            "quantity_unit_mismatch",
            f"This item is sold in {authoritative.display_text} ({authoritative.unit}), not {asserted.unit}.",
        )
    shift = authoritative.scale - asserted.scale
    if shift >= 0:
        converted = quantity * 10**shift
    else:
        factor = 10 ** (-shift)
        if quantity % factor:
            raise QuantityError(
                "quantity_unit_mismatch",
                f"{quantity} steps at scale {asserted.scale} is not a whole number of steps at scale "
                f"{authoritative.scale}.",
            )
        converted = quantity // factor
    parse_quantity(converted)
    return Reconciled(
        quantity=converted,
        warning={
            "type": "warning",
            "code": "quantity_converted",
            "content": (
                f"Quantity converted from scale {asserted.scale} to scale {authoritative.scale} "
                f"of {authoritative.unit}: {quantity} became {converted} steps."
            ),
        },
    )


def increment_warning(unit: QuantityUnit, quantity: int) -> dict[str, Any] | None:
    if unit.increment > 1 and quantity % unit.increment:
        return {
            "type": "warning",
            "code": "quantity_increment",
            "content": f"This item is ordered in multiples of {unit.increment} steps.",
        }
    return None
