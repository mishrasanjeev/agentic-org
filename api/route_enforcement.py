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
  hands to roles, except ``a2a`` and ``mcp``, whose scopes only API keys and
  agent grants hold. GET/HEAD/OPTIONS require the family's read scope,
  everything else the write scope. ``agenticorg:admin`` satisfies every family.

Scope enforcement applies to every credential: user sessions, API keys and
Grantex agent tokens alike. An agent token's tool scopes
(``tool:<connector>:<permission>``) are checked separately by the tool
gateway and satisfy no route family, so an agent that calls the API needs the
route scope in its grant, exactly as an API key does (review H-1). An
authenticated route reached with an ``auth_mode`` the auth middleware does not
set is logged; with ``AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE`` on it is
refused before any scope is read, whatever scopes it carries, and with it off
(the default) it is checked on its scopes like any other request.

Families that are not mapped are NOT enforced — they are reported by
:func:`unmapped_scope_families` and pinned by a unit test so the gap is
visible instead of silent. The ``a2a`` and ``mcp`` families are mapped but
enforced only while ``AGENTICORG_ROUTE_SCOPE_A2A_MCP`` is on; no role holds
their scopes, so API keys and agent grants that are given them, and admins,
reach those routes.

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
    "commerce-a2a-buyer": (60, 60),
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
    # A run of the guardrail adversarial set dispatches detector work for every case and rule.
    "guardrails-suite": (6, 60),
    # A prompt comparison or evaluation makes several billed model calls per request.
    "prompt-compare": (6, 60),
    "provider-webhook": (600, 60),
    "commerce-webhook": (600, 60),
    "gateway-webhook": (600, 60),
    "aa-callback-signed": (300, 60),
}
_DEFAULT_RATE = (300, 60)

# Rate-limit classes where a Redis outage in strict env must fail closed
# (credential-guessing and unauthenticated spend paths). Everything else
# stays available with a warning — a rate limiter must not become the outage.
_FAIL_CLOSED_CLASS_PREFIXES = ("auth-", "public-", "demo-", "commerce-a2a-buyer")
# Bug sheet 2026-09-14 row 7: SSO initiation and the OIDC callback accept no
# credentials (the IdP owns password brute-force protection) and their flow
# state is signed + browser-bound, so a limiter outage degrades to
# allow-with-warning instead of 503-ing every SSO login. Explicit set, not a
# prefix: every other ``auth-*`` class (login, signup, reset, ...) stays
# fail-closed.
_DEGRADE_ON_OUTAGE_CLASSES = frozenset({"auth-sso-login-initiation", "auth-sso-callback"})

# Declared route-scope family (prefix before the first "." or ":") ->
# (read scope, write scope). Every pair is taken from core.rbac.ROLE_SCOPES
# except ``a2a`` and ``mcp``: no role holds their scopes, so only API keys and
# agent grants given them, and agenticorg:admin, pass; and they are enforced
# only while route_scope_a2a_mcp is on (_enforced_family). A family that is
# not listed here is not scope-checked at all (unmapped_scope_families).
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
    # Call recordings and their transcripts are customer speech: a read
    # needs the audit scope the domain roles and the auditor hold, a write
    # (an upload, a transcript, a summary) the approvals write scope the
    # domain roles hold. Administrators pass as everywhere.
    "speech": ("audit:read", "approvals:write"),
    # Transaction records and findings are customer money movements: the
    # same shape as speech, a read for the audit scope, a write (records in,
    # detectors run, a disposition) for the approvals write scope.
    "txn": ("audit:read", "approvals:write"),
    # Lineage (core/lineage/): reading provenance is an audit read; noting a
    # chain an acquisition produced is a write of record.
    "lineage": ("audit:read", "approvals:write"),
    # Personalisation (core/personalisation/): consents, profiles and what was
    # rendered for a customer are audit-grade reads; granting consent, editing
    # profiles and rules, and rendering take the approvals write scope.
    "personalisation": ("audit:read", "approvals:write"),
    # Long-term memory holds what is remembered about customers and cases:
    # recall is an audit-grade read and a write changes what runs are told,
    # so it takes the approver scope (the sensitive-subsystem precedent).
    "memory": ("audit:read", "approvals:write"),
    # Content services (/content: drafting, summarisation, extraction and the
    # drafts queue) send the tenant's documents to a model and return
    # regulated text. Reads need audit:read (CxO, domain lead, auditor) and
    # actions need approvals:write (CxO, domain lead, developer); an analyst
    # holds neither. Drafting, draft decisions and the dataset install also
    # need an administrator at the route.
    "content": ("audit:read", "approvals:write"),
    # Document processing reads customer documents (statements, identity
    # documents, salary slips) and runs OCR: the same roles that work the
    # review queue, which are the roles the UI admits to the documents page.
    # Listing the catalogue needs approvals:read; analysing a file (and the
    # classifier dry run, a POST) needs approvals:write. Auditors hold
    # neither scope and analysts only the read, so neither submits documents.
    "documents": ("approvals:read", "approvals:write"),
    # A2A tasks and MCP calls run any agent type for machine callers (FINDINGS
    # A-68). No role holds these scopes: API keys and agent grants are given
    # them. Enforced only while AGENTICORG_ROUTE_SCOPE_A2A_MCP is on
    # (_A2A_MCP_FAMILIES), so they can be issued before they are required.
    "a2a": ("a2a:read", "a2a:write"),
    "mcp": ("mcp:read", "mcp:write"),
}

# Families above that are enforced only while ``route_scope_a2a_mcp`` is on.
_A2A_MCP_FAMILIES = frozenset({"a2a", "mcp"})

# Legacy spellings that issued credentials still carry. ``create_api_key``
# used to hand out ``agents:run`` / ``connectors:read`` (colon), which could
# never satisfy the family map above (audit 2026-09-13 finding 2), and still
# hands out ``mcp:call``. Aliases map to the canonical family scope;
# ``_expand_granted`` also accepts the colon/dot separator variant of every
# family scope.
LEGACY_SCOPE_ALIASES: dict[str, str] = {
    "agents:run": "agents:write",
    "connectors:read": "connectors.read",
    "mcp:call": "mcp:write",
}

# The modes ``auth.grantex_middleware`` sets once it has verified a credential.
KNOWN_AUTH_MODES = frozenset({"api_key", "grantex", "legacy", "commerce_buyer"})


# Scopes an operator may attach to an agent's Grantex registration so its
# token can call the matching route families. Exactly the
# canonical family scopes: never ``agenticorg:admin``, legacy aliases or any
# scope outside SCOPE_FAMILIES. The A2A and MCP scopes are included even while
# their setting is off, so an agent can hold them before they are required.
GRANTABLE_ROUTE_SCOPES: frozenset[str] = frozenset(scope for pair in SCOPE_FAMILIES.values() for scope in pair)


def validate_route_scopes(scopes: object) -> list[str]:
    """Return ``scopes`` de-duplicated and sorted, or raise ``ValueError`` saying why.

    Only canonical route-family scopes (``GRANTABLE_ROUTE_SCOPES``) are accepted,
    compared exactly.
    """
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise ValueError("route_scopes must be a list of scope names")
    unknown = sorted({s for s in scopes if s not in GRANTABLE_ROUTE_SCOPES})
    if unknown:
        raise ValueError(
            f"route_scopes {unknown} cannot be granted to an agent; allowed: {sorted(GRANTABLE_ROUTE_SCOPES)}"
        )
    return sorted(set(scopes))


def _family(declared_scope: str) -> str:
    head = declared_scope.split(":", 1)[0]
    return head.split(".", 1)[0]


def _enforced_family(family: str) -> tuple[str, str] | None:
    """``SCOPE_FAMILIES[family]``, or ``None`` when the family is not enforced now."""
    if family in _A2A_MCP_FAMILIES and not settings.route_scope_a2a_mcp:
        return None
    return SCOPE_FAMILIES.get(family)


def required_scopes_for(declared_scope: str | None, method: str) -> tuple[str, ...]:
    """RBAC scopes (any one suffices) required for ``declared_scope``.

    Empty tuple means "not mapped" — no scope check beyond authentication.
    ``connectors.*`` writes are admin-gated at the route level already; the
    family maps writes to ``connectors.read`` so domain roles can still test
    and list their connectors.
    """
    if not declared_scope:
        return ()
    family = _enforced_family(_family(declared_scope))
    if family is None:
        return ()
    read_scope, write_scope = family
    return (read_scope,) if method.upper() in _READ_METHODS else (write_scope,)


def unmapped_scope_families(declared_scopes: list[str]) -> set[str]:
    """Families present in the route table with no RBAC mapping (reported, not enforced)."""
    return {_family(s) for s in declared_scopes if s and _enforced_family(_family(s)) is None}


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
    buyer_access_id = getattr(request.state, "buyer_access_id", None)
    principal = (
        f"buyer:{buyer_access_id}"
        if buyer_access_id
        else f"t:{tenant_id}"
        if (meta.get("auth_required") and tenant_id)
        else f"ip:{_client_ip(request)}"
    )

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
    # Only the auth middleware's credential paths set a known mode. No HTTP
    # route that declares auth_required accepts OPTIONS or sits under one of
    # the middleware's exempt paths or prefixes, which it passes through
    # unauthenticated; the route table test in
    # tests/regression/test_route_scope_unknown_mode_20260927.py pins this,
    # with the one parameterised overlap in FINDINGS A-88. WebSocket routes
    # never get here (A-86). Any other mode (or none) means the scopes on
    # this request were not put there by a verified credential, and it is
    # always logged. With route_refuse_unknown_auth_mode on it is refused
    # before any scope is read, in log mode too: log mode stages scope
    # denials, it does not stand in for authentication. Off (the default,
    # FINDINGS A-95), the checks below run on whatever scopes it carries,
    # agenticorg:admin included, and an unmapped family passes, as before.
    auth_mode = getattr(request.state, "auth_mode", None)
    if not isinstance(auth_mode, str) or auth_mode not in KNOWN_AUTH_MODES:
        logger.warning(
            "route_enforcement_unknown_auth_mode",
            extra={"path": request.url.path, "auth_mode": repr(auth_mode)[:40]},
        )
        if settings.route_refuse_unknown_auth_mode:
            raise HTTPException(status_code=403, detail="Unrecognised authentication mode; request refused")
    required = required_scopes_for(meta.get("scope"), request.method)
    if auth_mode == "commerce_buyer":
        if request.url.path not in {"/api/v1/a2a/message:send", "/api/v1/a2a/extendedAgentCard"}:
            _deny(request, 403, "Buyer credential is A2A-only", declared=meta.get("scope"))
        return
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
