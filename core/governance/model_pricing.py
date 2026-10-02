# SPDX-License-Identifier: Apache-2.0
"""List prices per model, in USD per million tokens, for cost comparison and cost-aware routing.

The table carries the published list price of every catalogue model the
platform can name a price for, the Gemini rows coming from the router's own
pricing table so the two never drift. Models served inside the deployment
(``ollama``, ``vllm``) cost nothing per token here: their cost is the node,
not the call. An Azure deployment is priced as the base model it deploys. A
model nobody can price (``openai_compatible``, an unknown name) has no price:
it is reported as unpriced and ranks last in a cost-aware choice.

A deployment overrides or adds prices with ``AGENTICORG_MODEL_PRICE_OVERRIDES_JSON``,
a JSON object keyed ``provider/model`` with ``input`` and ``output`` per
million tokens, so negotiated rates replace the list.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

LOCAL_PROVIDERS: tuple[str, ...] = ("ollama", "vllm")
# A blended per-token rate weights input three to one against output, the
# platform's observed mix, for ranking and for a call whose split is unknown.
INPUT_SHARE = 0.75

# Published list prices per million tokens; update through the override when a
# provider changes them. The Gemini rows are the router's table.
_OPENAI: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "o1": (15.00, 60.00),
    "o1-mini": (1.10, 4.40),
}
_ANTHROPIC: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
    "claude-sonnet-4-6-20251001": (3.00, 15.00),
    "claude-opus-4-1-20250805": (15.00, 75.00),
    "claude-opus-4-5-20250929": (15.00, 75.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
}


@dataclass(frozen=True)
class Price:
    provider: str
    model: str
    input_per_million: float
    output_per_million: float
    # ``list`` (published), ``override`` (the deployment's), ``local`` (inside the deployment).
    source: str = "list"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def blended_per_million(self) -> float:
        return self.input_per_million * INPUT_SHARE + self.output_per_million * (1 - INPUT_SHARE)

    def cost_usd(self, *, input_tokens: int | None, output_tokens: int | None, tokens: int) -> float:
        """The cost of one call: by the split when it is known, by the blended rate otherwise."""
        if input_tokens is not None and output_tokens is not None:
            return round(
                (input_tokens * self.input_per_million + output_tokens * self.output_per_million) / 1_000_000, 6
            )
        return round(tokens * self.blended_per_million / 1_000_000, 6)


def _overrides() -> dict[str, tuple[float, float]]:
    raw = (settings.model_price_overrides_json or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        out: dict[str, tuple[float, float]] = {}
        for key, value in dict(data).items():
            rates = (float(value["input"]), float(value["output"]))
            if not all(math.isfinite(rate) and rate >= 0 for rate in rates):
                logger.warning("model_price_override_rejected", key=str(key)[:80])
                continue
            out[str(key).strip().lower()] = rates
        return out
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning("model_price_overrides_invalid", error_type=type(exc).__name__)
        return {}


def _list_price(provider: str, model: str) -> tuple[float, float] | None:
    if provider == "gemini":
        from core.llm.router import GEMINI_PRICE_PER_1M

        rates = GEMINI_PRICE_PER_1M.get(model)
        return (float(rates["input"]), float(rates["output"])) if rates else None
    if provider == "openai":
        return _OPENAI.get(model)
    if provider == "anthropic":
        return _ANTHROPIC.get(model)
    if provider == "azure_openai":
        return _OPENAI.get(model.removeprefix("deployment:"))
    return None


def price_for(provider: str | None, model: str) -> Price | None:
    """The price of ``model`` at ``provider``: the deployment's override, else the list, else None."""
    name = (model or "").strip()
    prov = (provider or "").strip().lower()
    if not prov and name.startswith(("ollama:", "vllm:")):
        prov = name.split(":", 1)[0]
    if not name:
        return None
    override = _overrides().get(f"{prov}/{name}".lower())
    if override is not None:
        return Price(prov, name, override[0], override[1], source="override")
    if prov in LOCAL_PROVIDERS:
        return Price(prov, name, 0.0, 0.0, source="local")
    listed = _list_price(prov, name)
    if listed is None:
        return None
    return Price(prov, name, listed[0], listed[1], source="list")


def rank_key(price: Price | None) -> float:
    """Cheapest first; an unpriced model ranks last."""
    return price.blended_per_million if price is not None else float("inf")
