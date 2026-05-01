"""PR-G regression pins for the 2026-05-01 security hardening sweep.

Closes:

- SEC-010 (P2): production base image digest pinning.
- SEC-011 (P2): Bandit medium clean (run_beat / run_worker bind-all
  noqa annotations carry both Ruff S104 and Bandit B104).
- SEC-012 (P2): strict env validation rejects dev-default secrets
  outside local/dev/test, requires ≥32-char secret_key, and treats
  unknown environments as strict (default-safe).
- SEC-013 (P2): /api/v1/evals returns ``data_quality`` in the body
  AND an ``X-Data-Quality`` response header so callers can distinguish
  baseline demo data from measured benchmark output.

Tests use the live Settings class + the FastAPI router and rely on
the existing test conftest hermetic seam (no real env, no real DB).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────
# SEC-010: production base images pinned by digest
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "dockerfile",
    ["Dockerfile", "Dockerfile.ui", "Dockerfile.ui.cloudrun"],
)
def test_sec_010_dockerfile_pins_base_images_by_digest(dockerfile: str) -> None:
    """Every ``FROM`` line in production Dockerfiles must reference a
    digest. Pure tag-only references (``FROM python:3.14-slim``) are
    not reproducible across rebuilds and are rejected here.
    """
    root = Path(__file__).resolve().parent.parent.parent
    text = (root / dockerfile).read_text(encoding="utf-8")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("FROM ")
    ]
    assert lines, f"{dockerfile} has no FROM lines"
    for line in lines:
        # Allow internal images (asia-south1-docker.pkg.dev/...) that aren't
        # subject to Renovate but ARE pinned by tag in the registry.
        if "asia-south1-docker.pkg.dev" in line:
            continue
        assert "@sha256:" in line, (
            f"{dockerfile}: base image not digest-pinned: {line!r}. "
            f"Run `bash scripts/refresh_image_digests.sh` to update."
        )


def test_sec_010_refresh_script_exists_and_documented() -> None:
    """The digest-refresh helper must be present + executable."""
    root = Path(__file__).resolve().parent.parent.parent
    script = root / "scripts" / "refresh_image_digests.sh"
    assert script.exists(), "scripts/refresh_image_digests.sh missing"
    text = script.read_text(encoding="utf-8")
    assert "Renovate" in text or "Dependabot" in text
    assert "Docker Hub" in text


# ─────────────────────────────────────────────────────────────────
# SEC-011: Bandit medium clean — run_beat / run_worker
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "script_name", ["run_beat.py", "run_worker.py"]
)
def test_sec_011_cloud_run_health_bind_carries_both_lint_and_security_noqa(
    script_name: str,
) -> None:
    """The ``HTTPServer(('0.0.0.0', port), ...)`` line in the Cloud
    Run health stubs must carry BOTH ``# noqa: S104`` (for Ruff) AND
    ``# nosec B104`` (for Bandit). Bandit's parser recognises only
    ``nosec``; Ruff recognises only ``noqa``.
    """
    root = Path(__file__).resolve().parent.parent.parent
    text = (root / "scripts" / script_name).read_text(encoding="utf-8")
    bind_lines = [
        line for line in text.splitlines() if 'HTTPServer(("0.0.0.0"' in line
    ]
    assert bind_lines, f"{script_name} no longer has the 0.0.0.0 bind"
    for line in bind_lines:
        assert "noqa: S104" in line, (
            f"{script_name}: missing # noqa: S104 on 0.0.0.0 bind: {line!r}"
        )
        assert "nosec B104" in line, (
            f"{script_name}: missing # nosec B104 on 0.0.0.0 bind: {line!r}"
        )


# ─────────────────────────────────────────────────────────────────
# SEC-012: strict env validation
# ─────────────────────────────────────────────────────────────────


def _import_settings_cls():
    # Settings reads env vars at instantiation; import the class (not
    # the module-level singleton) so we can construct fresh instances
    # per test.
    from core.config import Settings
    return Settings


def test_sec_012_relaxed_env_accepts_default_secret() -> None:
    """``env=local|dev|test|ci`` keep working with the dev fallback."""
    Settings = _import_settings_cls()  # noqa: N806
    for env in ("local", "dev", "development", "test", "ci"):
        s = Settings(env=env, secret_key="dev-only-secret-key")
        assert s.secret_key == "dev-only-secret-key"


def test_sec_012_production_rejects_default_secret() -> None:
    Settings = _import_settings_cls()  # noqa: N806
    with pytest.raises(ValueError, match="AGENTICORG_SECRET_KEY"):
        Settings(env="production", secret_key="dev-only-secret-key")


def test_sec_012_staging_also_rejects_default_secret() -> None:
    """Staging must be strict — it's internet-accessible and used for
    enterprise security review."""
    Settings = _import_settings_cls()  # noqa: N806
    with pytest.raises(ValueError, match="AGENTICORG_SECRET_KEY"):
        Settings(env="staging", secret_key="dev-only-secret-key")


def test_sec_012_preview_also_rejects_default_secret() -> None:
    Settings = _import_settings_cls()  # noqa: N806
    with pytest.raises(ValueError, match="AGENTICORG_SECRET_KEY"):
        Settings(env="preview", secret_key="dev-only-secret-key")


def test_sec_012_unknown_env_is_strict_by_default() -> None:
    """An env value we don't recognise should default to STRICT, not
    relaxed — fail-closed posture."""
    Settings = _import_settings_cls()  # noqa: N806
    with pytest.raises(ValueError, match="AGENTICORG_SECRET_KEY"):
        Settings(env="qa-cluster-3", secret_key="dev-only-secret-key")


def test_sec_012_secret_key_min_length_enforced_in_strict_envs() -> None:
    Settings = _import_settings_cls()  # noqa: N806
    short_secret = "x" * 31  # one byte short of 32
    with pytest.raises(ValueError, match="32 chars"):
        Settings(env="production", secret_key=short_secret)


def test_sec_012_secret_key_length_ok_with_32() -> None:
    Settings = _import_settings_cls()  # noqa: N806
    s = Settings(
        env="production",
        secret_key="x" * 32,
        db_url="postgresql+asyncpg://app:pass@prod-db/agenticorg",
        redis_url="redis://prod-redis:6379/0",
    )
    assert len(s.secret_key) == 32


def test_sec_012_localhost_redis_rejected_in_strict_envs() -> None:
    Settings = _import_settings_cls()  # noqa: N806
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(
            env="production",
            secret_key="x" * 32,
            db_url="postgresql+asyncpg://app:pass@prod-db/agenticorg",
            redis_url="redis://localhost:6379/0",
        )


def test_sec_012_127001_redis_also_rejected_in_strict_envs() -> None:
    """``127.0.0.1`` is the same fallback as ``localhost``."""
    Settings = _import_settings_cls()  # noqa: N806
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(
            env="production",
            secret_key="x" * 32,
            db_url="postgresql+asyncpg://app:pass@prod-db/agenticorg",
            redis_url="redis://127.0.0.1:6379/0",
        )


def test_sec_012_dev_db_rejected_in_strict_envs() -> None:
    Settings = _import_settings_cls()  # noqa: N806
    with pytest.raises(ValueError, match="DB_URL"):
        Settings(
            env="production",
            secret_key="x" * 32,
            db_url=(
                "postgresql+asyncpg://agenticorg:agenticorg_dev@localhost:5432/"
                "agenticorg"
            ),
        )


# ─────────────────────────────────────────────────────────────────
# SEC-013: evals data_quality marker
# ─────────────────────────────────────────────────────────────────


def test_sec_013_evals_baseline_marks_data_quality_demo(monkeypatch) -> None:
    """When the on-disk scorecard is missing, /api/v1/evals must
    return ``data_quality: "demo"`` in the body AND set
    ``X-Data-Quality: demo`` on the response."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.v1 import evals

    # Force the scorecard path to a non-existent file so the baseline
    # branch fires, regardless of the runtime CWD.
    monkeypatch.setattr(
        evals,
        "_SCORECARD_PATH",
        Path("/nonexistent/scorecard.json"),
    )

    app = FastAPI()
    app.include_router(evals.router, prefix="/api/v1")
    client = TestClient(app)
    resp = client.get("/api/v1/evals")
    assert resp.status_code == 200
    assert resp.headers["X-Data-Quality"] == "demo"
    body = resp.json()
    assert body["data_quality"] == "demo"
    assert body.get("_is_baseline") is True


def test_sec_013_evals_measured_marks_data_quality_measured(
    monkeypatch, tmp_path
) -> None:
    """When a real scorecard exists on disk, the response must say
    ``measured`` — never silently fall back to baseline mid-flight."""
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.v1 import evals

    real_scorecard = tmp_path / "scorecard.json"
    real_scorecard.write_text(
        json.dumps(
            {
                "version": "v1",
                "platform_metrics": {"stp_rate": 0.9},
                "agent_aggregates": {},
                "case_results": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(evals, "_SCORECARD_PATH", real_scorecard)

    app = FastAPI()
    app.include_router(evals.router, prefix="/api/v1")
    client = TestClient(app)
    resp = client.get("/api/v1/evals")
    assert resp.status_code == 200
    assert resp.headers["X-Data-Quality"] == "measured"
    body = resp.json()
    assert body["data_quality"] == "measured"
    # The baseline marker must NOT appear when real data is served
    assert body.get("_is_baseline") is not True


# ─────────────────────────────────────────────────────────────────
# CSP regression — no unsafe-eval, no wildcard
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "config",
    ["ui/nginx.conf", "ui/nginx.cloudrun.conf.template"],
)
def test_csp_disallows_unsafe_eval_and_wildcard(config: str) -> None:
    """Cheap regression: even if SEC-009 (full inline removal) ships
    later, neither nginx config should ever contain ``unsafe-eval`` or
    a wildcard ``*`` in script-src or default-src."""
    root = Path(__file__).resolve().parent.parent.parent
    text = (root / config).read_text(encoding="utf-8")
    csp_lines = [
        line for line in text.splitlines()
        if "Content-Security-Policy" in line
    ]
    assert csp_lines, f"{config} has no CSP header"
    csp = " ".join(csp_lines)
    assert "'unsafe-eval'" not in csp, (
        f"{config}: CSP must not allow 'unsafe-eval'"
    )
    # Any ``script-src ... *`` would let any origin run code; same
    # for default-src.
    assert not re.search(r"script-src[^;]*\*[^;]*;", csp), (
        f"{config}: CSP script-src must not include wildcard"
    )
    assert not re.search(r"default-src[^;]*\*[^;]*;", csp), (
        f"{config}: CSP default-src must not include wildcard"
    )
