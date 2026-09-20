# SPDX-License-Identifier: Apache-2.0

"""TypeSafe Jev adapter using the documented System One HTTP contract."""

from __future__ import annotations

import time
from collections.abc import Mapping
from math import isfinite

import httpx

from core.decisioning.contracts import (
    DecisionAnswer,
    DecisionProviderError,
    DecisionRequest,
    DecisionResponse,
)


class JevDecisionProvider:
    """Call Jev for bounded typed decisions.

    This adapter is deliberately not an authorization layer. Callers must
    continue to enforce authentication, tenant boundaries, policy, consent,
    and tool permissions deterministically after receiving a response.
    """

    provider_name = "jev"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.typesafe.ai",
        model: str = "jev-latest",
        timeout_seconds: float = 0.8,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Jev API key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("Jev timeout must be positive")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._model = model.strip() or "jev-latest"
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def decide(self, request: DecisionRequest) -> DecisionResponse:
        payload = request.to_payload()
        payload["model"] = request.model.strip() or self._model
        started = time.perf_counter()
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout_seconds)
        try:
            response = await client.post(
                f"{self._base_url}/v1/systemone",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DecisionProviderError(
                f"Jev decision request failed ({type(exc).__name__})"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        if not isinstance(body, Mapping) or not isinstance(body.get("answers"), Mapping):
            raise DecisionProviderError("Jev returned an invalid decision response")

        answers: dict[str, DecisionAnswer] = {}
        for name, raw in body["answers"].items():
            if not isinstance(name, str) or not isinstance(raw, Mapping):
                raise DecisionProviderError("Jev returned an invalid typed answer")
            answer_type = raw.get("type")
            if answer_type not in {"choice", "score", "noul"}:
                raise DecisionProviderError("Jev returned an unsupported answer type")
            value_key = {
                "choice": "choice",
                "score": "score",
                "noul": "noul",
            }[answer_type]
            if value_key not in raw:
                raise DecisionProviderError("Jev returned an incomplete typed answer")
            confidence = raw.get("confidence")
            if confidence is not None:
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError) as exc:
                    raise DecisionProviderError("Jev returned invalid confidence") from exc
                if not isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                    raise DecisionProviderError("Jev returned out-of-range confidence")
            probabilities = raw.get("probabilities", {})
            if not isinstance(probabilities, Mapping):
                probabilities = {}
            normalized_probabilities: dict[str, float] = {}
            for key, value in probabilities.items():
                if isinstance(value, bool):
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue
                if isfinite(numeric_value):
                    normalized_probabilities[str(key)] = numeric_value
            answers[name] = DecisionAnswer(
                type=answer_type,
                value=raw[value_key],
                confidence=confidence,
                probabilities=normalized_probabilities,
            )

        usage = body.get("usage", {})
        if not isinstance(usage, Mapping):
            usage = {}
        return DecisionResponse(
            provider=self.provider_name,
            model=str(body.get("model", payload["model"])),
            answers=answers,
            usage={
                str(key): int(value)
                for key, value in usage.items()
                if isinstance(value, int)
            },
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
