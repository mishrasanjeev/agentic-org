# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A shop's local delivery area: the postal codes it delivers to, checked before a payment link.

The setup flow stores the area at ``provider_policy.ucp.fulfillment.postal_code_prefixes``
as a list of postal code prefixes, for example Indian PIN code prefixes ``["5600", "5601"]``.
When the list is set, every shipping destination must carry a postal code that starts with
one of them; otherwise the checkout offers no shipping option and says why, so it never
becomes ready for a payment link. Store pickup is not affected. An empty list (the default)
means the shop's shipping rates alone decide where it delivers.

Postal codes are compared without spaces or hyphens and in upper case, so ``"560 001"``
matches ``"5600"`` and a UK prefix ``"SW1"`` matches ``"sw1a 1aa"``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

MAX_POSTAL_PREFIXES = 50
MAX_PREFIX_CHARS = 10
SHOWN_PREFIXES = 8
_PREFIX = re.compile(r"^[A-Z0-9]{1,10}$")
_SEPARATORS = re.compile(r"[\s-]+")


class DeliveryAreaError(ValueError):
    """A postal code prefix the setup flow refuses."""


def compact_postal_code(value: Any) -> str:
    """A postal code without spaces or hyphens, in upper case ("" when there is none)."""
    if not isinstance(value, str | int) or isinstance(value, bool):
        return ""
    return _SEPARATORS.sub("", str(value)).upper()


def normalize_postal_prefixes(values: Iterable[Any]) -> list[str]:
    """The prefixes a merchant entered, checked and de-duplicated in order; ``DeliveryAreaError`` names a bad one."""
    out: list[str] = []
    for raw in values:
        prefix = compact_postal_code(raw)
        if not prefix:
            continue
        if not _PREFIX.match(prefix):
            raise DeliveryAreaError(
                f"{str(raw)[:20]!r} is not a postal code prefix: use up to {MAX_PREFIX_CHARS} letters or digits."
            )
        if prefix not in out:
            out.append(prefix)
    if len(out) > MAX_POSTAL_PREFIXES:
        raise DeliveryAreaError(f"Give at most {MAX_POSTAL_PREFIXES} postal code prefixes.")
    return out


def stored_postal_prefixes(value: Any) -> tuple[str, ...]:
    """The stored prefixes, read leniently: malformed entries are skipped, at most ``MAX_POSTAL_PREFIXES``."""
    if not isinstance(value, list | tuple):
        return ()
    out: list[str] = []
    for raw in value:
        prefix = compact_postal_code(raw)
        if prefix and _PREFIX.match(prefix) and prefix not in out:
            out.append(prefix)
    return tuple(out[:MAX_POSTAL_PREFIXES])


def in_area(postal_code: Any, prefixes: Sequence[str]) -> bool:
    """True when there is no area, or the postal code starts with one of its prefixes."""
    if not prefixes:
        return True
    code = compact_postal_code(postal_code)
    return bool(code) and any(code.startswith(prefix) for prefix in prefixes)


def describe_area(prefixes: Sequence[str]) -> str:
    """``"postal codes starting with 5600 or 5601"`` (the first few, then how many more)."""
    shown = list(prefixes[:SHOWN_PREFIXES])
    rest = len(prefixes) - len(shown)
    if rest:
        listed = ", ".join(shown) + f" and {rest} more"
    elif len(shown) > 1:
        listed = ", ".join(shown[:-1]) + f" or {shown[-1]}"
    else:
        listed = shown[0] if shown else ""
    return f"postal codes starting with {listed}"


def outside_area_message(postal_code: Any, prefixes: Sequence[str]) -> str:
    code = compact_postal_code(postal_code)[:12]
    return f"This shop delivers only to {describe_area(prefixes)}; {code} is outside it."


def postal_code_needed_message(prefixes: Sequence[str]) -> str:
    return (
        f"This shop delivers only to {describe_area(prefixes)}. "
        "Add the postal code to the delivery address (postal_code)."
    )
