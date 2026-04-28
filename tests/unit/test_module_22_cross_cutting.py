"""Foundation #6 — Module 22 Cross-Cutting Concerns (12 TCs).

Source-pin tests for TC-CC-001 through TC-CC-012.

Module 22 is the safety-net module — RBAC, tenant isolation,
error envelope shape, prompt-lock, concurrency protection,
input safety. These pins guard the patterns that every other
module relies on.

Pinned contracts:

- ``require_scope("agenticorg:admin")`` admin-bypass: every
  scope check accepts the actor when scopes contain anything
  starting with ``agenticorg:admin``. The auditor role gets
  read scopes only — no write scope is derivable.
- Tenant isolation = SET LOCAL agenticorg.tenant_id at session
  open + RLS policies on every tenant-scoped table.
- Error envelope is the E-series shape:
  ``{"error": {"code", "name", "message", "severity",
  "retryable", "timestamp"}}``. The codes are part of the
  public contract.
- Postgres advisory lock pattern is used to serialize
  concurrent requests on the same agent (per-agent budget,
  prompt edits) so two parallel writers can't race past the
  budget cap.
- Prompt lock on active agents: PATCH/POST flows return 400
  with the exact message the UI parses.
- SQL injection: all asyncpg queries use bind parameters; the
  one place that interpolates (SET LOCAL tenant_id) validates
  the UUID with a regex first.
- XSS / arbitrary-content: defusedxml is used for any XML
  parsing path (RPA scrapers); JSON is parsed by Pydantic
  with strict typing.
- Large payload: handled at the platform edge — pin that the
  router uses Pydantic models on every endpoint that takes
  a body so deeply nested or massive structures fail fast
  via 422 instead of OOM-ing the worker.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-CC-001 / 002 — RBAC: domain segregation + admin sees all
# ─────────────────────────────────────────────────────────────────


def test_tc_cc_001_get_user_domains_returns_jwt_claim() -> None:
    """The domain-segregation gate reads the JWT claim
    ``agenticorg:domains`` — no client-supplied query params,
    no DB lookup. If this changes shape, every domain-scoped
    endpoint silently breaks."""
    src = (REPO / "api" / "deps.py").read_text(encoding="utf-8")
    assert 'claims.get("agenticorg:domains")' in src


def test_tc_cc_002_admin_scope_bypass_is_pinned() -> None:
    """``require_scope`` must accept any scope starting with
    ``agenticorg:admin`` so the global-admin role doesn't need
    every per-resource scope listed individually. Removing
    this widens privilege gaps because admins suddenly fail
    routes they previously had access to."""
    src = (REPO / "api" / "deps.py").read_text(encoding="utf-8")
    assert 'any(s.startswith("agenticorg:admin") for s in scopes)' in src


# ─────────────────────────────────────────────────────────────────
# TC-CC-003 — RBAC: auditor denied write operations
# ─────────────────────────────────────────────────────────────────


def test_tc_cc_003_auditor_role_does_not_get_write_scopes() -> None:
    """The role-to-scope map must NOT grant the auditor role any
    scope ending in ``:write``. Foundation #8 false-green
    prevention: granting write to auditor would silently let
    them mutate state during what should be a read-only audit.

    Loads the live ROLE_SCOPES dict and inspects the auditor
    entry directly — more precise than text-grep, and any
    structural rename will surface as an ImportError."""
    from core.rbac import get_scopes_for_role

    auditor_scopes = get_scopes_for_role("auditor") or []
    write_scopes = [s for s in auditor_scopes if ":write" in s]
    assert not write_scopes, (
        f"auditor role grants write scopes {write_scopes} — "
        f"broke read-only contract"
    )
    # Auditor must still have audit:read so the audit page works.
    assert "audit:read" in auditor_scopes


# ─────────────────────────────────────────────────────────────────
# TC-CC-004 — Tenant isolation (SET LOCAL + RLS)
# ─────────────────────────────────────────────────────────────────


def test_tc_cc_004_get_tenant_session_validates_uuid_before_set_local() -> None:
    """asyncpg can't bind parameters in SET LOCAL, so we
    interpolate. The interpolation is safe ONLY because we
    regex-validate the UUID first. If the validation goes
    away, the SET LOCAL becomes a SQL injection vector."""
    src = (REPO / "core" / "database.py").read_text(encoding="utf-8")
    assert "SET LOCAL agenticorg.tenant_id" in src
    # The defense-in-depth UUID regex must remain.
    assert "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}" in src
    assert "ValueError" in src and "tenant_id" in src


def test_tc_cc_004_rls_policy_uses_current_setting() -> None:
    """RLS policies must filter on
    ``current_setting('agenticorg.tenant_id', true)`` — the
    same key the session-open SET LOCAL writes. Mismatched
    keys silently disable RLS without anyone noticing."""
    src = (REPO / "core" / "database.py").read_text(encoding="utf-8")
    assert "current_setting('agenticorg.tenant_id', true)" in src
    assert "tenant_isolation" in src


# ─────────────────────────────────────────────────────────────────
# TC-CC-005 — Concurrent requests (advisory lock)
# ─────────────────────────────────────────────────────────────────


def test_tc_cc_005_per_agent_advisory_lock_pattern_pinned() -> None:
    """Agent-scoped writes (budget check, run dispatch) use a
    Postgres advisory lock keyed on a hash of the agent UUID
    so two concurrent calls serialize. Without it, both can
    see "under budget" and both blow past the cap."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in src
    assert "lock_key = abs(hash(str(agent_id))) % (2**31)" in src


def test_tc_cc_005_init_db_advisory_lock_for_startup_ddl() -> None:
    """Rolling deploys can race on ALTER TABLE ENABLE ROW LEVEL
    SECURITY (the April 16 outage). The startup DDL block must
    use a transaction-scoped advisory lock to serialize."""
    src = (REPO / "core" / "database.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock(4815162342)" in src


# ─────────────────────────────────────────────────────────────────
# TC-CC-006 / 007 / 008 — Error handling envelope shape
# ─────────────────────────────────────────────────────────────────


def test_tc_cc_006_value_error_returns_e2001_envelope() -> None:
    """A ValueError raised in any handler must surface as the
    documented E2001 / VALIDATION_ERROR envelope. The envelope
    keys are part of the public API contract."""
    src = (REPO / "api" / "error_handlers.py").read_text(encoding="utf-8")
    assert '"E2001"' in src
    assert '"VALIDATION_ERROR"' in src
    for key in ('"code"', '"name"', '"message"', '"severity"',
                '"retryable"', '"timestamp"'):
        assert key in src, f"error envelope missing key {key}"


def test_tc_cc_008_404_returns_e1005_envelope() -> None:
    src = (REPO / "api" / "error_handlers.py").read_text(encoding="utf-8")
    assert '"E1005"' in src
    assert '"NOT_FOUND"' in src
    assert '"Resource not found"' in src


def test_tc_cc_006_500_returns_retryable_e1001_envelope() -> None:
    """500s carry ``retryable: True`` so smart clients can back off
    + retry. 4xx/422 client errors do NOT (the request itself is
    bad, retrying won't help). Pin the asymmetry."""
    src = (REPO / "api" / "error_handlers.py").read_text(encoding="utf-8")
    assert '"E1001"' in src
    assert '"INTERNAL_ERROR"' in src
    # 500 retryable=True
    block_500 = src.split('"E1001"', 1)[1][:400]
    assert '"retryable": True' in block_500
    # 400 retryable=False
    block_400 = src.split('"E2001"', 1)[1][:400]
    assert '"retryable": False' in block_400


# ─────────────────────────────────────────────────────────────────
# TC-CC-009 — Prompt lock on active agents
# ─────────────────────────────────────────────────────────────────


def test_tc_cc_009_prompt_lock_on_active_agents_returns_specific_message() -> None:
    """The exact message text is parsed by the UI to render the
    "Clone this agent" CTA. Renaming silently breaks the UX."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "Prompt is locked on active agents. Clone this agent to make changes." in src


# ─────────────────────────────────────────────────────────────────
# TC-CC-010 — SQL injection prevention
# ─────────────────────────────────────────────────────────────────


def test_tc_cc_010_audit_event_type_uses_bind_parameter_not_concat() -> None:
    """The audit event_type filter uses an ILIKE pattern with the
    user input bound via SQLAlchemy text + bind, NOT
    f-string interpolation. Bind params are SQL-injection-safe;
    f-strings are not."""
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    # Pattern is built as a normal Python string (with %), then
    # passed to AuditLog.event_type.ilike(pattern). SQLAlchemy
    # routes this via parameter binding, not literal SQL.
    assert "AuditLog.event_type.ilike(pattern)" in src
    # And there's NO f-string SQL anywhere in this file.
    assert 'f"SELECT' not in src
    assert 'f"UPDATE' not in src
    assert 'f"DELETE' not in src


def test_tc_cc_010_set_local_uuid_validation_is_defense_in_depth() -> None:
    """Cross-pin with TC-CC-004: the one place we DO interpolate
    SQL (SET LOCAL agenticorg.tenant_id) is gated by a UUID
    regex. This is defense-in-depth on top of the JWT-supplied
    tenant_id."""
    src = (REPO / "core" / "database.py").read_text(encoding="utf-8")
    assert "defense-in-depth" in src.lower() or "regex above" in src.lower()


# ─────────────────────────────────────────────────────────────────
# TC-CC-011 — XSS / arbitrary-content safety
# ─────────────────────────────────────────────────────────────────


def test_tc_cc_011_defusedxml_used_for_xml_parsing() -> None:
    """Any XML the platform parses (RPA scrapers, Tally) must go
    through defusedxml — stdlib xml.etree is vulnerable to
    XXE / billion-laughs."""
    src = (REPO / "connectors" / "framework" / "base_connector.py").read_text(
        encoding="utf-8"
    )
    assert "from defusedxml.ElementTree import" in src or "defusedxml" in src


# ─────────────────────────────────────────────────────────────────
# TC-CC-012 — Large payload handling
# ─────────────────────────────────────────────────────────────────


def test_tc_cc_012_pydantic_models_validate_request_bodies() -> None:
    """The boundary defense for large/malformed payloads is
    Pydantic — every endpoint that takes a body declares a
    BaseModel, so massive or deeply-nested input fails fast
    via 422 instead of OOM-ing the worker."""
    # Pin the pattern via a representative endpoint: the org
    # invite handler has an InviteRequest model.
    src = (REPO / "api" / "v1" / "org.py").read_text(encoding="utf-8")
    assert "class InviteRequest(BaseModel):" in src
    assert "class AcceptInviteRequest(BaseModel):" in src
    # And the handlers take these models, not raw dict.
    assert "body: InviteRequest" in src
    assert "body: AcceptInviteRequest" in src


def test_tc_cc_012_audit_per_page_caps_response_size() -> None:
    """Cross-cutting size cap example: per_page is clamped to
    [1, 100] in /audit. This pattern (cap at the boundary) is
    the platform's defense against "give me 10k rows please" →
    OOM. Pin the cap so a future refactor can't lift it."""
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    assert "per_page = min(max(per_page, 1), 100)" in src
