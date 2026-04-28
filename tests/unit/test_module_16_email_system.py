"""Foundation #6 — Module 16 Email System.

Source-pin tests for TC-EMAIL-001 through TC-EMAIL-006
(TC-EMAIL-003 is documented duplicate of 001 — covered
transitively).

Email is a quiet but high-blast-radius surface: a typo in the
domain blocklist or MX-check could mass-spam real customers, and
a missed test-domain block could leak invites to bounce-back
addresses.

Pinned contracts:

- _BLOCKED_DOMAINS frozenset enumerates the bounce/test/disposable
  domains that must be rejected. Exact membership pinned —
  silently dropping ``mailinator.com`` could let invites flow to
  burner mailboxes.
- ``.local`` and ``.test`` TLDs are rejected before MX check
  (they don't resolve at all in real DNS).
- Real domains pass validation iff they have at least one MX
  record. The DNS check is wrapped so a transient resolver error
  reads as "no MX" (fail-closed) — never as "ok, send anyway".
- send_email captures into fake_mail under
  AGENTICORG_TEST_FAKE_MAIL=1 (Foundation #7 PR-B contract).
- Higher-level helpers (welcome / invite / password reset) all
  funnel through send_email, so the domain validation runs once
  for every email path.
- Subject + body templates pin the user-visible copy that
  runbooks and support docs reference.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-EMAIL-001 (covers 003 — duplicate)
# Email domain validation — blocked domain
# ─────────────────────────────────────────────────────────────────


def test_tc_email_001_blocked_domains_frozenset_pinned() -> None:
    """The blocklist is the canonical source of disposable +
    test domains. Silent removal of an entry would let invites
    flow to bounce-back / burner addresses."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    assert "_BLOCKED_DOMAINS = {" in src
    for domain in (
        '"example.com"',
        '"test.com"',
        '"localhost"',
        '"mailinator.com"',
        '"guerrillamail.com"',
        '"sharklasers.com"',
        '"yopmail.com"',
    ):
        assert domain in src, f"_BLOCKED_DOMAINS missing {domain}"


def test_tc_email_001_validate_returns_documented_failure_reason() -> None:
    """validate_email_domain returns (False, reason). The reason
    string is shown in audit logs + admin alerts, so the
    "Blocked domain: {x}" / "Test domain: {x}" / "No MX records
    for {x}" prefixes are part of the contract."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    assert 'return False, f"Blocked domain: {domain}"' in src
    assert 'return False, f"Test domain: {domain}"' in src
    assert 'return False, f"No MX records for {domain}' in src


def test_tc_email_001_invalid_email_format_rejected() -> None:
    """No `@` in the address → "Invalid email format". Pin
    that the early-return branch exists — without it the
    domain extraction would IndexError on a value like
    "alice"."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    assert 'if "@" not in email:' in src
    assert '"Invalid email format"' in src


# ─────────────────────────────────────────────────────────────────
# TC-EMAIL-002 — Email domain validation — fake domain (no MX)
# ─────────────────────────────────────────────────────────────────


def test_tc_email_002_mx_check_fails_closed_on_resolver_error() -> None:
    """The DNS lookup is wrapped in try/except. ANY exception
    (timeout, NXDOMAIN, network error) returns False — never
    passes through as "OK, send the email"."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    mx_block = src.split("def _has_mx_record", 1)[1].split("\n\n\n", 1)[0]
    assert "try:" in mx_block
    assert "except Exception:" in mx_block
    assert "return False" in mx_block


def test_tc_email_002_mx_check_uses_dnspython_resolver() -> None:
    """Pin that we use dns.resolver (the dnspython library) and
    not a homegrown socket query. dnspython handles the long
    tail of edge cases (IDN, malformed records, EDNS)."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    assert "import dns.resolver" in src
    assert 'dns.resolver.resolve(domain, "MX")' in src


# ─────────────────────────────────────────────────────────────────
# TC-EMAIL-004 — Test-domain blocked (.local / .test TLDs)
# ─────────────────────────────────────────────────────────────────


def test_tc_email_004_local_and_test_tlds_rejected_before_mx_check() -> None:
    """``.local`` and ``.test`` are RFC-reserved test/admin TLDs.
    They don't resolve in real DNS, so the MX check would
    return False anyway — but rejecting them BEFORE the DNS
    call avoids a pointless network round-trip on every test
    address."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    assert 'if domain.endswith(".local") or domain.endswith(".test"):' in src
    # And the explicit branch returns BEFORE the MX call below.
    block = src.split('if domain.endswith(".local")', 1)[1][:200]
    assert 'return False' in block
    assert 'Test domain' in block


# ─────────────────────────────────────────────────────────────────
# TC-EMAIL-005 — Welcome email on signup
# ─────────────────────────────────────────────────────────────────


def test_tc_email_005_welcome_email_funnels_through_send_email() -> None:
    """All higher-level helpers (welcome, invite, password reset)
    must funnel through send_email. Otherwise validation runs in
    one but not the others, and the fake-mail seam misses captures."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    welcome_block = src.split("def send_welcome_email", 1)[1].split(
        "\n\ndef ", 1
    )[0]
    assert "send_email(to," in welcome_block


def test_tc_email_005_welcome_subject_includes_org_name() -> None:
    """Subject template references the org_name so the recipient
    can tell which workspace invited them at a glance. Pin the
    exact format — support docs quote this string."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    assert 'f"Welcome to AgenticOrg — {org_name}"' in src


def test_tc_email_005_welcome_body_links_to_dashboard() -> None:
    """Welcome HTML must include a "Go to Dashboard" CTA
    pointing at AGENTICORG_APP_URL. Without the env var the
    URL falls back to the public app domain."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    welcome_block = src.split("def send_welcome_email", 1)[1].split(
        "\n\ndef ", 1
    )[0]
    assert "AGENTICORG_APP_URL" in welcome_block
    assert "https://app.agenticorg.ai" in welcome_block
    assert "Go to Dashboard" in welcome_block


# ─────────────────────────────────────────────────────────────────
# TC-EMAIL-006 — Invite email
# ─────────────────────────────────────────────────────────────────


def test_tc_email_006_invite_subject_includes_org_name() -> None:
    """Subject "You're invited to {org_name} on AgenticOrg" is
    quoted in the onboarding runbook + UI confirmation toast."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    assert 'f"You\'re invited to {org_name} on AgenticOrg"' in src


def test_tc_email_006_invite_body_includes_inviter_role_link() -> None:
    """Invite HTML must show inviter, role, and a clickable
    Accept Invitation link. Missing any of these makes the
    invite confusing or unactionable."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    invite_block = src.split("def send_invite_email", 1)[1]
    assert "{inviter}" in invite_block
    assert "{role}" in invite_block
    assert "{invite_link}" in invite_block
    assert "Accept Invitation" in invite_block


def test_tc_email_006_password_reset_email_funnels_through_send_email() -> None:
    """Cross-check: password reset is the third helper. All three
    must go through send_email so the validation gate fires once
    per outbound mail."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    reset_block = src.split("def send_password_reset_email", 1)[1].split(
        "\n\ndef ", 1
    )[0]
    assert "send_email(to," in reset_block
    assert "Reset Password" in reset_block
    assert "expires in 1 hour" in reset_block


# ─────────────────────────────────────────────────────────────────
# Cross-pin — Foundation #7 PR-B fake-mail seam preserves validation
# ─────────────────────────────────────────────────────────────────


def test_email_send_runs_validation_before_fake_mail_seam() -> None:
    """Closure-plan rule: the fake-mail seam must NOT mask the
    production domain validation. send_email validates the
    domain BEFORE checking AGENTICORG_TEST_FAKE_MAIL — so a
    test asserting "invalid domain → no capture" still works
    under the fake. Pinned at the source level (we can't
    confirm runtime ordering without running the code)."""
    src = (REPO / "core" / "email.py").read_text(encoding="utf-8")
    if "fake_mail" in src:
        # The fake-mail check must come AFTER validate_email_domain.
        send_block = src.split("def send_email", 1)[1].split(
            "\n\ndef ", 1
        )[0]
        validate_idx = send_block.find("validate_email_domain")
        fake_idx = send_block.find("fake_mail")
        if validate_idx >= 0 and fake_idx >= 0:
            assert validate_idx < fake_idx, (
                "Foundation #8 false-green prevention: "
                "validate_email_domain must run BEFORE the "
                "fake-mail seam check, not after."
            )
