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
    # Invitable / SSO-provisioned roles (api/v1/org.py invite_member,
    # auth/sso/provisioning.py default_role). These carry no fixed domain;
    # get_allowed_domains() derives it from User.domain and otherwise yields
    # [] so the role never resolves to "all domains" (None) by accident.
    "domain_lead": [],
    "analyst": [],
    "developer": [],
}

_USER_DOMAIN_DERIVED_ROLES = frozenset({"domain_lead", "analyst", "developer"})

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
    # Bug sheet 2026-09-14 rows 17-19/22: create and manage the caller's own
    # personal connectors (ownership enforced in core/ownership.py).
    "connectors.personal.write",
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
    "domain_lead": _DOMAIN_ROLE_SCOPES,
    "analyst": [
        "agents:read",
        "workflows:read",
        "approvals:read",
        "connectors.read",
        "report_schedules.read",
    ],
    # Row 52: developers build personal agents in any domain. agents:write
    # and approvals:* let them run, edit, and approve their OWN personal
    # agents; core/ownership.py keeps tenant-agent mutation admin-only and
    # limits developer approval decisions to their own personal agents.
    "developer": [
        "agents:read",
        "agents:write",
        "approvals:read",
        "approvals:write",
        "workflows:read",
        "connectors.read",
        "connectors.contracts.read",
        "connectors.registry.read",
        "connectors.tools.read",
        "connectors.personal.write",
    ],
}

ROLE_LABELS: dict[str, dict[str, str]] = {
    "cfo": {"title": "CFO", "domain_label": "Finance"},
    "chro": {"title": "CHRO", "domain_label": "HR"},
    "cmo": {"title": "CMO", "domain_label": "Marketing"},
    "coo": {"title": "COO", "domain_label": "Operations"},
    "merchant": {"title": "Merchant Operator", "domain_label": "Commerce"},
    "admin": {"title": "CEO / Admin", "domain_label": "All Domains"},
    "auditor": {"title": "Auditor", "domain_label": "Read-only"},
    "domain_lead": {"title": "Domain Lead", "domain_label": "Assigned Domain"},
    "analyst": {"title": "Analyst", "domain_label": "Assigned Domain"},
    "developer": {"title": "Developer", "domain_label": "Assigned Domain"},
}


def get_allowed_domains(role: str, user_domain: str | None = None) -> list[str] | None:
    """Domains a role may access. ``None`` means unrestricted (admin/auditor).

    Fails closed: roles without a fixed domain derive it from the user's
    ``User.domain`` (``[]`` when unset), and unknown roles get ``[]`` rather
    than ``None`` so consumers that treat ``None`` as "all domains" never
    grant tenant-wide visibility to an unmapped role.
    """
    if role in _USER_DOMAIN_DERIVED_ROLES:
        domain = (user_domain or "").strip()
        return [domain] if domain else []
    if role not in ROLE_DOMAIN_MAP:
        return []
    return ROLE_DOMAIN_MAP[role]


def get_scopes_for_role(role: str) -> list[str]:
    return ROLE_SCOPES.get(role, [])
