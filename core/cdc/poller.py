"""CDC poller — background polling for connectors.

Supports real polling for registered connectors (HubSpot, etc.)
via the connector registry pattern.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()

# Registry of connector-specific poller functions
_POLLERS: dict[str, Any] = {}


def register_poller(connector_name: str, poller_fn: Any) -> None:
    """Register a polling function for a connector."""
    _POLLERS[connector_name] = poller_fn
    logger.info("cdc_poller_registered", connector=connector_name)


async def poll_connector(
    connector_name: str,
    last_sync_at: str,
) -> list[dict[str, Any]]:
    """Poll a connector for new/updated records since *last_sync_at*.

    Uses a watermark strategy: only records with updated_at > last_sync_at
    are returned.
    """
    try:
        watermark = datetime.fromisoformat(last_sync_at)
    except (ValueError, TypeError):
        watermark = datetime.min.replace(tzinfo=UTC)

    poller_fn = _POLLERS.get(connector_name)
    if poller_fn is None:
        return []

    try:
        raw_records = await poller_fn(since=watermark)
        events = []
        for rec in raw_records:
            events.append({
                "connector": connector_name,
                "event_type": rec.get("event_type", "record_updated"),
                "resource_type": rec.get("type", "unknown"),
                "resource_id": str(rec.get("id", "")),
                "payload": rec,
                "polled_at": datetime.now(UTC).isoformat(),
            })
        logger.info(
            "cdc_poll_complete",
            connector=connector_name,
            events=len(events),
        )
        return events
    except Exception:
        logger.exception("cdc_poll_failed", connector=connector_name)
        return []


# ── HubSpot Poller ──────────────────────────────────────────────────


async def _hubspot_poll(since: datetime) -> list[dict[str, Any]]:
    """Poll HubSpot for contacts, deals, and companies updated since watermark.

    Uses HubSpot's Search API with lastmodifieddate filter.
    Requires HUBSPOT_ACCESS_TOKEN env var.
    """
    import httpx

    token = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    if not token:
        logger.warning("hubspot_cdc_no_token")
        return []

    base = "https://api.hubapi.com"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    since_ms = int(since.timestamp() * 1000)

    records: list[dict[str, Any]] = []

    # Poll contacts, deals, and companies
    for obj_type in ("contacts", "deals", "companies"):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{base}/crm/v3/objects/{obj_type}/search",
                    headers=headers,
                    json={
                        "filterGroups": [{
                            "filters": [{
                                "propertyName": "lastmodifieddate",
                                "operator": "GTE",
                                "value": str(since_ms),
                            }],
                        }],
                        "sorts": [{"propertyName": "lastmodifieddate", "direction": "DESCENDING"}],
                        "limit": 100,
                        "properties": _HUBSPOT_PROPERTIES.get(obj_type, []),
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                for result in data.get("results", []):
                    props = result.get("properties", {})
                    records.append({
                        "id": result.get("id"),
                        "type": obj_type.rstrip("s"),  # "contact", "deal", "company"
                        "event_type": "record_updated",
                        "properties": props,
                        "updated_at": props.get("lastmodifieddate", ""),
                    })

        except Exception:
            logger.exception("hubspot_cdc_poll_error", object_type=obj_type)

    logger.info("hubspot_cdc_polled", records=len(records))
    return records


_HUBSPOT_PROPERTIES: dict[str, list[str]] = {
    "contacts": [
        "firstname", "lastname", "email", "phone", "company",
        "lifecyclestage", "lastmodifieddate", "createdate",
    ],
    "deals": [
        "dealname", "amount", "dealstage", "pipeline", "closedate",
        "hubspot_owner_id", "lastmodifieddate", "createdate",
    ],
    "companies": [
        "name", "domain", "industry", "numberofemployees", "annualrevenue",
        "city", "state", "country", "lastmodifieddate", "createdate",
    ],
}


# Auto-register HubSpot poller if token is available
if os.getenv("HUBSPOT_ACCESS_TOKEN"):
    register_poller("hubspot", _hubspot_poll)
