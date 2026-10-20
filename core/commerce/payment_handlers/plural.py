# SPDX-License-Identifier: Apache-2.0
"""Plural tokenised instrument: the merchant's Plural rail as a UCP payment handler.

Offered when the merchant has an enabled ``plural_pine`` payment provider and a
``provider_policy.ucp.mandate_capability`` (the rails, the per-payment cap and
the instrument types the merchant's Plural configuration accepts for
agent-initiated payments). The handler config carries that capability and the
allowance (``max_amount`` = ``mandate_capability.max_amount_minor_units``):
a completion above the allowance, in another currency, or with a credential
the handler does not take, is refused before any decision or executor runs.

Accepted credentials: a network token with its cryptogram, or a Plural
instrument token (handler-defined ``plural_pine_token`` with ``token``); a
raw PAN is never accepted, so card numbers stay out of AgenticOrg. In
2026-04-08 the network token arrives as the ``card`` credential with
``card_number_type: network_token``.

This handler executes nothing. The Plural executor (``core/commerce/executors``)
moves money, and only on a consumed ``allow`` decision.
"""

from __future__ import annotations

from typing import Any

from core.commerce.payment_handlers import Allowance, register_payment_handler
from core.commerce.ucp import releases as v
from core.commerce.ucp.capabilities import PaymentHandlerEntry
from core.commerce.ucp.merchant import MerchantUcp

HANDLER_NAME = "ai.agenticorg.payment.plural_tokenized"
HANDLER_ID = "plural_tokenized"
HANDLER_VERSION = "2026-10-20"
# The handler's config schema, authority-bound to the handler name (agenticorg.ai -> ai.agenticorg).
HANDLER_SCHEMA = "https://agenticorg.ai/ucp/handlers/plural-tokenized/2026-10-20/config.json"
# The credential an agent presents for this handler (ACP PaymentHandler.instrument_schemas).
# Both files are served from ui/public on agenticorg.ai (tests map each URL to its file).
INSTRUMENT_SCHEMA = "https://agenticorg.ai/ucp/handlers/plural-tokenized/2026-10-20/instrument.json"
TOKEN_CREDENTIAL = "plural_pine_token"


class PluralTokenizedInstrumentHandler:
    name = HANDLER_NAME

    def _provider(self, merchant: MerchantUcp) -> dict[str, Any] | None:
        for provider in merchant.payment_providers:
            if provider.get("provider_type") == "plural_pine" and provider.get("enabled") is True:
                return provider
        return None

    def entries(self, merchant: MerchantUcp, version: str) -> list[PaymentHandlerEntry]:
        provider = self._provider(merchant)
        capability = merchant.mandate_capability
        if provider is None or capability is None:
            return []
        types = capability.instrument_types or ("card",)
        allowance = self.allowance(merchant)
        config: dict[str, Any] = {
            "provider": "plural_pine",
            "environment": str(provider.get("environment") or "sandbox"),
            "mandate_capability": capability.to_dict(),
            "accepted_credentials": list(self.accepted_credentials(version)),
        }
        if allowance.max_amount_minor_units is not None and allowance.currency:
            config["allowance"] = {"max_amount": allowance.max_amount_minor_units, "currency": allowance.currency}
        return [
            PaymentHandlerEntry(
                name=HANDLER_NAME,
                id=HANDLER_ID,
                version=HANDLER_VERSION,
                schema=HANDLER_SCHEMA,
                spec=None,
                available_instruments=tuple({"type": t} for t in types),
                config=config,
            )
        ]

    def accepted_credentials(self, version: str) -> tuple[str, ...]:
        if version >= v.V2026_08_25:
            return ("network_token", TOKEN_CREDENTIAL)
        return ("card", TOKEN_CREDENTIAL)

    def allowance(self, merchant: MerchantUcp) -> Allowance:
        capability = merchant.mandate_capability
        if capability is None:
            return Allowance(max_amount_minor_units=None, currency=None)
        return Allowance(max_amount_minor_units=capability.max_amount_minor_units, currency=capability.currency)

    def request_constraints(
        self, entry: PaymentHandlerEntry, instrument_type: str, version: str
    ) -> dict[str, Any] | None:
        if version < v.V2026_08_25:
            return None
        return {
            "path": (
                f"$['payment']['instruments'][?@['handler_id'] == '{entry.id}' && @['type'] == '{instrument_type}']"
            ),
            "required": ["credential"],
            "properties": {
                "credential": {
                    "anyOf": [
                        {"properties": {"type": {"const": "network_token"}}, "required": ["cryptogram"]},
                        {"properties": {"type": {"const": TOKEN_CREDENTIAL}}, "required": ["token"]},
                    ]
                }
            },
        }


register_payment_handler(PluralTokenizedInstrumentHandler())
