"""Source-level guards for the 2026-09-13 migration/infra audit fixes.

Covers (see docs/bug_triage_skill.md Rule 8 / Rule 16):

* v6z19 repairs ``billing_subscriptions`` / ``cdc_triggers`` on databases
  whose bootstrap stamped past v4.0.0, and the deploy wrapper refuses a
  "green" migration when they are still missing.
* v6z16 ``downgrade`` no longer strips RLS from tables that were protected
  before that revision.
* The Cloud Run deploy script rolls the Celery worker/beat services.
* The secrets-rotation workflow cannot replace externally issued
  credentials with random bytes.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = REPO_ROOT / "migrations" / "versions"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_v6z19_repairs_billing_and_cdc_tables_idempotently() -> None:
    path = VERSIONS / "v6_z19_repair_billing_cdc_tables.py"
    mig = _load("v6z19", path)
    assert mig.revision == "v6z19_repair_billing_cdc"
    assert mig.down_revision == "v6z18_billing_cdc_state"
    assert len(mig.revision) <= 32

    executed: list[str] = []

    class _Op:
        @staticmethod
        def execute(sql: str) -> None:
            executed.append(" ".join(str(sql).split()))

    mig.op = _Op()
    mig.upgrade()
    joined = "\n".join(executed)

    assert "CREATE TABLE IF NOT EXISTS billing_subscriptions" in joined
    assert "CREATE TABLE IF NOT EXISTS cdc_triggers" in joined
    # Runtime SQL (core/billing/subscriptions.py) needs these columns.
    for column in (
        "provider_customer_id",
        "current_period_start",
        "current_period_end",
        "external_id",
    ):
        assert column in joined
    assert "ADD COLUMN IF NOT EXISTS provider_customer_id" in joined
    # Index audit (scripts/check_database_indexes.py): every FK needs a
    # leading index and no structurally duplicate indexes may remain.
    assert "ix_cdc_triggers_workflow_id ON cdc_triggers (workflow_id)" in joined
    assert "DROP INDEX IF EXISTS ix_billing_subscriptions_tenant_id" in joined
    assert "CREATE INDEX IF NOT EXISTS ix_billing_subscriptions_tenant_id" not in joined
    for table in ("billing_subscriptions", "cdc_triggers"):
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in joined
        assert f"CREATE POLICY {table}_tenant_isolation ON {table}" in joined
        assert (
            f"{table}_tenant_isolation ON {table} USING (tenant_id::text = "
            "current_setting('agenticorg.tenant_id', true))"
        ) in joined
    # Every statement must be re-runnable on a database that already has
    # the tables (production-shaped) — no bare CREATE.
    for stmt in executed:
        if stmt.startswith("CREATE TABLE") or stmt.startswith("CREATE INDEX"):
            assert "IF NOT EXISTS" in stmt, stmt
        if stmt.startswith("ALTER TABLE") and "ADD COLUMN" in stmt:
            assert "IF NOT EXISTS" in stmt, stmt


def test_alembic_wrapper_requires_billing_and_cdc_tables() -> None:
    wrapper = (REPO_ROOT / "scripts" / "alembic_migrate.py").read_text(encoding="utf-8")
    match = re.search(r"REQUIRED_RUNTIME_TABLES = frozenset\((.*?)\n\)", wrapper, re.S)
    assert match, "REQUIRED_RUNTIME_TABLES block missing"
    block = match.group(1)
    assert '"billing_subscriptions"' in block
    assert '"cdc_triggers"' in block


def test_v6z16_downgrade_keeps_rls_on_previously_protected_tables() -> None:
    mig = _load("v6z16", VERSIONS / "v6_z16_rls_tenant_coverage.py")

    previously = set(mig.PREVIOUSLY_COVERED_TABLES)
    assert previously <= set(mig.ALL_TABLES)
    # Spot-check the tables named in v4.8.0 baseline / v4.6.0 / v4.5.0.
    assert {
        "sso_configs",
        "invoices",
        "approval_policies",
        "departments",
        "agent_task_results",
    } <= previously

    executed: list[str] = []

    class _Op:
        @staticmethod
        def execute(sql: str) -> None:
            executed.append(" ".join(str(sql).split()))

    mig.op = _Op()
    mig.downgrade()
    joined = "\n".join(executed)

    for table in previously:
        assert f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY" not in joined
        assert f"DROP POLICY IF EXISTS {table}_tenant_isolation" not in joined
    # Newly covered tables are still reverted.
    assert "ALTER TABLE agents DISABLE ROW LEVEL SECURITY" in joined
    assert "ALTER TABLE workflow_runs DISABLE ROW LEVEL SECURITY" in joined
    assert len(executed) == len(mig.ALL_TABLES) - len(previously)


def test_deploy_script_rolls_worker_and_beat_with_the_api_image() -> None:
    script = (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")

    assert 'WORKER_SERVICE="${WORKER_SERVICE:-agenticorg-worker}"' in script
    assert 'BEAT_SERVICE="${BEAT_SERVICE:-agenticorg-beat}"' in script
    assert 'deploy_background_service "$WORKER_SERVICE" "worker"' in script
    assert 'deploy_background_service "$BEAT_SERVICE" "beat"' in script
    # Background services roll on the API image with commit metadata so the
    # revision readiness check verifies digest + AGENTICORG_GIT_SHA.
    assert (
        'update_service_no_traffic new_revision "$svc" "$API_IMAGE" '
        '"$BACKGROUND_UPDATE_ENV_VARS" "$label" "$API_IMAGE_DIGEST" "AGENTICORG_GIT_SHA"'
    ) in script
    # Worker/beat are promoted only after the public API health check and
    # before the UI is staged, and a failure rolls everything back.
    health_idx = script.index('poll_health_url "$HEALTH_URL" "public API"')
    worker_idx = script.index('deploy_background_service "$WORKER_SERVICE" "worker"')
    ui_stage_idx = script.index(
        'update_service_no_traffic UI_NEW_REVISION "$UI_SERVICE"', health_idx
    )
    assert health_idx < worker_idx < ui_stage_idx
    assert script.count("rollback_background_services") >= 4
    # The service sanity check covers all four services.
    assert 'for svc in "$API_SERVICE" "$UI_SERVICE" $WORKER_SERVICE $BEAT_SERVICE; do' in script


def test_secrets_rotation_refuses_externally_issued_credentials() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "secrets-rotation.yml").read_text(
        encoding="utf-8"
    )
    # No implicit default set that could rotate a provider-issued key.
    assert 'secrets=AGENTICORG_WEBHOOK_SECRET,GRANTEX_API_KEY' not in workflow
    assert "Pass the 'secrets' input explicitly" in workflow
    # Refusal list covers the vault-key fallback and Grantex-issued key.
    assert "AGENTICORG_SECRET_KEY)" in workflow
    assert "GRANTEX_API_KEY|" in workflow
    # Every runtime that reads secrets is rolled, not only the API.
    assert "agenticorg-api agenticorg-worker agenticorg-beat" in workflow
    # The workflow no longer promises a dual-read window it cannot provide.
    assert "24h dual-read window" not in workflow
