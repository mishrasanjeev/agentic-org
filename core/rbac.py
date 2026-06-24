"""Role-Based Access Control — central policy for domain segregation."""
from __future__ import annotations

# enterprise-gate: process-local-ok reason=static-role-domain-policy-map
ROLE_DOMAIN_MAP: dict[str, list[str] | None] = {
    "cfo": ["finance"],
    "chro": ["hr"],
    "cmo": ["marketing"],
    "coo": ["ops"],
    "merchant": ["commerce"],
    "admin": None,
    "auditor": None,
}

_DOMAIN_ROLE_SCOPES = [
    "agents:read",
    "agents:write",
    "workflows:read",
    "workflows:write",
    "approvals:read",
    "approvals:write",
    "audit:read",
    "connectors.read",
    "connectors.contracts.read",
    "connectors.registry.read",
    "connectors.tools.read",
    "report_schedules.read",
    "report_schedules.write",
    "report_schedules.run",
]

ROLE_SCOPES: dict[str, list[str]] = {
    "cfo": _DOMAIN_ROLE_SCOPES,
    "chro": _DOMAIN_ROLE_SCOPES,
    "cmo": [
        *_DOMAIN_ROLE_SCOPES,
        "connectors.cmo_vendor_sandbox.write",
    ],
    "coo": _DOMAIN_ROLE_SCOPES,
    "merchant": ["commerce.merchant_config.write"],
    "admin": ["agenticorg:admin"],
    "auditor": ["audit:read"],
}

ROLE_LABELS: dict[str, dict[str, str]] = {
    "cfo": {"title": "CFO", "domain_label": "Finance"},
    "chro": {"title": "CHRO", "domain_label": "HR"},
    "cmo": {"title": "CMO", "domain_label": "Marketing"},
    "coo": {"title": "COO", "domain_label": "Operations"},
    "merchant": {"title": "Merchant Operator", "domain_label": "Commerce"},
    "admin": {"title": "CEO / Admin", "domain_label": "All Domains"},
    "auditor": {"title": "Auditor", "domain_label": "Read-only"},
}


def get_allowed_domains(role: str) -> list[str] | None:
    return ROLE_DOMAIN_MAP.get(role)


def get_scopes_for_role(role: str) -> list[str]:
    return ROLE_SCOPES.get(role, [])
