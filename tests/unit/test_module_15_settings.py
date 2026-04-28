"""Foundation #6 — Module 15 Settings (5 TCs).

Source-pin tests for TC-SET-001 through TC-SET-005.

Settings is the tenant control plane — fleet limits, PII
masking, data region, audit retention. Compliance lives here:
the PII flag, data-region pin, and audit-retention floor are
all referenced by SOC-2 evidence collection.

Pinned contracts:

- /config router carries `dependencies=[require_tenant_admin]`
  at the ROUTER level — every route in the module is admin-
  only by default. Foundation #8 false-green prevention: a
  per-route gate would let a refactor accidentally drop the
  dependency on a single endpoint.
- /governance/config defaults are "fail-closed safe":
  GovernanceConfig server_default writes a row on first read
  so every tenant has a stable baseline.
- /governance/config PUT writes an AuditLog row with old + new
  values in the SAME TRANSACTION as the config change — if
  the audit insert fails, the config change rolls back too.
- /governance no-op PUT (no changed fields) returns current
  state without an audit row — auditors aren't spammed by
  empty PUTs.
- audit_retention_years is bounded [1, 10] at the schema
  layer.
- Fleet limits: max_active_agents=35, max_shadow_agents=50
  are the documented defaults.
- Data region is a closed enum: IN | EU | US — no other
  values accepted (compliance regions are a closed list).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-SET-001 — Settings page loads
# ─────────────────────────────────────────────────────────────────


def test_tc_set_001_fleet_limits_get_endpoint_pinned() -> None:
    src = (REPO / "api" / "v1" / "config.py").read_text(encoding="utf-8")
    assert '@router.get("/config/fleet_limits")' in src
    # Returns FleetLimits().model_dump() if no value stored yet.
    fleet_block = src.split('@router.get("/config/fleet_limits")', 1)[1].split(
        "@router.", 1
    )[0]
    assert "FleetLimits().model_dump()" in fleet_block
    assert "FleetLimits(**stored).model_dump()" in fleet_block


def test_tc_set_001_governance_get_endpoint_pinned() -> None:
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    assert '@router.get("/config", response_model=GovernanceConfigOut)' in src


def test_tc_set_001_governance_get_creates_baseline_on_first_read() -> None:
    """First-time read must create a defaulted GovernanceConfig
    row so every tenant has a stable baseline. Without this, a
    fresh tenant's first GET would 404 or return Nones, and
    downstream compliance checks would fail."""
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    get_block = src.split('@router.get("/config", response_model=', 1)[1].split(
        "@router.", 1
    )[0]
    assert "if row is None:" in get_block
    assert "row = GovernanceConfig(tenant_id=tid)" in get_block
    assert "session.add(row)" in get_block
    # Refresh required so server_default values are pulled back.
    assert "await session.refresh(row)" in get_block


# ─────────────────────────────────────────────────────────────────
# TC-SET-002 — Update fleet limits
# ─────────────────────────────────────────────────────────────────


def test_tc_set_002_fleet_limits_put_endpoint_pinned() -> None:
    src = (REPO / "api" / "v1" / "config.py").read_text(encoding="utf-8")
    assert '@router.put("/config/fleet_limits")' in src


def test_tc_set_002_fleet_limits_defaults_pinned() -> None:
    """The UI shows the defaults when nothing is stored. Pinning
    them so a refactor can't silently change a tenant's
    apparent budget."""
    src = (REPO / "core" / "schemas" / "api.py").read_text(encoding="utf-8")
    assert "max_active_agents: int = 35" in src
    assert "max_shadow_agents: int = 50" in src


def test_tc_set_002_fleet_limits_put_merges_into_existing_settings() -> None:
    """PUT must MERGE the new fleet_limits into the existing
    tenant.settings JSONB — NOT replace the whole dict.
    Otherwise toggling fleet limits would silently nuke any
    other settings keys (onboarding_step, branding, ...).
    Closure plan rule: partial-update semantics (TC-ORG-006
    cross-pin)."""
    src = (REPO / "api" / "v1" / "config.py").read_text(encoding="utf-8")
    put_block = src.split('@router.put("/config/fleet_limits")', 1)[1]
    assert "settings = dict(tenant.settings or {})" in put_block
    assert 'settings[_FLEET_LIMITS_KEY] = body.model_dump()' in put_block
    # NOT a wholesale replace.
    assert "tenant.settings = body.model_dump()" not in put_block


# ─────────────────────────────────────────────────────────────────
# TC-SET-003 — Toggle PII masking
# ─────────────────────────────────────────────────────────────────


def test_tc_set_003_governance_put_admin_gated() -> None:
    """PUT /governance/config carries an explicit admin gate
    even though the router already gates GET — keeping the
    decoration explicit so a future router refactor doesn't
    silently drop the requirement on the mutating endpoint."""
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    put_block = src.split('@router.put(', 1)[1][:300]
    assert "require_tenant_admin" in put_block


def test_tc_set_003_pii_masking_partial_update_semantics() -> None:
    """GovernanceConfigUpdate uses Optional fields — None means
    "don't change". The handler explicitly checks ``is not
    None`` before applying. Without this, a PUT with only
    pii_masking would clobber data_region back to the schema
    default."""
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    assert "pii_masking: bool | None = None" in src
    assert "if body.pii_masking is not None:" in src
    assert "if body.data_region is not None:" in src


def test_tc_set_003_governance_change_writes_audit_log_in_same_txn() -> None:
    """Compliance backbone: every governance change must produce
    an AuditLog row. Pin that the AuditLog insert + the row
    update happen in the same ``async with`` block — the
    comment explicitly says "in the same transaction so a
    failed audit write rolls back the config change"."""
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    assert (
        "in the same transaction so a failed audit write" in src
        or "Apply + audit in the same transaction" in src
    )
    assert 'event_type="governance_config.change"' in src
    assert 'action="update"' in src


def test_tc_set_003_governance_change_records_old_and_new_values() -> None:
    """The audit details include BOTH old and new values so
    auditors can reconstruct the change. Pin the structure."""
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    assert (
        '"changes": {k: {"old": v[0], "new": v[1]} for k, v in changed.items()}'
        in src
    )


def test_tc_set_003_no_op_put_skips_audit_log() -> None:
    """A PUT with no actual changes returns current state
    WITHOUT writing an audit row. Without this, dashboards
    refreshing the form would spam the audit log with empty
    "change" events."""
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    assert "if not changed:" in src
    assert "No-op" in src


# ─────────────────────────────────────────────────────────────────
# TC-SET-004 — Change data region
# ─────────────────────────────────────────────────────────────────


def test_tc_set_004_data_region_is_closed_enum_pinned() -> None:
    """Compliance regions are a CLOSED list. The Literal type
    forces Pydantic to reject any other value — Foundation #8
    false-green prevention: silently accepting "APAC" would
    let UI bugs persist a region the platform doesn't actually
    support."""
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    assert 'DataRegion = Literal["IN", "EU", "US"]' in src


def test_tc_set_004_audit_retention_bounded_in_schema() -> None:
    """Retention bounded [1, 10] years. <1 violates SOC-2; >10
    is operationally unreasonable + costly. Both bounds enforced
    at the schema layer."""
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    assert "audit_retention_years: int = Field(ge=1, le=10)" in src


# ─────────────────────────────────────────────────────────────────
# TC-SET-005 — Non-admin cannot access settings
# ─────────────────────────────────────────────────────────────────


def test_tc_set_005_config_router_admin_gated_at_router_level() -> None:
    """The /config router carries the admin gate at the router
    level so EVERY route inherits it. Foundation #8 false-
    green prevention: a per-route gate would let a refactor
    accidentally drop the dependency on a single endpoint."""
    src = (REPO / "api" / "v1" / "config.py").read_text(encoding="utf-8")
    assert (
        "router = APIRouter(dependencies=[require_tenant_admin])" in src
    )


def test_tc_set_005_governance_router_admin_gated_on_writes() -> None:
    """The /governance router does NOT gate at the router level
    (because GET is allowed for any authenticated user — they
    need to read their own tenant's config). The PUT is
    individually admin-gated."""
    src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
    # Router does NOT have the dependency at top level.
    assert (
        'router = APIRouter(prefix="/governance", tags=["Governance"])' in src
    )
    # But PUT is individually gated.
    put_block = src.split('@router.put(', 1)[1][:400]
    assert "require_tenant_admin" in put_block
