"""Workflow trigger types."""
from __future__ import annotations
from typing import Any

class WorkflowTrigger:
    def __init__(self, trigger_type: str, config: dict[str, Any] | None = None):
        self.trigger_type = trigger_type
        self.config = config or {}

    def matches(self, event: dict[str, Any]) -> bool:
        if self.trigger_type == "manual":
            return True
        if self.trigger_type == "webhook":
            return True
        if self.trigger_type == "email_received":
            subject = event.get("subject", "")
            filters = self.config.get("filter", {}).get("subject_contains", [])
            return any(f.lower() in subject.lower() for f in filters)
        if self.trigger_type == "api_event":
            return event.get("event_type") == self.config.get("event_type")
        return False
