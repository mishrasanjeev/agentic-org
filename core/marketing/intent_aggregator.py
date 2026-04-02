"""Intent data aggregation across Bombora + G2 + TrustRadius.

Provides a unified interface to query all three intent data providers
in parallel, normalize scores to a 0-100 scale, and produce a single
composite intent score with configurable weights for ABM workflows.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from connectors.marketing.bombora import BomboraConnector
from connectors.marketing.g2 import G2Connector
from connectors.marketing.trustradius import TrustRadiusConnector

logger = structlog.get_logger()

# ── Scoring weights ────────────────────────────────────────────────────
WEIGHT_BOMBORA = 0.40
WEIGHT_G2 = 0.30
WEIGHT_TRUSTRADIUS = 0.30

# Maximum batch size for parallel domain processing
BATCH_SIZE = 100


class IntentAggregator:
    """Aggregate intent signals from Bombora, G2, and TrustRadius.

    Usage::

        agg = IntentAggregator()
        score = await agg.aggregate_intent(
            domain="acme.com",
            bombora_config={"api_key": "..."},
            g2_config={"api_key": "..."},
            trustradius_config={"api_key": "..."},
        )
        print(score["composite_score"])  # 0-100

    Weights (default):
        - Bombora:     40 %
        - G2:          30 %
        - TrustRadius: 30 %
    """

    # ── Single-domain aggregation ──────────────────────────────────────

    async def aggregate_intent(
        self,
        domain: str,
        bombora_config: dict[str, Any] | None = None,
        g2_config: dict[str, Any] | None = None,
        trustradius_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Query all three providers in parallel and return a composite score.

        Returns an ``IntentScore`` dict::

            {
                "domain": str,
                "composite_score": float,   # 0-100, weighted
                "bombora_surge": float,     # 0-100
                "g2_signals": float,        # 0-100
                "trustradius_intent": float, # 0-100
                "topics": list[str],
                "last_updated": str,        # ISO-8601
            }
        """
        bombora_task = self._fetch_bombora(domain, bombora_config or {})
        g2_task = self._fetch_g2(domain, g2_config or {})
        tr_task = self._fetch_trustradius(domain, trustradius_config or {})

        bombora_result, g2_result, tr_result = await asyncio.gather(
            bombora_task, g2_task, tr_task, return_exceptions=True
        )

        bombora_score = self._extract_score(bombora_result, "bombora")
        g2_score = self._extract_score(g2_result, "g2")
        tr_score = self._extract_score(tr_result, "trustradius")

        composite = (
            bombora_score * WEIGHT_BOMBORA
            + g2_score * WEIGHT_G2
            + tr_score * WEIGHT_TRUSTRADIUS
        )

        topics = self._merge_topics(bombora_result, g2_result, tr_result)

        return {
            "domain": domain,
            "composite_score": round(composite, 2),
            "bombora_surge": round(bombora_score, 2),
            "g2_signals": round(g2_score, 2),
            "trustradius_intent": round(tr_score, 2),
            "topics": topics,
            "last_updated": datetime.now(UTC).isoformat(),
        }

    # ── Batch aggregation ──────────────────────────────────────────────

    async def batch_aggregate(
        self,
        domains: list[str],
        configs: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Aggregate intent for many domains in parallel batches.

        ``configs`` keys: ``bombora``, ``g2``, ``trustradius`` -- each
        containing the connector config dict.

        Processes up to ``BATCH_SIZE`` (100) domains concurrently to
        avoid overwhelming upstream APIs.
        """
        bombora_cfg = configs.get("bombora", {})
        g2_cfg = configs.get("g2", {})
        tr_cfg = configs.get("trustradius", {})

        results: list[dict[str, Any]] = []
        for i in range(0, len(domains), BATCH_SIZE):
            batch = domains[i : i + BATCH_SIZE]
            tasks = [
                self.aggregate_intent(d, bombora_cfg, g2_cfg, tr_cfg)
                for d in batch
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for domain, result in zip(batch, batch_results, strict=False):
                if isinstance(result, Exception):
                    logger.error("intent_batch_error", domain=domain, error=str(result))
                    results.append(self._empty_score(domain, str(result)))
                else:
                    results.append(result)
        return results

    # ── Provider fetch helpers ─────────────────────────────────────────

    async def _fetch_bombora(
        self, domain: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Fetch surge scores from Bombora for a single domain."""
        connector = BomboraConnector(config)
        try:
            await connector.connect()
            return await connector.get_surge_scores(domains=domain)
        except Exception as exc:
            logger.warning("bombora_fetch_error", domain=domain, error=str(exc))
            return {"error": str(exc)}
        finally:
            await connector.disconnect()

    async def _fetch_g2(
        self, domain: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Fetch intent signals from G2 for a single domain."""
        connector = G2Connector(config)
        try:
            await connector.connect()
            return await connector.get_intent_signals(domain=domain)
        except Exception as exc:
            logger.warning("g2_fetch_error", domain=domain, error=str(exc))
            return {"error": str(exc)}
        finally:
            await connector.disconnect()

    async def _fetch_trustradius(
        self, domain: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Fetch buyer intent from TrustRadius for a single domain."""
        connector = TrustRadiusConnector(config)
        try:
            await connector.connect()
            return await connector.get_buyer_intent(vendor_id=domain)
        except Exception as exc:
            logger.warning("trustradius_fetch_error", domain=domain, error=str(exc))
            return {"error": str(exc)}
        finally:
            await connector.disconnect()

    # ── Score extraction & normalization ────────────────────────────────

    @staticmethod
    def _extract_score(result: Any, provider: str) -> float:
        """Extract a 0-100 intent score from a provider response.

        Falls back to 0 if the call failed or the response is unexpected.
        """
        if isinstance(result, Exception):
            logger.debug("intent_score_exception", provider=provider, error=str(result))
            return 0.0

        if not isinstance(result, dict):
            return 0.0

        if "error" in result:
            return 0.0

        # Bombora: look for a surge score in the first company entry
        if provider == "bombora":
            companies = result.get("companies", [])
            if companies and isinstance(companies[0], dict):
                return float(companies[0].get("surge_score", 0))
            return 0.0

        # G2: direct intent_score field
        if provider == "g2":
            return float(result.get("intent_score", 0))

        # TrustRadius: direct intent_score field
        if provider == "trustradius":
            return float(result.get("intent_score", 0))

        return 0.0

    @staticmethod
    def _merge_topics(
        bombora_result: Any, g2_result: Any, tr_result: Any
    ) -> list[str]:
        """Merge topic labels from all three providers, de-duplicated."""
        topics: set[str] = set()

        for result in (bombora_result, g2_result, tr_result):
            if isinstance(result, Exception) or not isinstance(result, dict):
                continue
            # Bombora topics
            for company in result.get("companies", []):
                if isinstance(company, dict):
                    for t in company.get("topics", []):
                        if isinstance(t, dict):
                            topics.add(t.get("name", t.get("topic_name", "")))
                        elif isinstance(t, str):
                            topics.add(t)
            # G2 / TR signals may include topic labels
            for signal in result.get("signals", []):
                if isinstance(signal, dict) and signal.get("topic"):
                    topics.add(signal["topic"])
            for activity in result.get("activity", []):
                if isinstance(activity, dict) and activity.get("topic"):
                    topics.add(activity["topic"])

        topics.discard("")
        return sorted(topics)

    @staticmethod
    def _empty_score(domain: str, error: str) -> dict[str, Any]:
        """Return a zeroed-out IntentScore dict with an error note."""
        return {
            "domain": domain,
            "composite_score": 0.0,
            "bombora_surge": 0.0,
            "g2_signals": 0.0,
            "trustradius_intent": 0.0,
            "topics": [],
            "last_updated": datetime.now(UTC).isoformat(),
            "error": error,
        }
