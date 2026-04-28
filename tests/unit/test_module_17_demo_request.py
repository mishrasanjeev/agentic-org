"""Foundation #6 — Module 17 Demo Request Flow (4 TCs).

Source-pin tests for TC-DEMO-001 through TC-DEMO-004.

The demo request endpoint is the public lead-capture surface.
Critical contracts:

- Public endpoint (NO auth required) — landing-page form must
  reach it without a session.
- Stores the request in legacy demo_requests table AND creates
  a row in lead_pipeline so the sales team sees the lead.
- Email notification + sales-agent trigger are NON-BLOCKING:
  exceptions are caught + logged but the public response still
  succeeds. Foundation #8 false-green prevention: a failed
  email must NOT show the user a 5xx (they'll bounce).
- Duplicate emails (same tenant + email) re-use the existing
  lead — no duplicate rows.
- Hardcoded default tenant
  (00000000-0000-0000-0000-000000000001) so single-tenant
  deployments don't fail on tenant lookup.
- Response shape: status, message, lead_id, agent_triggered —
  the landing form uses message verbatim.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-DEMO-001 — Submit demo request (no auth required)
# ─────────────────────────────────────────────────────────────────


def test_tc_demo_001_endpoint_is_public_no_auth_dependency() -> None:
    """The /demo-request endpoint MUST NOT carry a require_auth
    or require_tenant_admin dependency — it's the public
    landing-form receiver. Pin the absence so a refactor that
    "tightens security" doesn't silently break sign-ups."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    submit_block = src.split('@router.post("/demo-request"', 1)[1].split(
        "@router.", 1
    )[0]
    # Function signature has only `body: DemoRequest` — no auth
    # dependency injected.
    assert "async def submit_demo_request(body: DemoRequest):" in submit_block
    # And the router-level decoration is plain (no router-wide gate).
    assert "router = APIRouter()" in src


def test_tc_demo_001_returns_201_with_documented_message() -> None:
    """201 (Created) is the right status — a new resource was
    accepted. The message string is part of the public contract;
    the landing page renders it verbatim in the success toast."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    assert '@router.post("/demo-request", status_code=201)' in src
    assert '"message": "We\'ll be in touch within 2 minutes."' in src


def test_tc_demo_001_required_fields_pinned() -> None:
    """name + email required (Pydantic Field(...) shape). company,
    role, phone optional with empty-string defaults so the form
    works whether or not the user fills them."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    request_block = src.split("class DemoRequest(BaseModel):", 1)[1].split(
        "\n\n", 1
    )[0]
    assert "name: str" in request_block
    assert "email: str" in request_block
    # Optional fields with empty-string defaults.
    assert 'company: str = ""' in request_block
    assert 'role: str = ""' in request_block
    assert 'phone: str = ""' in request_block


# ─────────────────────────────────────────────────────────────────
# TC-DEMO-002 — Demo request creates lead in pipeline
# ─────────────────────────────────────────────────────────────────


def test_tc_demo_002_creates_lead_in_pipeline_with_documented_defaults() -> None:
    """The INSERT into lead_pipeline pins source='website',
    stage='new', score=0. The sales pipeline filters by these
    values; changing them silently changes which leads the
    sales agent picks up."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    assert "INSERT INTO lead_pipeline" in src
    assert "'website', 'new', 0" in src


def test_tc_demo_002_uses_hardcoded_default_tenant() -> None:
    """Single-tenant deployments must not fail on tenant lookup.
    The hardcoded UUID 00000000-0000-0000-0000-000000000001 is
    intentional — a tenant-lookup failure on the public endpoint
    would block sign-ups for everyone."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    assert (
        'default_tenant_id = "00000000-0000-0000-0000-000000000001"' in src
    )
    assert "single-tenant deployment" in src.lower()


def test_tc_demo_002_lead_creation_failure_is_non_blocking() -> None:
    """If lead-pipeline insert fails (e.g. column drift), the
    public endpoint MUST still return 201 — Foundation #8
    false-green prevention in reverse: a 5xx would lose the
    customer at the form. The email notification path still
    catches the request."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    # Lead creation block has a bare except that logs but
    # doesn't raise.
    assert "except Exception:" in src
    assert (
        'logger.exception("Failed to create lead in pipeline (non-blocking)")'
        in src
    )


def test_tc_demo_002_response_includes_lead_id_field() -> None:
    """lead_id surfaced in the response so the landing page can
    show it (or attach it to follow-up tracking)."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    assert '"lead_id": lead_id' in src


# ─────────────────────────────────────────────────────────────────
# TC-DEMO-003 — Duplicate email handling
# ─────────────────────────────────────────────────────────────────


def test_tc_demo_003_duplicate_email_reuses_existing_lead() -> None:
    """A second submission with the same email MUST return the
    existing lead_id — NOT create a duplicate row. Without
    this, a customer who hits the form twice would create two
    leads + double the sales agent's work."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    assert "Check for duplicate lead (same email)" in src
    assert (
        "SELECT id FROM lead_pipeline WHERE email = :email AND tenant_id = :tid"
        in src
    )
    # On dup: log "already exists" and reuse the lead_id.
    assert "lead_id = str(dup[0])" in src
    assert "Lead already exists" in src


def test_tc_demo_003_duplicate_check_is_tenant_scoped() -> None:
    """The dup check filters by BOTH email AND tenant_id —
    different tenants can have leads with the same email
    (e.g. shared procurement contact)."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    assert "AND tenant_id = :tid" in src


# ─────────────────────────────────────────────────────────────────
# TC-DEMO-004 — Triggers sales agent
# ─────────────────────────────────────────────────────────────────


def test_tc_demo_004_triggers_sales_agent_on_new_lead() -> None:
    """After lead creation, _run_sales_agent_on_lead is called.
    Pin the import path so a refactor that moves the function
    is caught immediately."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    assert "from api.v1.sales import _run_sales_agent_on_lead" in src
    assert "_run_sales_agent_on_lead(default_tenant_id, lead_id)" in src


def test_tc_demo_004_sales_agent_trigger_is_non_blocking() -> None:
    """Sales agent failure (LLM timeout, etc.) must NOT block
    the public response. Pin the try/except so a refactor can't
    silently make the endpoint synchronous-only."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    sales_section = src.split(
        "from api.v1.sales import _run_sales_agent_on_lead", 1
    )[1][:600]
    assert "except Exception:" in sales_section
    assert (
        'logger.exception("Sales agent trigger failed (non-blocking)")'
        in sales_section
    )


def test_tc_demo_004_response_includes_agent_triggered_flag() -> None:
    """The response surfaces ``agent_triggered: bool`` so the
    landing page can confirm the qualification email is on
    its way."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    assert '"agent_triggered": agent_status is not None' in src


def test_tc_demo_004_email_notification_is_non_blocking() -> None:
    """Cross-pin with TC-EMAIL-001/002: the founder-notification
    email goes through send_email which has its own validation
    + fake-mail seam (Foundation #7 PR-B). A failed send MUST
    NOT block the public response."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    email_section = src.split("# 3. Send email notification", 1)[1][:400]
    assert "except Exception:" in email_section
    assert (
        'logger.exception("Email send failed but request was saved")'
        in email_section
    )


# ─────────────────────────────────────────────────────────────────
# Cross-pin — admin /admin/seed-demo is admin-gated
# ─────────────────────────────────────────────────────────────────


def test_admin_seed_demo_endpoint_is_admin_only() -> None:
    """The companion /admin/seed-demo endpoint MUST be admin-
    gated even though it lives in the same module as the
    public /demo-request. Foundation #8 false-green: silent
    public exposure would let anyone seed demo data into the
    tenant."""
    src = (REPO / "api" / "v1" / "demo.py").read_text(encoding="utf-8")
    assert (
        '@router.post("/admin/seed-demo", dependencies=[require_tenant_admin])'
        in src
    )
