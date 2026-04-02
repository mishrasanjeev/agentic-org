"""A/B Test Engine -- create, track, auto-select winner, CMO override."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger()


class WinningMetric(StrEnum):
    """Metric used to determine the winning variant."""

    OPEN_RATE = "open_rate"
    CLICK_RATE = "click_rate"
    CONVERSION_RATE = "conversion_rate"


@dataclass
class Variant:
    """A single variant in an A/B test."""

    id: str
    subject: str
    content_html: str
    send_time: str = ""
    audience_segment: str = ""
    metrics: dict[str, int] = field(
        default_factory=lambda: {
            "opens": 0,
            "clicks": 0,
            "conversions": 0,
            "sent": 0,
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "content_html": self.content_html,
            "send_time": self.send_time,
            "audience_segment": self.audience_segment,
            "metrics": self.metrics.copy(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Variant:
        return cls(
            id=data["id"],
            subject=data["subject"],
            content_html=data["content_html"],
            send_time=data.get("send_time", ""),
            audience_segment=data.get("audience_segment", ""),
            metrics=data.get(
                "metrics",
                {"opens": 0, "clicks": 0, "conversions": 0, "sent": 0},
            ),
        )


@dataclass
class ABTest:
    """An A/B test configuration and state."""

    test_id: str
    campaign_id: str
    variants: list[Variant]
    test_size_pct: float = 10.0
    winning_metric: WinningMetric = WinningMetric.OPEN_RATE
    auto_send_winner: bool = True
    auto_wait_hours: int = 24
    state: str = "created"  # created / running / evaluating / finalized
    winner_id: str | None = None
    winner_override: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "campaign_id": self.campaign_id,
            "variants": [v.to_dict() for v in self.variants],
            "test_size_pct": self.test_size_pct,
            "winning_metric": self.winning_metric,
            "auto_send_winner": self.auto_send_winner,
            "auto_wait_hours": self.auto_wait_hours,
            "state": self.state,
            "winner_id": self.winner_id,
            "winner_override": self.winner_override,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ABTest:
        return cls(
            test_id=data["test_id"],
            campaign_id=data["campaign_id"],
            variants=[Variant.from_dict(v) for v in data.get("variants", [])],
            test_size_pct=data.get("test_size_pct", 10.0),
            winning_metric=WinningMetric(data.get("winning_metric", "open_rate")),
            auto_send_winner=data.get("auto_send_winner", True),
            auto_wait_hours=data.get("auto_wait_hours", 24),
            state=data.get("state", "created"),
            winner_id=data.get("winner_id"),
            winner_override=data.get("winner_override"),
            created_at=data.get("created_at", ""),
        )


class ABTestEngine:
    """A/B Test Engine with Redis persistence and in-memory fallback."""

    MIN_SAMPLE_PER_VARIANT = 100
    HIGH_CONFIDENCE_MARGIN = 0.10  # 10% margin for high confidence

    def __init__(self, redis_url: str | None = None):
        self._redis = None
        self._memory_store: dict[str, dict[str, Any]] = {}
        self._redis_url = redis_url
        self._init_redis()

    def _init_redis(self) -> None:
        """Try to connect to Redis; fall back to in-memory store."""
        try:
            import os

            import redis

            url = self._redis_url or os.getenv(
                "AGENTICORG_REDIS_URL", "redis://localhost:6379/1"
            )
            self._redis = redis.from_url(url, decode_responses=True)
            self._redis.ping()
            logger.info("ab_test_engine_redis_connected")
        except Exception as exc:
            logger.warning("ab_test_engine_redis_unavailable", error=str(exc))
            self._redis = None

    def _store_key(self, test_id: str) -> str:
        return f"abtest:{test_id}"

    def _save(self, test: ABTest) -> None:
        data = json.dumps(test.to_dict())
        if self._redis:
            self._redis.set(self._store_key(test.test_id), data)
        else:
            self._memory_store[test.test_id] = test.to_dict()

    def _load(self, test_id: str) -> ABTest | None:
        if self._redis:
            raw = self._redis.get(self._store_key(test_id))
            if not raw:
                return None
            return ABTest.from_dict(json.loads(raw))
        data = self._memory_store.get(test_id)
        if not data:
            return None
        return ABTest.from_dict(data)

    # ── Public API ─────────────────────────────────────────────────

    def create_test(
        self,
        campaign_id: str,
        variants: list[dict[str, Any]],
        test_size_pct: float = 10.0,
        winning_metric: str = "open_rate",
        auto_send_winner: bool = True,
        auto_wait_hours: int = 24,
    ) -> str:
        """Create a new A/B test. Returns test_id."""
        test_id = str(uuid.uuid4())
        variant_objects = []
        for v in variants:
            variant_objects.append(
                Variant(
                    id=v.get("id", str(uuid.uuid4())),
                    subject=v.get("subject", ""),
                    content_html=v.get("content_html", ""),
                    send_time=v.get("send_time", ""),
                    audience_segment=v.get("audience_segment", ""),
                )
            )

        test = ABTest(
            test_id=test_id,
            campaign_id=campaign_id,
            variants=variant_objects,
            test_size_pct=test_size_pct,
            winning_metric=WinningMetric(winning_metric),
            auto_send_winner=auto_send_winner,
            auto_wait_hours=auto_wait_hours,
            state="created",
            created_at=datetime.now(UTC).isoformat(),
        )
        self._save(test)

        # Also maintain a campaign -> test index
        if self._redis:
            self._redis.sadd(f"abtest_campaign:{campaign_id}", test_id)
            self._redis.sadd("abtest_all", test_id)

        logger.info(
            "ab_test_created",
            test_id=test_id,
            campaign_id=campaign_id,
            variants=len(variant_objects),
        )
        return test_id

    def record_metrics(
        self,
        test_id: str,
        variant_id: str,
        opens: int = 0,
        clicks: int = 0,
        conversions: int = 0,
        sent: int = 0,
    ) -> dict[str, Any]:
        """Update variant metrics. Increments existing values."""
        test = self._load(test_id)
        if not test:
            return {"error": "test_not_found"}

        variant = None
        for v in test.variants:
            if v.id == variant_id:
                variant = v
                break

        if not variant:
            return {"error": "variant_not_found"}

        variant.metrics["opens"] = variant.metrics.get("opens", 0) + opens
        variant.metrics["clicks"] = variant.metrics.get("clicks", 0) + clicks
        variant.metrics["conversions"] = (
            variant.metrics.get("conversions", 0) + conversions
        )
        variant.metrics["sent"] = variant.metrics.get("sent", 0) + sent

        if test.state == "created":
            test.state = "running"

        self._save(test)

        logger.info(
            "ab_test_metrics_recorded",
            test_id=test_id,
            variant_id=variant_id,
            metrics=variant.metrics,
        )
        return {"status": "updated", "metrics": variant.metrics}

    def check_winner(self, test_id: str) -> dict[str, Any] | None:
        """Check if a winner can be determined.

        Returns {winner_variant_id, confidence, should_auto_send} or None
        if minimum sample not reached.
        Min sample: 100 per variant. Winner: highest metric value.
        Confidence: simple percentage difference (>10% margin = high confidence).
        """
        test = self._load(test_id)
        if not test:
            return None

        # Check minimum sample size
        for v in test.variants:
            if v.metrics.get("sent", 0) < self.MIN_SAMPLE_PER_VARIANT:
                logger.info(
                    "ab_test_min_sample_not_reached",
                    test_id=test_id,
                    variant_id=v.id,
                    sent=v.metrics.get("sent", 0),
                )
                return None

        # Calculate rate for each variant based on winning metric
        rates: list[tuple[str, float]] = []
        for v in test.variants:
            sent = v.metrics.get("sent", 1)  # avoid division by zero
            if test.winning_metric == WinningMetric.OPEN_RATE:
                rate = v.metrics.get("opens", 0) / sent
            elif test.winning_metric == WinningMetric.CLICK_RATE:
                rate = v.metrics.get("clicks", 0) / sent
            elif test.winning_metric == WinningMetric.CONVERSION_RATE:
                rate = v.metrics.get("conversions", 0) / sent
            else:
                rate = v.metrics.get("opens", 0) / sent
            rates.append((v.id, rate))

        # Sort by rate descending
        rates.sort(key=lambda x: x[1], reverse=True)
        winner_id, winner_rate = rates[0]
        runner_up_rate = rates[1][1] if len(rates) > 1 else 0.0

        # Calculate confidence based on margin
        if runner_up_rate > 0:
            margin = (winner_rate - runner_up_rate) / runner_up_rate
        else:
            margin = 1.0 if winner_rate > 0 else 0.0

        confidence = "high" if margin >= self.HIGH_CONFIDENCE_MARGIN else "low"

        # Update test state
        test.state = "evaluating"
        test.winner_id = winner_id
        self._save(test)

        result = {
            "winner_variant_id": winner_id,
            "winner_rate": round(winner_rate, 4),
            "runner_up_rate": round(runner_up_rate, 4),
            "margin": round(margin, 4),
            "confidence": confidence,
            "should_auto_send": test.auto_send_winner and confidence == "high",
        }

        logger.info("ab_test_winner_checked", test_id=test_id, **result)
        return result

    def finalize_test(
        self, test_id: str, winner_override: str | None = None
    ) -> dict[str, Any]:
        """Finalize the test. If winner_override provided (CMO picked), use that."""
        test = self._load(test_id)
        if not test:
            return {"error": "test_not_found"}

        if winner_override:
            # Validate override is a valid variant
            valid_ids = {v.id for v in test.variants}
            if winner_override not in valid_ids:
                return {"error": "invalid_variant_id", "valid_ids": list(valid_ids)}
            test.winner_id = winner_override
            test.winner_override = winner_override

        test.state = "finalized"
        self._save(test)

        logger.info(
            "ab_test_finalized",
            test_id=test_id,
            winner_id=test.winner_id,
            override=winner_override is not None,
        )
        return {
            "status": "finalized",
            "test_id": test_id,
            "winner_id": test.winner_id,
            "was_override": winner_override is not None,
        }

    def get_results(self, test_id: str) -> dict[str, Any] | None:
        """Get full test data with per-variant metrics."""
        test = self._load(test_id)
        if not test:
            return None
        return test.to_dict()

    def list_tests(self, campaign_id: str | None = None) -> list[dict[str, Any]]:
        """List all tests, optionally filtered by campaign_id."""
        results: list[dict[str, Any]] = []

        if self._redis:
            if campaign_id:
                test_ids = self._redis.smembers(f"abtest_campaign:{campaign_id}")
            else:
                test_ids = self._redis.smembers("abtest_all")

            for tid in test_ids:
                test = self._load(tid)
                if test:
                    results.append(test.to_dict())
        else:
            for _tid, data in self._memory_store.items():
                if campaign_id and data.get("campaign_id") != campaign_id:
                    continue
                results.append(data)

        return results
