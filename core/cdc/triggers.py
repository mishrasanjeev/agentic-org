"""CDC trigger evaluation — matches CDC events against configured workflow triggers."""

from __future__ import annotations

from typing import Any

# In-memory trigger registry: list of {connector, event_type, resource_type, workflow_id}
_triggers: list[dict[str, str]] = []


def register_trigger(
    connector: str,
    event_type: str,
    resource_type: str,
    workflow_id: str,
) -> None:
    """Register a trigger rule that maps a CDC event pattern to a workflow."""
    _triggers.append(
        {
            "connector": connector,
            "event_type": event_type,
            "resource_type": resource_type,
            "workflow_id": workflow_id,
        }
    )


def evaluate_triggers(event: dict[str, Any], tenant_id: str) -> list[str]:
    """Match a CDC event against registered triggers and return matching workflow IDs."""
    matched: list[str] = []
    for trigger in _triggers:
        connector_match = trigger["connector"] == "*" or trigger["connector"] == event.get("connector")
        event_type_match = trigger["event_type"] == "*" or trigger["event_type"] == event.get("event_type")
        resource_match = trigger["resource_type"] == "*" or trigger["resource_type"] == event.get("resource_type")

        if connector_match and event_type_match and resource_match:
            matched.append(trigger["workflow_id"])
    return matched


def clear_triggers() -> None:
    """Clear all registered triggers (for testing)."""
    _triggers.clear()
