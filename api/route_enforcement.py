"""Runtime enforcement for ``@route_meta`` annotations.

Until 2026-09-13 ``route_meta(scope=..., rate_limit=...)`` was metadata only:
nothing read it at request time, so the annotations implied controls that did
not exist (audit finding C13). This module turns the two security-relevant
fields into real checks, applied as a global FastAPI dependency:

* ``rate_limit`` — a named class mapped to ``(limit, window_seconds)`` in
  :data:`RATE_LIMIT_CLASSES`, counted per tenant (or per client IP for public
  and pre-auth routes) via the Redis-backed ``core.auth_state.check_window_rate``.
* ``scope`` — declared route scopes are grouped into families
  (:data:`SCOPE_FAMILIES`) that map onto the RBAC scopes ``core.rbac.ROLE_SCOPES``
  hands to roles. GET/HEAD/OPTIONS require the family's read scope, everything
  else the write scope. ``agenticorg:admin`` satisfies every family.

Scope enforcement applies only to user sessions and API keys (``auth_mode``
``legacy``/``api_key``). Grantex agent tokens carry tool scopes, which the
tool gateway enforces; RBAC families do not apply to them.

Families that are not mapped are NOT enforced — they are reported by
:func:`unmapped_scope_families` and pinned by a unit test so the gap is
visible instead of silent.

``AGENTICORG_ROUTE_ENFORCEMENT_MODE=log`` turns denials into warnings for a
staged rollout; the default is ``enforce``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

from api.client_ip import client_ip as resolve_client_ip
from api.route_metadata import ROUTE_METADATA_ATTR
from core.config import settings

logger = logging.getLogger("agenticorg.route_enforcement")

ADMIN_SCOPE = "agenticorg:admin"
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Named rate-limit class -> (requests, window seconds). Keyed per tenant for
# authenticated routes and per client IP for public / pre-auth routes.
# Unknown classes fall back to ``_DEFAULT_RATE``.
RATE_LIMIT_CLASSES: dict[str, tuple[int, int]] = {
    "standard": (600, 60),
    "public-read": (120, 60),
    "public-evals-read": (60, 60),
    "public-status-read": (60, 60),
    "public-product-facts": (60, 60),
    "demo-request-public": (5, 3600),
    "client-portal-public": (60, 60),
    "branding-public-lookup": (120, 60),
    "push-public-config": (60, 60),
    "healthcheck": (600, 60),
    "infra-health-probe": (600, 60),
    # Per client IP. Offices/NAT and CI suites share an IP; credential
    # stuffing is separately bounded by the failure-based login throttle.
    "auth-login": (120, 60),
    "auth-signup": (5, 3600),
    "auth-password-reset": (5, 3600),
    "auth-invite": (30, 3600),
    "auth-mutating": (30, 60),
    "auth-discovery": (60, 60),
    "auth-sso-login-initiation": (30, 60),
    "auth-sso-callback": (30, 60),
    "oauth-callback": (30, 60),
    "ai-generation": (20, 60),
    "agent-execution": (60, 60),
    "a2a-task-execute": (60, 60),
    "workflow-execution": (60, 60),
    "chat-query": (60, 60),
    "sales-agent-trigger": (20, 60),
    "sop-parse": (10, 60),
    "sop-deploy": (10, 60),
    "knowledge-search": (120, 60),
    "content-safety-check": (120, 60),
    "mcp-tool-call": (120, 60),
    "agent-feedback": (120, 60),
    "file-upload": (30, 60),
    "sop-upload": (30, 60),
    "bulk-import": (10, 60),
    "connector-bulk": (10, 60),
    "company-bulk-write": (10, 60),
    "billing-mutating": (20, 60),
    "credential-write": (30, 60),
    "security-admin-write": (30, 60),
    "provider-webhook": (600, 60),
    "commerce-webhook": (600, 60),
    "gateway-webhook": (600, 60),
    "aa-callback-signed": (300, 60),
}
_DEFAULT_RATE = (300, 60)

# Rate-limit classes where a Redis outage in strict env must fail closed
# (credential-guessing and unauthenticated spend paths). Everything else
# stays available with a warning — a rate limiter must not become the outage.
_FAIL_CLOSED_CLASS_PREFIXES = ("auth-", "public-", "demo-")
# Bug sheet 2026-09-14 row 7: SSO initiation and the OIDC callback accept no
# credentials (the IdP owns password brute-force protection) and their flow
# state is signed + browser-bound, so a limiter outage degrades to
# allow-with-warning instead of 503-ing every SSO login. Explicit set, not a
# prefix: every other ``auth-*`` class (login, signup, reset, ...) stays
# fail-closed.
_DEGRADE_ON_OUTAGE_CLASSES = frozenset({"auth-sso-login-initiation", "auth-sso-callback"})

# Declared route-scope family (prefix before the first "." or ":") ->
# (read scope, write scope) from core.rbac.ROLE_SCOPES.
SCOPE_FAMILIES: dict[str, tuple[str, str]] = {
    "agents": ("agents:read", "agents:write"),
    # Chat executes agents (bug sheet #53, 2026-09-14): a query needs the
    # same write scope as ``POST /agents/{id}/run``; history is a read.
    "chat": ("agents:read", "agents:write"),
    # Agent teams route work across agents (bug sheet 2026-09-14 ownership
    # sweep): the family was unmapped, so any authenticated tenant user could
    # create or read routing teams. Same scopes as the agents they contain.
    "agent_teams": ("agents:read", "agents:write"),
    "workflows": ("workflows:read", "workflows:write"),
    "workflow_variants": ("workflows:read", "workflows:write"),
    "approvals": ("approvals:read", "approvals:write"),
    "audit": ("audit:read", "audit:read"),
    "connectors": ("connectors.read", "connectors.read"),
    "report_schedules": ("report_schedules.read", "report_schedules.write"),
}

# Legacy spellings that issued credentials still carry. ``create_api_key``
# used to hand out ``agents:run`` / ``connectors:read`` (colon), which could
# never satisfy the family map above (audit 2026-09-13 finding 2). Aliases
# map to the canonical RBAC scope; ``_expand_granted`` also accepts the
# colon/dot separator variant of every family scope.
LEGACY_SCOPE_ALIASES: dict[str, str] = {
    "agents:run": "agents:write",
    "connectors:read": "connectors.read",
}


def _family(declared_scope: str) -> str:
    head = declared_scope.split(":", 1)[0]
    return head.split(".", 1)[0]


def required_scopes_for(declared_scope: str | None, method: str) -> tuple[str, ...]:
    """RBAC scopes (any one suffices) required for ``declared_scope``.

    Empty tuple means "not mapped" — no scope check beyond authentication.
    ``connectors.*`` writes are admin-gated at the route level already; the
    family maps writes to ``connectors.read`` so domain roles can still test
    and list their connectors.
    """
    if not declared_scope:
        return ()
    family = SCOPE_FAMILIES.get(_family(declared_scope))
    if family is None:
        return ()
    read_scope, write_scope = family
    return (read_scope,) if method.upper() in _READ_METHODS else (write_scope,)


def unmapped_scope_families(declared_scopes: list[str]) -> set[str]:
    """Families present in the route table with no RBAC mapping (reported, not enforced)."""
    return {_family(s) for s in declared_scopes if s and _family(s) not in SCOPE_FAMILIES}


def _expand_granted(granted: list[str]) -> set[str]:
    """Granted scopes plus their legacy aliases and separator variants."""
    out: set[str] = set()
    for scope in granted:
        if not isinstance(scope, str) or not scope:
            continue
        out.add(scope)
        alias = LEGACY_SCOPE_ALIASES.get(scope)
        if alias:
            out.add(alias)
        for sep, other in ((":", "."), (".", ":")):
            if sep in scope:
                head, tail = scope.split(sep, 1)
                out.add(f"{head}{other}{tail}")
    return out


def _client_ip(request: Request) -> str:
    return resolve_client_ip(request)


def _enforcement_mode() -> str:
    mode = str(getattr(settings, "route_enforcement_mode", "enforce") or "enforce").lower()
    return "log" if mode == "log" else "enforce"


def _deny(request: Request, status: int, detail: str, **ctx: Any) -> None:
    if _enforcement_mode() == "log":
        logger.warning("route_enforcement_log_only", extra={"path": request.url.path, "detail": detail, **ctx})
        return
    raise HTTPException(status_code=status, detail=detail)


async def _check_rate_limit(request: Request, meta: dict[str, Any]) -> None:
    rate_class = meta.get("rate_limit")
    if not rate_class:
        return
    limit, window = RATE_LIMIT_CLASSES.get(rate_class, _DEFAULT_RATE)
    tenant_id = getattr(request.state, "tenant_id", None)
    principal = f"t:{tenant_id}" if (meta.get("auth_required") and tenant_id) else f"ip:{_client_ip(request)}"

    from core.auth_state import check_window_rate

    try:
        blocked = await check_window_rate(f"rl:{rate_class}", principal, limit, window)
    except RuntimeError as exc:
        # Redis unavailable in strict env. Credential/unauthenticated-spend
        # classes fail closed; everything else stays available.
        if rate_class.startswith(_FAIL_CLOSED_CLASS_PREFIXES) and rate_class not in _DEGRADE_ON_OUTAGE_CLASSES:
            logger.error("route_rate_limit_backend_unavailable_fail_closed", extra={"rate_class": rate_class})
            raise HTTPException(status_code=503, detail="Rate limiting unavailable; request refused") from exc
        logger.warning("route_rate_limit_backend_unavailable_allowing", extra={"rate_class": rate_class})
        return
    if blocked:
        _deny(request, 429, "Rate limit exceeded", rate_class=rate_class)


def _check_scope(request: Request, meta: dict[str, Any]) -> None:
    if not meta.get("auth_required"):
        return
    if getattr(request.state, "auth_mode", None) not in ("legacy", "api_key"):
        return  # Grantex agent tokens: tool scopes enforced by the tool gateway
    required = required_scopes_for(meta.get("scope"), request.method)
    if not required:
        return
    granted = _expand_granted(getattr(request.state, "scopes", None) or [])
    if ADMIN_SCOPE in granted or any(s in granted for s in required):
        return
    _deny(request, 403, f"Missing scope: {' or '.join(required)}", declared=meta.get("scope"))


async def enforce_route_metadata(request: Request) -> None:
    """Global dependency: apply ``route_meta`` rate limits and scope checks."""
    route = request.scope.get("route")
    endpoint = getattr(route, "endpoint", None)
    meta = getattr(endpoint, ROUTE_METADATA_ATTR, None) if endpoint is not None else None
    if not isinstance(meta, dict):
        return
    await _check_rate_limit(request, meta)
    _check_scope(request, meta)
