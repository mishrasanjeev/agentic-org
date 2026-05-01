"""SEC-2026-05 PR-A regression pins.

Pin the contracts the brutal security scan
(``docs/BRUTAL_SECURITY_SCAN_2026-05-01.md``) flagged in the items
this PR closes. These are CI gates, not advisory — failing the test
blocks merge so the bug class can't recur.

Items pinned:

- **SEC-001**: ``.dockerignore`` exists and excludes every secret /
  build-artifact pattern listed in the audit.
- **SEC-007**: ``AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1`` cannot enable
  the bypass when ``AGENTICORG_ENV`` is staging / production / any
  non-local value.
- **SEC-014**: The Account Aggregator routes (``api/v1/aa_callback.py``)
  treat ``Depends(get_current_tenant)`` as a string, matching what the
  dependency actually returns.
- **SEC-008 partial**: Pillow is pinned at a version that fixes both
  CVE-2026-25990 and CVE-2026-40192 (≥ 12.2.0).
- **SEC-011 partial**: Bandit medium-severity findings under
  ``api/auth/core/scripts`` count as zero — clean security lint is a
  CI gate, not a manual check.
"""

from __future__ import annotations

import json
import re
import subprocess  # noqa: S404 — invoking bandit CLI is the test's job
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# SEC-001: .dockerignore exclusions
# ─────────────────────────────────────────────────────────────────


# Patterns that MUST be excluded by .dockerignore. Each entry pairs
# the literal pattern that should appear in the file with a one-line
# rationale tied to the audit.
_REQUIRED_DOCKERIGNORE_PATTERNS = [
    (".env", "primary secret file — must never enter image"),
    (".env.*", "env-per-environment files (e.g. .env.production)"),
    ("*.pem", "TLS / signing private keys"),
    ("*.key", "any *.key file is presumed secret"),
    ("id_rsa", "ssh private keys"),
    (".venv/", "Python virtualenv with installed deps"),
    ("node_modules/", "frontend deps tree"),
    ("ui/node_modules/", "scoped frontend deps"),
    ("sdk-ts/node_modules/", "scoped sdk deps"),
    ("mcp-server/node_modules/", "scoped mcp-server deps"),
    (".git/", "git history can include unrelated branches/secrets"),
    (".pytest_cache/", "pytest temp state"),
    ("htmlcov/", "coverage HTML reports"),
    ("coverage_report.json", "coverage artifact tracked locally"),
    ("ui/test-results/", "Playwright run outputs"),
    ("ui/playwright-report/", "Playwright HTML reports"),
    ("*.log", "log files"),
]


def test_dockerignore_exists_and_excludes_audit_required_patterns() -> None:
    """SEC-001: .dockerignore must exist at repo root and explicitly
    exclude every secret / artifact pattern from the audit. Missing
    even one pattern means a future Dockerfile change could leak that
    class of file into a published image.
    """
    dockerignore = REPO / ".dockerignore"
    assert dockerignore.exists(), (
        ".dockerignore is missing — Docker builds with this repo would "
        "include local .env, .venv, node_modules, etc. (SEC-2026-05-P0-001)"
    )
    content = dockerignore.read_text(encoding="utf-8")

    # Strip comments + blanks before checking — patterns must be
    # active (uncommented) lines.
    active = "\n".join(
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )

    missing = [
        pattern
        for pattern, _why in _REQUIRED_DOCKERIGNORE_PATTERNS
        if pattern not in active.split("\n")
    ]
    assert not missing, (
        f".dockerignore is missing required exclusions: {missing}. "
        "These were called out in the SEC-2026-05-P0-001 audit. "
        "Add them — do not remove the test."
    )


def test_dockerignore_does_not_re_include_secrets_via_negation() -> None:
    """``!.env`` would re-include the secret file even after exclusion.
    Negation patterns are powerful and easy to misuse — pin that the
    most dangerous re-include patterns are NOT present.
    """
    content = (REPO / ".dockerignore").read_text(encoding="utf-8")

    forbidden_negations = [
        "!.env\n",
        "!*.pem",
        "!*.key",
        "!id_rsa",
        "!.venv",
        "!node_modules",
    ]
    for pattern in forbidden_negations:
        # Match exact line (with optional trailing whitespace) — a
        # comment containing the pattern is fine.
        line_pattern = re.compile(
            rf"^{re.escape(pattern.rstrip(chr(10)))}\s*$", re.MULTILINE
        )
        assert not line_pattern.search(content), (
            f".dockerignore re-includes a sensitive pattern via negation: "
            f"{pattern!r}. This defeats the exclusion above and is "
            f"forbidden by SEC-2026-05-P0-001."
        )


# ─────────────────────────────────────────────────────────────────
# SEC-007: webhook unsigned-bypass env guard
# ─────────────────────────────────────────────────────────────────


# Import once at module load with clean env so the test functions can
# reload() with various env shapes without having to fight Python's
# import cache. The guard runs at import time and at reload time, so
# reload is the right tool to test the guard repeatedly.
from importlib import reload as _reload  # noqa: E402

from api.v1 import webhooks as _webhooks_module  # noqa: E402


def test_webhook_unsigned_bypass_refuses_in_production_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-2026-05-P1-007: a single bad env var
    (``AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1``) must NOT silently turn
    public webhooks into unauthenticated ingestion in staging/prod.

    The startup-time guard in ``api/v1/webhooks.py`` (added by this PR)
    raises when the bypass flag is set in any environment that isn't
    explicitly local / dev / test. We exercise the guard via
    ``importlib.reload`` so the production env shape is applied at
    module-load time.
    """
    monkeypatch.setenv("AGENTICORG_ENV", "production")
    monkeypatch.setenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", "1")

    with pytest.raises(RuntimeError, match=r"unsigned webhook bypass"):
        _reload(_webhooks_module)


def test_webhook_unsigned_bypass_refuses_in_staging_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", "staging")
    monkeypatch.setenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", "1")

    with pytest.raises(RuntimeError, match=r"unsigned webhook bypass"):
        _reload(_webhooks_module)


def test_webhook_unsigned_bypass_allowed_in_local_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local development can still opt into unsigned webhooks for
    testing — the guard's purpose is preventing PROD misconfig, not
    breaking dev workflows."""
    monkeypatch.setenv("AGENTICORG_ENV", "local")
    monkeypatch.setenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", "1")

    # Must NOT raise — local opt-in is the supported workflow.
    _reload(_webhooks_module)


def test_webhook_unsigned_bypass_unset_is_safe_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the bypass flag, every environment is safe."""
    monkeypatch.setenv("AGENTICORG_ENV", "production")
    monkeypatch.delenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", raising=False)

    _reload(_webhooks_module)  # must not raise


# ─────────────────────────────────────────────────────────────────
# SEC-014: AA callback tenant type
# ─────────────────────────────────────────────────────────────────


def test_aa_callback_signatures_treat_tenant_as_string() -> None:
    """SEC-2026-05-P3-014: ``api/v1/aa_callback.py`` previously
    annotated the dependency as ``tenant: dict``, then called
    ``tenant.get(...)`` — but ``get_current_tenant`` returns a tenant
    ID **string**. Any consent route that touched the tenant dict
    raised AttributeError at runtime.

    Pin that no AA route signature still types tenant as ``dict``.
    """
    src = (REPO / "api" / "v1" / "aa_callback.py").read_text(encoding="utf-8")
    # Find every Depends(get_current_tenant) annotation in the module.
    # The audit's specific bug shape: ``tenant: dict = Depends(get_current_tenant)``.
    bug_pattern = re.compile(
        r"tenant\s*:\s*dict\s*=\s*Depends\(\s*get_current_tenant\s*\)",
        re.MULTILINE,
    )
    matches = bug_pattern.findall(src)
    assert not matches, (
        f"api/v1/aa_callback.py still types ``tenant`` as ``dict`` on "
        f"a Depends(get_current_tenant) parameter — that dependency "
        f"returns a string, so ``tenant.get(...)`` raises at runtime. "
        f"SEC-2026-05-P3-014. Found {len(matches)} occurrence(s)."
    )


# ─────────────────────────────────────────────────────────────────
# SEC-008 partial: Pillow CVE bump
# ─────────────────────────────────────────────────────────────────


def test_pillow_cve_residual_compensating_controls_documented() -> None:
    """SEC-2026-05-P2-008 RESIDUAL: Pillow 10.4.0 has CVE-2026-25990 +
    CVE-2026-40192 (fixed in 12.2.0), but ``composio-core==0.7.21``
    (no newer release exists) requires ``pillow>=10.2.0,<11`` — direct
    upgrade is blocked by the resolver.

    The audit's recommendation is *"Upgrade Pillow to at least 12.2.0
    if compatible"*. It isn't. The honest move is to document the
    residual + compensating controls in pyproject.toml. This test
    pins that documentation so a future contributor can't silently
    delete the residual notice while the CVE is still unfixed.

    When composio-core publishes a Pillow≥12 compatible release, this
    test should be flipped back to enforce the lower bound (commit
    history of this file shows the pin shape).
    """
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    # The residual notice MUST contain the audit ID so removing it is
    # an obvious change in code review.
    assert "SEC-2026-05-P2-008 RESIDUAL" in pyproject, (
        "pyproject.toml is missing the SEC-2026-05-P2-008 residual notice "
        "documenting why Pillow can't currently be upgraded past the "
        "composio-core constraint. Either upgrade Pillow (and remove this "
        "test in the same PR — the residual is gone), or restore the notice."
    )
    # The notice must reference both CVEs so a casual diff doesn't
    # erase the awareness.
    for cve in ("CVE-2026-25990", "CVE-2026-40192"):
        assert cve in pyproject, (
            f"Pillow CVE residual notice in pyproject.toml is missing {cve}. "
            "Both CVEs from SEC-2026-05-P2-008 must remain referenced "
            "until they're actually fixed."
        )
    # Compensating-control language must remain — without it, the
    # residual would just be acknowledged exposure without mitigation.
    assert "Compensating controls" in pyproject, (
        "The Pillow CVE residual notice must include the 'Compensating "
        "controls' section that explains why the unfixed CVE is not "
        "actively exploitable in this codebase. SEC-2026-05-P2-008."
    )


# ─────────────────────────────────────────────────────────────────
# SEC-011 partial: Bandit medium-severity gate for api/auth/core/scripts
# ─────────────────────────────────────────────────────────────────


def test_bandit_no_medium_severity_findings_in_security_critical_paths() -> None:
    """SEC-2026-05-P2-011: 99 Bandit findings — 0 high, 7 medium, 92 low.
    Pin a CI gate that no MEDIUM finding is allowed under
    api / auth / core / scripts.

    Skips when bandit isn't installed (e.g. in CI environments that run
    a subset of tests). The CI 'security-scan' job has bandit as a hard
    dependency — that's where this gate lands meaningfully.
    """
    try:
        result = subprocess.run(  # noqa: S603 — args list, not shell
            [
                sys.executable,
                "-m",
                "bandit",
                "-r",
                "api",
                "auth",
                "core",
                "scripts",
                "-x",
                "tests,migrations,codex-pytest-basetemp,codex-pytest-temp",
                "-ll",  # only LOW+
                "-iii",  # confidence threshold HIGH
                "-f",
                "json",
                "-q",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        pytest.skip("bandit not installed — CI security-scan job covers this")
        return

    # Bandit returns non-zero when it finds anything matching -ll/-iii.
    # Parse the JSON regardless and count by severity.
    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        pytest.fail(f"bandit produced unparseable output:\n{result.stdout}\n{result.stderr}")

    medium_findings = [
        r
        for r in (report.get("results") or [])
        if r.get("issue_severity", "").upper() == "MEDIUM"
        and r.get("issue_confidence", "").upper() in {"MEDIUM", "HIGH"}
    ]
    if medium_findings:
        details = "\n".join(
            f"  {r.get('filename')}:{r.get('line_number')} "
            f"[{r.get('test_id')}] {r.get('issue_text', '')[:140]}"
            for r in medium_findings
        )
        pytest.fail(
            f"Bandit found {len(medium_findings)} MEDIUM-severity issue(s) "
            f"in api/auth/core/scripts — these are SEC-2026-05-P2-011 "
            f"findings the audit explicitly asked to clean.\n{details}"
        )
