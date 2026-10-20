# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Agentic Commerce Protocol (ACP, API version ``2026-04-17``) surfaces.

* ``feed``: the product feed (ACP Feed ``Product`` JSON and the flat CSV with
  the merchant-feed eligibility, seller and returns columns), generated only
  from verified, fresh OACP artifacts.
* ``checkout``: ACP checkout sessions over the UCP session table and state
  engine (``core.commerce.ucp``): request translation and the ACP renderer.
* ``headers``: ``API-Version``, ``Idempotency-Key``, ``Request-Id``,
  ``Signature`` and ``Timestamp`` handling.
* ``errors``: internal message codes and protocol errors mapped to ACP
  ``Error`` and ``MessageError`` objects (blocker dicts never leave the API).
* ``capabilities``: capability negotiation, extensions and payment handlers.
* ``delegate_payment``: the Grantex delegate-payment allowance per session.
* ``orders``: ACP ``order_create`` / ``order_update`` webhook events signed
  with ``Merchant-Signature``.
"""

from __future__ import annotations

API_VERSION = "2026-04-17"
SUPPORTED_API_VERSIONS: tuple[str, ...] = (API_VERSION,)
