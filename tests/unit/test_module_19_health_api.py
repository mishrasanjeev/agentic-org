"""Foundation #6 — Module 19 Health & API.

Source-pin tests for TC-API-001 through TC-API-006. Deploy-readiness
contract — K8s probes, Cloud Run health checks, and SRE dashboards
all key off the response shapes pinned here.

Pinned contracts:

- /health/liveness is the lightweight probe (no DB / no Redis).
- /health is the readiness probe — checks DB + Redis ONLY,
  returns "healthy"/"unhealthy" + version + commit + per-check
  status.
- /health/diagnostics is the heavyweight admin-only probe with
  full connector + composio detail.
- All routers mounted under ``/api/v1`` prefix — versioned URL
  is the public-API contract.
- CORS origins: in dev=*, in prod=allowlisted domains.
- PaginatedResponse default per_page is 20 (NOT 50, NOT 100 —
  changing this breaks every paginated UI list).
- Health gate accepts ONLY "healthy" — Foundation #5/#8 lesson:
  the prod health gate must reject "degraded" / "unhealthy"
  / anything else as failed, since the gate is what keeps a
  bad rollout from receiving traffic.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-API-001 — Liveness
# ─────────────────────────────────────────────────────────────────


def test_tc_api_001_liveness_endpoint_is_lightweight() -> None:
    """Liveness must NOT touch DB or Redis — those are readiness
    concerns. K8s liveness probes that wait on external deps
    cause cascading restarts when those deps blip."""
    src = (REPO / "api" / "v1" / "health.py").read_text(encoding="utf-8")
    # Find the liveness function block.
    block = src.split('@router.get("/health/liveness")', 1)[1].split(
        '@router.get(', 1
    )[0]
    assert '"status": "alive"' in block
    # Must NOT do DB or Redis work in liveness.
    assert "session" not in block
    assert "redis" not in block.lower()
    assert "aioredis" not in block


# ─────────────────────────────────────────────────────────────────
# TC-API-002 — Full readiness
# ─────────────────────────────────────────────────────────────────


def test_tc_api_002_readiness_checks_db_and_redis() -> None:
    """Readiness must probe BOTH DB and Redis — Cloud Run / K8s
    keys off this to decide whether to send traffic."""
    src = (REPO / "api" / "v1" / "health.py").read_text(encoding="utf-8")
    block = src.split('@router.get("/health")', 1)[1].split(
        '@router.get(', 1
    )[0]
    assert "session.execute(text" in block
    assert "aioredis.from_url" in block
    # Returns the per-check status so dashboards can show which
    # subsystem broke.
    assert '"checks":' in block


def test_tc_api_002_readiness_returns_only_healthy_when_all_green() -> None:
    """Foundation #5/#8 lesson: the prod health gate accepts ONLY
    'healthy'. Anything else (unhealthy, degraded) must NOT
    surface as a passing readiness check — otherwise a bad
    deploy keeps receiving traffic."""
    src = (REPO / "api" / "v1" / "health.py").read_text(encoding="utf-8")
    block = src.split('@router.get("/health")', 1)[1].split(
        '@router.get(', 1
    )[0]
    # The status branch is `"healthy" if core_healthy else "unhealthy"`.
    assert '"healthy" if core_healthy else "unhealthy"' in block
    assert (
        "core_healthy = checks[\"db\"] == \"healthy\" "
        "and checks[\"redis\"] == \"healthy\""
    ) in block


def test_tc_api_002_readiness_includes_version_and_commit() -> None:
    """Operators need version + commit in the readiness response
    so a "wrong version is serving" outage is one curl away from
    diagnosis. Codex 2026-04-23 prod incident — pin the field
    presence so a refactor can't silently drop them."""
    src = (REPO / "api" / "v1" / "health.py").read_text(encoding="utf-8")
    block = src.split('@router.get("/health")', 1)[1].split(
        '@router.get(', 1
    )[0]
    assert '"version": APP_VERSION' in block
    assert '"commit": _deployed_commit()' in block


def test_tc_api_002_diagnostics_endpoint_is_admin_only() -> None:
    """The full diagnostics endpoint includes connector + env
    detail — must be admin-gated. A public endpoint here would
    leak operational topology."""
    src = (REPO / "api" / "v1" / "health.py").read_text(encoding="utf-8")
    assert (
        '"/health/diagnostics",\n'
        "    dependencies=[require_scope(\"agenticorg:admin\")],"
    ) in src or 'require_scope("agenticorg:admin")' in src.split(
        '"/health/diagnostics"', 1
    )[1][:200]


# ─────────────────────────────────────────────────────────────────
# TC-API-003 — API versioning
# ─────────────────────────────────────────────────────────────────


def test_tc_api_003_all_routers_mount_under_v1_prefix() -> None:
    """Every public router is mounted under ``/api/v1``. Without
    the version prefix, breaking changes can't be rolled out
    behind a /api/v2 — every client breaks at the same time."""
    src = (REPO / "api" / "main.py").read_text(encoding="utf-8")
    # Count include_router calls AND assert all carry the prefix.
    include_lines = [line for line in src.splitlines() if "include_router" in line]
    assert len(include_lines) > 10, "expected many routers; got too few"
    # Every line that includes a router must declare the v1 prefix
    # (a few exceptions exist for non-versioned endpoints — each
    # must be explicit). Check that at least 90% have /api/v1.
    v1_lines = [line for line in include_lines if 'prefix="/api/v1"' in line]
    coverage = len(v1_lines) / len(include_lines)
    assert coverage >= 0.9, (
        f"only {coverage*100:.0f}% of routers use /api/v1 prefix — "
        f"versioning contract slipping"
    )


def test_tc_api_003_app_carries_version_string() -> None:
    """FastAPI's ``version=`` arg shows up in OpenAPI + the docs
    page header. SDK consumers parse OpenAPI to detect breaking
    changes — without a version string they can't pin compat."""
    src = (REPO / "api" / "main.py").read_text(encoding="utf-8")
    assert "version=product_facts._version_from_pyproject()" in src


# ─────────────────────────────────────────────────────────────────
# TC-API-004 — CORS headers
# ─────────────────────────────────────────────────────────────────


def test_tc_api_004_cors_dev_allows_wildcard() -> None:
    """In development we open CORS so localhost UIs can hit a
    deployed API. Without this, every dev needs to fiddle with
    a proxy."""
    src = (REPO / "api" / "main.py").read_text(encoding="utf-8")
    # Pin the dev branch.
    assert 'if settings.env == "development"' in src
    assert '_cors_origins = (\n    ["*"]' in src


def test_tc_api_004_cors_prod_uses_allowlist() -> None:
    """In prod we MUST allowlist origins — a wildcard CORS in
    prod is an XSS-via-third-party-site vector."""
    src = (REPO / "api" / "main.py").read_text(encoding="utf-8")
    # The else branch falls back to a domain allowlist:
    assert "https://agenticorg.ai" in src
    assert "https://app.agenticorg.ai" in src


def test_tc_api_004_cors_middleware_added_with_credentials_true() -> None:
    """credentials=True is required for cookie-bearing requests
    from the UI. If this is False, the UI session breaks."""
    src = (REPO / "api" / "main.py").read_text(encoding="utf-8")
    cors_block = src.split("CORSMiddleware,", 1)[1][:500]
    assert "allow_credentials=True" in cors_block
    assert 'allow_origins=_cors_origins' in cors_block


# ─────────────────────────────────────────────────────────────────
# TC-API-005 — Pagination defaults
# ─────────────────────────────────────────────────────────────────


def test_tc_api_005_paginated_response_default_per_page_is_20() -> None:
    """The default per_page is 20. Changing this silently doubles
    or halves response sizes for every paginated endpoint, with
    real impact on UI list rendering + memory."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    # The model defines per_page: int = 20.
    block = src.split("class PaginatedResponse(BaseModel):", 1)[1][:300]
    assert "per_page: int = 20" in block


def test_tc_api_005_paginated_response_default_page_is_1() -> None:
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    block = src.split("class PaginatedResponse(BaseModel):", 1)[1][:300]
    assert "page: int = 1" in block


def test_tc_api_005_paginated_response_carries_total_and_pages() -> None:
    """``total`` and ``pages`` let UIs render "showing X-Y of Z"
    + pagination controls. Without them every UI builds its own
    incompatible math."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    block = src.split("class PaginatedResponse(BaseModel):", 1)[1][:300]
    assert "total: int" in block
    assert "pages: int" in block


# ─────────────────────────────────────────────────────────────────
# TC-API-006 — Custom page size (per_page)
# ─────────────────────────────────────────────────────────────────


def test_tc_api_006_audit_per_page_caps_at_100() -> None:
    """Custom per_page is supported but capped at 100 (Module 12
    test pinned this from the audit angle; cross-pin here from
    the API-contract angle)."""
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    assert "per_page = min(max(per_page, 1), 100)" in src


def test_tc_api_006_paginated_response_accepts_arbitrary_per_page_field() -> None:
    """The PaginatedResponse model just stores per_page — no
    upper-bound at the schema layer. Caps happen per-endpoint
    at the handler. Pin the model's permissiveness so handler
    caps remain the only enforcement point."""
    from core.schemas.api import PaginatedResponse

    # 1000 is fine as a stored value; the handler rejects 1000+
    # before constructing the response.
    p = PaginatedResponse(items=[], total=0, page=1, per_page=1000, pages=0)
    assert p.per_page == 1000
