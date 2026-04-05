"""CDC poller — background polling for connectors without webhook support."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


async def poll_connector(
    connector_name: str,
    last_sync_at: str,
) -> list[dict[str, Any]]:
    """Poll a connector for new/updated records since *last_sync_at*.

    Uses a watermark strategy: only records with updated_at > last_sync_at
    are returned.  Each record is normalized into the standard CDC event
    format so downstream consumers can treat polled and webhook events
    identically.

    Returns:
        List of CDC-normalized event dicts.
    """
    try:
        watermark = datetime.fromisoformat(last_sync_at)
    except (ValueError, TypeError):
        watermark = datetime.min.replace(tzinfo=UTC)

    # In production this would call the connector SDK / API.
    # For now, return an empty list — real connectors will be plugged in
    # via a registry pattern.
    new_records: list[dict[str, Any]] = []

    # Stub: simulate connector polling via registry lookup
    try:
        from core.cdc._connector_registry import get_poller_fn

        poller_fn = get_poller_fn(connector_name)
        if poller_fn is not None:
            raw_records = await poller_fn(since=watermark)
            for rec in raw_records:
                new_records.append(
                    {
                        "connector": connector_name,
                        "event_type": "record_updated",
                        "resource_type": rec.get("type", "unknown"),
                        "resource_id": str(rec.get("id", "")),
                        "payload": rec,
                        "polled_at": datetime.now(UTC).isoformat(),
                    }
                )
    except (ImportError, ModuleNotFoundError):
        # Registry not yet implemented — return empty for now
        pass

    return new_records
