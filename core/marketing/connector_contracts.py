"""Marketing connector readiness contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HUBSPOT_CRM_READ_SCOPES = (
    "crm.objects.contacts.read",
    "crm.objects.deals.read",
)
HUBSPOT_OPTIONAL_SCOPES = ("automation",)
HUBSPOT_CRM_READ_TOOLS = ("list_contacts", "search_contacts", "list_deals")


@dataclass(frozen=True)
class ConnectorContract:
    key: str
    provider: str
    label: str
    status: str
    required_scopes: tuple[str, ...]
    missing_scopes: tuple[str, ...]
    optional_scopes: tuple[str, ...] = ()
    non_blocking_scope_gaps: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    missing_tools: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "provider": self.provider,
            "label": self.label,
            "status": self.status,
            "required_scopes": list(self.required_scopes),
            "missing_scopes": list(self.missing_scopes),
            "optional_scopes": list(self.optional_scopes),
            "non_blocking_scope_gaps": list(self.non_blocking_scope_gaps),
            "required_tools": list(self.required_tools),
            "missing_tools": list(self.missing_tools),
            "evidence": list(self.evidence),
            "reason": self.reason,
        }


def _normalise_scopes(*values: Any) -> set[str]:
    scopes: set[str] = set()
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            parts = value.replace(",", " ").split()
        elif isinstance(value, (list, tuple, set)):
            parts = [str(item) for item in value]
        else:
            continue
        scopes.update(part.strip() for part in parts if part and part.strip())
    return scopes


def evaluate_hubspot_crm_read_contract(
    *,
    connector_status: str | None,
    health_status: str | None,
    tool_functions: list[Any] | tuple[Any, ...] | None,
    credentials: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> ConnectorContract:
    """Evaluate the HubSpot CRM read contract.

    OAuth scope strings are useful evidence, but HubSpot private-app tokens
    and older connector configs do not always persist an OAuth ``scope`` field.
    A healthy HubSpot connector with registered CRM read tools has already
    proved read capability, so absent scope strings must not block CMO
    readiness.
    """
    raw_tools = tool_functions or []
    tools = {
        str(tool.get("name") if isinstance(tool, dict) else tool)
        for tool in raw_tools
    }
    missing_tools = tuple(
        tool for tool in HUBSPOT_CRM_READ_TOOLS if tool not in tools
    )
    scopes = _normalise_scopes(
        (credentials or {}).get("scope"),
        (credentials or {}).get("scopes"),
        (config or {}).get("scope"),
        (config or {}).get("scopes"),
    )
    missing_required_scopes = tuple(
        scope for scope in HUBSPOT_CRM_READ_SCOPES if scope not in scopes
    )
    missing_optional = tuple(
        scope for scope in HUBSPOT_OPTIONAL_SCOPES if scope not in scopes
    )
    active = str(connector_status or "").lower() == "active"
    healthy = str(health_status or "").lower() == "healthy"
    evidence: list[str] = []
    if active:
        evidence.append("connector active")
    if healthy:
        evidence.append("health check healthy")
    if not missing_tools:
        evidence.append("HubSpot CRM read tools registered")
    if scopes:
        evidence.append("stored OAuth scope evidence")
    else:
        evidence.append("private app or legacy OAuth config without persisted scope string")

    if not active or not healthy:
        return ConnectorContract(
            key="hubspot_crm_read",
            provider="hubspot",
            label="HubSpot CRM Read",
            status="not_ready",
            required_scopes=HUBSPOT_CRM_READ_SCOPES,
            missing_scopes=missing_required_scopes,
            optional_scopes=HUBSPOT_OPTIONAL_SCOPES,
            non_blocking_scope_gaps=missing_optional,
            required_tools=HUBSPOT_CRM_READ_TOOLS,
            missing_tools=missing_tools,
            evidence=tuple(evidence),
            reason="HubSpot connector must be active and healthy.",
        )

    if missing_tools:
        return ConnectorContract(
            key="hubspot_crm_read",
            provider="hubspot",
            label="HubSpot CRM Read",
            status="not_ready",
            required_scopes=HUBSPOT_CRM_READ_SCOPES,
            missing_scopes=missing_required_scopes,
            optional_scopes=HUBSPOT_OPTIONAL_SCOPES,
            non_blocking_scope_gaps=missing_optional,
            required_tools=HUBSPOT_CRM_READ_TOOLS,
            missing_tools=missing_tools,
            evidence=tuple(evidence),
            reason="HubSpot connector is missing required CRM read tools.",
        )

    return ConnectorContract(
        key="hubspot_crm_read",
        provider="hubspot",
        label="HubSpot CRM Read",
        status="ready",
        required_scopes=HUBSPOT_CRM_READ_SCOPES,
        missing_scopes=(),
        optional_scopes=HUBSPOT_OPTIONAL_SCOPES,
        non_blocking_scope_gaps=missing_optional,
        required_tools=HUBSPOT_CRM_READ_TOOLS,
        missing_tools=(),
        evidence=tuple(evidence),
        reason=(
            "CRM read capability is available from the healthy HubSpot "
            "connector and registered tools."
        ),
    )
