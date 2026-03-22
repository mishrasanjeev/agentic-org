"""Role-Based Access Control — central policy for domain segregation."""
from __future__ import annotations

ROLE_DOMAIN_MAP: dict[str, list[str] | None] = {
    "cfo": ["finance"],
    "chro": ["hr"],
    "cmo": ["marketing"],
    "coo": ["ops"],
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
]

ROLE_SCOPES: dict[str, list[str]] = {
    "cfo": _DOMAIN_ROLE_SCOPES,
    "chro": _DOMAIN_ROLE_SCOPES,
    "cmo": _DOMAIN_ROLE_SCOPES,
    "coo": _DOMAIN_ROLE_SCOPES,
    "admin": ["agenticorg:admin"],
    "auditor": ["audit:read"],
}

ROLE_LABELS: dict[str, dict[str, str]] = {
    "cfo": {"title": "CFO", "domain_label": "Finance"},
    "chro": {"title": "CHRO", "domain_label": "HR"},
    "cmo": {"title": "CMO", "domain_label": "Marketing"},
    "coo": {"title": "COO", "domain_label": "Operations"},
    "admin": {"title": "CEO / Admin", "domain_label": "All Domains"},
    "auditor": {"title": "Auditor", "domain_label": "Read-only"},
}


def get_allowed_domains(role: str) -> list[str] | None:
    return ROLE_DOMAIN_MAP.get(role)


def get_scopes_for_role(role: str) -> list[str]:
    return ROLE_SCOPES.get(role, [])
