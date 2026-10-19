# SPDX-License-Identifier: Apache-2.0
"""G29: UCP quantities are integer step counts of the sale basis (unit, scale <= 15, increment), bounded at 2^53-1."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from core.commerce.ucp import quantity as q
from core.commerce.ucp.capabilities import DEFAULT_REGISTRY, RegistryContext
from core.commerce.ucp.checkout import EngineDeps, RequestError, evaluate
from core.commerce.ucp.items import ProductEvidenceCatalog
from core.commerce.ucp.negotiation import negotiate
from tests import ucp_schema
from tests import ucp_txn_fixtures as uf

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000a2"))


def test_step_count_bounds() -> None:
    assert q.parse_quantity(1) == 1
    assert q.parse_quantity(2**53 - 1) == 2**53 - 1
    for bad in (0, -1, 2**53, 1.0, 2.5, True, "3", None):
        with pytest.raises(q.QuantityError):
            q.parse_quantity(bad)


def test_unit_rules_scale_each_and_package_codes() -> None:
    assert q.parse_quantity_unit({"unit": "KGM", "scale": 3, "display_text": "kg"}).identity == ("KGM", 3)
    with pytest.raises(q.QuantityError):
        q.parse_quantity_unit({"unit": "KGM", "scale": 16, "display_text": "kg"})
    with pytest.raises(q.QuantityError):
        q.parse_quantity_unit({"unit": "C62", "scale": 1, "display_text": "each"})
    with pytest.raises(q.QuantityError):
        q.parse_quantity_unit({"unit": "XBX", "display_text": "box"})  # Recommendation 21 package code
    with pytest.raises(q.QuantityError):
        q.parse_quantity_unit({"unit": "MTR", "display_text": "m", "increment": 0})
    with pytest.raises(q.QuantityError):
        q.parse_quantity_unit({"unit": "MTR"})


def test_exact_line_amounts_never_float() -> None:
    # 1200 minor units per metre, 15 steps of 0.1 m = 1.5 m.
    assert q.line_amount(1200, 15, 1) == 1800
    # Half to even on a fractional minor unit: 0.5 rounds to 0, 1.5 rounds to 2.
    assert q.line_amount(5, 1, 1) == 0
    assert q.line_amount(15, 1, 1) == 2
    assert q.line_amount(10**15, 2**53 - 1, 15) == 2**53 - 1


def test_asserted_unit_mismatch_converts_visibly_or_rejects() -> None:
    authoritative = q.QuantityUnit("MTR", 1, "metre", 1)
    same = q.reconcile(q.QuantityUnit("MTR", 1, "metre"), authoritative, 15)
    assert same.quantity == 15 and same.warning is None
    finer = q.reconcile(q.QuantityUnit("MTR", 0, "metre"), authoritative, 2)
    assert finer.quantity == 20 and finer.warning["code"] == "quantity_converted"
    coarser = q.reconcile(q.QuantityUnit("MTR", 2, "metre"), authoritative, 150)
    assert coarser.quantity == 15
    with pytest.raises(q.QuantityError):
        q.reconcile(q.QuantityUnit("MTR", 2, "metre"), authoritative, 155)
    with pytest.raises(q.QuantityError):
        q.reconcile(q.QuantityUnit("KGM", 1, "kg"), authoritative, 1)


async def _evaluate(version: str, body: dict[str, Any]):
    merchant = uf.merchant(TENANT)

    async def _fetch(_url: str) -> Any:
        return uf.platform_profile(version, capabilities=["dev.ucp.shopping.checkout"])

    negotiated = await negotiate(
        ucp_agent=f'profile="{uf.PLATFORM_PROFILE}"',
        merchant=merchant,
        registry=DEFAULT_REGISTRY,
        context=RegistryContext(),
        resource="checkout",
        fetcher=_fetch,
        supported_versions=(version,),
    )

    async def _products(_m):
        return uf.products(TENANT)

    return await evaluate(
        resource="checkout",
        merchant=merchant,
        negotiated=negotiated,
        body=body,
        previous=None,
        deps=EngineDeps(catalog=ProductEvidenceCatalog(_products)),
        now=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_fractional_item_priced_in_steps_with_visible_conversion_2026_08_25() -> None:
    body = {
        "line_items": [
            {"item": {"id": "FABRIC-1", "quantity_unit": {"unit": "MTR", "display_text": "metre"}}, "quantity": 2}
        ]
    }
    result = await _evaluate("2026-08-25", body)
    line = result.state["line_items"][0]
    assert line["quantity"] == 20  # 2 m asserted at scale 0 = 20 steps of 0.1 m
    assert line["item"]["quantity_unit"] == {"unit": "MTR", "scale": 1, "display_text": "metre", "increment": 5}
    assert line["totals"][0]["amount"] == 2400
    assert any(m["code"] == "quantity_converted" for m in result.messages)
    ucp_schema.assert_valid("2026-08-25", "shopping/checkout.json", body, request_op="create")


@pytest.mark.asyncio
async def test_increment_is_advisory_warning() -> None:
    result = await _evaluate("2026-08-25", {"line_items": [{"item": {"id": "FABRIC-1"}, "quantity": 7}]})
    assert any(m["code"] == "quantity_increment" and m["type"] == "warning" for m in result.messages)
    assert result.status == "ready_for_complete"


@pytest.mark.asyncio
async def test_quantity_unit_ignored_in_2026_04_08_and_float_quantity_refused() -> None:
    result = await _evaluate(
        "2026-04-08", {"line_items": [{"item": {"id": "FABRIC-1", "quantity_unit": {"unit": "KGM"}}, "quantity": 10}]}
    )
    assert result.state["line_items"][0]["quantity"] == 10
    with pytest.raises(RequestError):
        await _evaluate("2026-08-25", {"line_items": [{"item": {"id": "FABRIC-1"}, "quantity": 1.5}]})
