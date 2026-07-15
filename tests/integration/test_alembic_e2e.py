"""End-to-end Alembic migration verification.

Runs against the CI Postgres service. Three scenarios exercise
``scripts/alembic_migrate.py`` against realistic DB shapes:

1. Empty DB -> wrapper must create the ORM baseline, stamp
   ``v480_baseline``, and upgrade to the current head. The Alembic
   version chain starts after the historical raw-SQL baseline, so this
   bootstrap belongs in the wrapper rather than in tribal local setup.

2. Legacy-shaped DB (full schema from ``ORMBase.metadata.create_all``,
   no ``alembic_version`` row) -> wrapper must stamp v480_baseline
   and return cleanly.

3. Already-managed DB (subsequent wrapper invocation) -> wrapper
   takes the ``upgrade head`` path, stays idempotent, and exits 0.

Skipped when Postgres is not reachable (e.g. local Windows machine
without Docker). CI ``integration-tests`` always has Postgres.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

_DB_URL = os.getenv(
    "AGENTICORG_DB_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)
_SYNC_URL = _DB_URL.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")
_READINESS_TABLES = {
    "capability_readiness_records",
    "capability_evidence_records",
    "capability_promotion_events",
}
_READINESS_TRIGGERS = {
    "capability_readiness_scope",
    "capability_readiness_transition_guard",
    "capability_readiness_records_no_delete",
    "capability_evidence_scope",
    "capability_evidence_records_immutable",
    "capability_promotion_chain",
    "capability_promotion_events_immutable",
}


def _pg_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="Postgres is not reachable; skipping Alembic e2e test",
)


def _reset_schema() -> None:
    engine = create_engine(_SYNC_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    engine.dispose()


def _build_legacy_schema() -> None:
    """Populate a realistic legacy schema: every ORM table, no
    ``alembic_version`` row. Matches the shape of a prod DB that
    was bootstrapped by ``init_db()`` before Alembic took over."""
    import core.models  # noqa: F401 — register every model
    from core.models.base import BaseModel

    engine = create_engine(_SYNC_URL)
    BaseModel.metadata.create_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _reset_after_module():
    """Leave the schema clean at module exit so downstream integration
    tests (which rely on ORMBase.metadata.create_all) do not see a
    partially-populated state left over from this module's manipulations."""
    yield
    _reset_schema()


def _run_migrate_wrapper() -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENTICORG_DB_URL"] = _DB_URL
    env.setdefault("AGENTICORG_SECRET_KEY", "ci-test-secret-key-minimum-16")
    return subprocess.run(  # noqa: S603
        [sys.executable, "scripts/alembic_migrate.py"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


def _table_names() -> set[str]:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
    engine.dispose()
    return names


def _current_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    expected_head = ScriptDirectory.from_config(cfg).get_current_head()
    assert expected_head, "Alembic has no head revision"
    return expected_head


def _alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _SYNC_URL)
    return cfg


def _assert_readiness_controls() -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        assert _READINESS_TABLES <= set(inspect(conn).get_table_names())
        triggers = set(
            conn.execute(
                text("""
                    SELECT t.tgname
                    FROM pg_trigger t
                    JOIN pg_class c ON c.oid = t.tgrelid
                    WHERE NOT t.tgisinternal
                      AND c.relname IN (
                        'capability_readiness_records',
                        'capability_evidence_records',
                        'capability_promotion_events'
                      )
                """)
            ).scalars()
        )
        assert _READINESS_TRIGGERS <= triggers
        policies = set(
            conn.execute(
                text("""
                    SELECT tablename
                    FROM pg_policies
                    WHERE policyname LIKE 'capability_%_scope_isolation'
                """)
            ).scalars()
        )
        assert policies == _READINESS_TABLES
        flags = conn.execute(
            text("""
                SELECT relname, relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relname IN (
                  'capability_readiness_records',
                  'capability_evidence_records',
                  'capability_promotion_events'
                )
            """)
        ).all()
        assert len(flags) == 3
        assert all(enabled and forced for _, enabled, forced in flags)
        indexes = set(
            conn.execute(
                text("""
                    SELECT indexname FROM pg_indexes
                    WHERE indexname IN (
                      'ix_capability_readiness_company_id',
                      'ix_capability_evidence_company_id',
                      'ix_capability_promotion_company_id'
                    )
                """)
            ).scalars()
        )
        assert indexes == {
            "ix_capability_readiness_company_id",
            "ix_capability_evidence_company_id",
            "ix_capability_promotion_company_id",
        }
    engine.dispose()


def _assert_connector_config_controls() -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        policies = set(
            conn.execute(
                text("""
                    SELECT policyname
                    FROM pg_policies
                    WHERE tablename = 'connector_configs'
                """)
            ).scalars()
        )
        assert policies == {"connector_configs_scope_isolation"}
        flags = conn.execute(
            text("""
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relname = 'connector_configs'
            """)
        ).one()
        assert flags == (True, True)
        indexes = set(
            conn.execute(
                text("""
                    SELECT indexname
                    FROM pg_indexes
                    WHERE tablename = 'connector_configs'
                """)
            ).scalars()
        )
        assert {
            "uq_connector_configs_tenant_global",
            "uq_connector_configs_tenant_company",
            "ix_connector_configs_tenant_company",
        } <= indexes
        triggers = set(
            conn.execute(
                text("""
                    SELECT t.tgname
                    FROM pg_trigger t
                    JOIN pg_class c ON c.oid = t.tgrelid
                    WHERE NOT t.tgisinternal
                      AND c.relname = 'connector_configs'
                """)
            ).scalars()
        )
        assert "connector_config_company_scope_guard_trigger" in triggers
    engine.dispose()


def _seed_tenant_and_companies(tenant_id: uuid.UUID, company_ids: list[uuid.UUID]) -> None:
    engine = create_engine(_SYNC_URL)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO tenants (id, name, slug, plan, data_region, settings, byok_kek_resource)
                VALUES (:id, :name, :slug, 'enterprise', 'IN', '{}'::jsonb, '')
            """),
            {"id": tenant_id, "name": f"tenant-{tenant_id.hex[:8]}", "slug": f"tenant-{tenant_id.hex}"},
        )
        for index, company_id in enumerate(company_ids):
            conn.execute(
                text("""
                    INSERT INTO companies (
                      id, tenant_id, name, pan, fy_start_month, fy_end_month,
                      gst_auto_file, is_active, subscription_status,
                      document_vault_enabled, gstn_auto_upload, user_roles, currency
                    ) VALUES (
                      :id, :tenant_id, :name, :pan, '04', '03', false, true,
                      'active', true, false, '{}'::jsonb, 'INR'
                    )
                """),
                {
                    "id": company_id,
                    "tenant_id": tenant_id,
                    "name": f"company-{company_id.hex[:8]}",
                    "pan": f"ABCD{index:04d}EF"[:10],
                },
            )
    engine.dispose()


def _insert_readiness_record(
    conn,
    *,
    record_id: uuid.UUID,
    tenant_id: uuid.UUID,
    company_id: uuid.UUID | None,
    capability_id: str,
    title: str,
) -> None:
    conn.execute(
        text("""
            INSERT INTO capability_readiness_records (
              id, tenant_id, company_id, capability_id, domain, title, description,
              scope_disposition, scope_details, required_gate_ids,
              internal_maturity_state, release_gate_state, public_availability_state,
              claim_state, gate_results, permitted_claim_ids, owners, approver_ids,
              traceability, limitations, review_expires_at, current_promotion_sequence,
              created_by, updated_by
            ) VALUES (
              :id, :tenant_id, :company_id, :capability_id, 'hr', :title, 'trigger test',
              'Mandatory', '{}'::jsonb, '[]'::jsonb, 'Missing', 'Blocked', 'Unavailable',
              'Hidden', '{}'::jsonb, '[]'::jsonb, '{}'::jsonb, '[]'::jsonb,
              '{}'::jsonb, '[]'::jsonb, :review_expires_at, 0, 'fixture', 'fixture'
            )
        """),
        {
            "id": record_id,
            "tenant_id": tenant_id,
            "company_id": company_id,
            "capability_id": capability_id,
            "title": title,
            "review_expires_at": datetime.now(UTC) + timedelta(days=30),
        },
    )


def _expect_db_rejection(statement: str, params: dict[str, object], message: str) -> None:
    engine = create_engine(_SYNC_URL)
    conn = engine.connect()
    transaction = conn.begin()
    try:
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text(statement), params)
        assert message in str(exc_info.value).lower()
    finally:
        transaction.rollback()
        conn.close()
        engine.dispose()


def test_empty_db_bootstraps_baseline_then_upgrades_to_head():
    _reset_schema()
    assert _table_names() == set()

    result = _run_migrate_wrapper()
    assert "empty database" in result.stderr
    assert "creating ORM baseline" in result.stderr

    tables = _table_names()
    assert "tenants" in tables
    assert "connector_configs" in tables
    assert "alembic_version" in tables
    assert _READINESS_TABLES <= tables

    with create_engine(_SYNC_URL).connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == _current_head()
    _assert_readiness_controls()
    _assert_connector_config_controls()


def test_legacy_db_gets_stamped_at_baseline():
    """A legacy-shaped DB (no alembic_version) must be stamped at the
    baseline and then upgraded to the current head.

    The wrapper runs ``stamp v480_baseline`` followed by
    ``upgrade head``, so the resulting version is whatever the Alembic
    head is today, not necessarily the baseline. Read the expected
    head dynamically so this test does not regress every time a new
    migration file is added."""
    expected_head = _current_head()

    _reset_schema()
    _build_legacy_schema()
    tables = _table_names()
    assert "connector_configs" in tables  # probe table for the wrapper
    assert "alembic_version" not in tables

    result = _run_migrate_wrapper()
    assert "legacy schema detected" in result.stderr

    with create_engine(_SYNC_URL).connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == expected_head
    _assert_readiness_controls()
    _assert_connector_config_controls()


def test_already_managed_db_is_noop():
    """A DB that already has alembic_version must take the ``upgrade
    head`` branch, complete cleanly, and exit 0."""
    if "alembic_version" not in _table_names():
        _reset_schema()
        _build_legacy_schema()
        _run_migrate_wrapper()

    result = _run_migrate_wrapper()
    assert result.returncode == 0
    assert "alembic_version present" in result.stderr
    assert "alembic_version" in _table_names()
    _assert_readiness_controls()
    _assert_connector_config_controls()


def test_readiness_migration_downgrade_upgrade_round_trip():
    if "alembic_version" not in _table_names():
        _reset_schema()
        _run_migrate_wrapper()

    command.downgrade(_alembic_cfg(), "v6z4_fk_index_coverage")
    assert not (_READINESS_TABLES & _table_names())
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        functions = set(
            conn.execute(
                text("""
                    SELECT proname FROM pg_proc
                    WHERE proname IN (
                      'guard_capability_readiness_transition',
                      'prevent_capability_ledger_mutation',
                      'guard_capability_promotion_chain',
                      'guard_capability_ledger_scope'
                    )
                """)
            ).scalars()
        )
    engine.dispose()
    assert functions == set()

    command.upgrade(_alembic_cfg(), "head")
    _assert_readiness_controls()
    _assert_connector_config_controls()


def test_connector_config_migration_replaces_legacy_policies_and_round_trips():
    if "alembic_version" not in _table_names():
        _reset_schema()
        _build_legacy_schema()
        command.stamp(_alembic_cfg(), "v480_baseline")
        command.upgrade(_alembic_cfg(), "head")

    command.downgrade(_alembic_cfg(), "v6z5_readiness_ledger")
    engine = create_engine(_SYNC_URL)
    with engine.begin() as conn:
        policies = set(
            conn.execute(
                text("""
                    SELECT policyname FROM pg_policies
                    WHERE tablename = 'connector_configs'
                """)
            ).scalars()
        )
        assert policies == {"tenant_isolation"}
        flags = conn.execute(
            text("""
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class WHERE relname = 'connector_configs'
            """)
        ).one()
        assert flags == (True, False)
        conn.execute(
            text("""
                CREATE POLICY connector_configs_tenant_isolation
                ON connector_configs
                USING (
                    tenant_id::text = current_setting('agenticorg.tenant_id', true)
                )
            """)
        )
    engine.dispose()

    command.upgrade(_alembic_cfg(), "head")
    _assert_connector_config_controls()


def test_readiness_database_triggers_enforce_scope_append_only_and_chain():
    if not (_READINESS_TABLES <= _table_names()):
        command.upgrade(_alembic_cfg(), "head")
    tenant_id = uuid.uuid4()
    company_id = uuid.uuid4()
    record_id = uuid.uuid4()
    event_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    _seed_tenant_and_companies(tenant_id, [company_id])

    engine = create_engine(_SYNC_URL)
    with engine.begin() as conn:
        _insert_readiness_record(
            conn,
            record_id=record_id,
            tenant_id=tenant_id,
            company_id=company_id,
            capability_id="HR-C94",
            title="Trigger proof",
        )
        conn.execute(
            text("""
                INSERT INTO capability_promotion_events (
                  id, readiness_record_id, tenant_id, company_id, capability_id,
                  sequence, event_type, to_internal_maturity, to_release_gate,
                  to_public_availability, to_claim_state, scope_disposition_snapshot,
                  gate_results_snapshot, evidence_snapshot, ownership_snapshot,
                  traceability_snapshot, permitted_claim_ids, limitations,
                  requested_by, approved_by, reason, policy_version, event_hash
                ) VALUES (
                  :id, :record_id, :tenant_id, :company_id, 'HR-C94', 0, 'registered',
                  'Missing', 'Blocked', 'Unavailable', 'Hidden', 'Mandatory', '{}'::jsonb,
                  '[]'::jsonb, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '[]'::jsonb,
                  'fixture', 'fixture', 'registered', 'test-policy', :event_hash
                )
            """),
            {
                "id": event_id,
                "record_id": record_id,
                "tenant_id": tenant_id,
                "company_id": company_id,
                "event_hash": "a" * 64,
            },
        )
        now = datetime.now(UTC)
        conn.execute(
            text("""
                INSERT INTO capability_evidence_records (
                  id, readiness_record_id, tenant_id, company_id, capability_id,
                  evidence_version, evidence_type, artifact_uri, sha256_checksum,
                  environment, provider_account_class, product_version, source_commit_sha,
                  observed_at, reviewed_at, expires_at, reviewed_by, submitted_by,
                  supports_gate_ids, supports_claim_ids, evidence_metadata, created_by
                ) VALUES (
                  :id, :record_id, :tenant_id, :company_id, 'HR-C94', 'proof-v1',
                  'implementation_test', 'evidence://trigger-test', :checksum, 'test',
                  'fixture', 'test', :commit_sha, :observed_at, :reviewed_at, :expires_at,
                  'reviewer', 'fixture', '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, 'fixture'
                )
            """),
            {
                "id": evidence_id,
                "record_id": record_id,
                "tenant_id": tenant_id,
                "company_id": company_id,
                "checksum": "b" * 64,
                "commit_sha": "c" * 40,
                "observed_at": now - timedelta(hours=2),
                "reviewed_at": now - timedelta(hours=1),
                "expires_at": now + timedelta(days=1),
            },
        )
    engine.dispose()

    _expect_db_rejection(
        "UPDATE capability_evidence_records SET reviewed_by = 'attacker' WHERE id = :id",
        {"id": evidence_id},
        "append-only",
    )
    _expect_db_rejection(
        "UPDATE capability_promotion_events SET reason = 'tampered' WHERE id = :id",
        {"id": event_id},
        "append-only",
    )
    _expect_db_rejection(
        "DELETE FROM capability_readiness_records WHERE id = :id",
        {"id": record_id},
        "append-only",
    )
    _expect_db_rejection(
        "UPDATE capability_readiness_records SET capability_id = 'HR-C95' WHERE id = :id",
        {"id": record_id},
        "identity is immutable",
    )
    _expect_db_rejection(
        "UPDATE capability_readiness_records SET limitations = '[\"changed\"]'::jsonb WHERE id = :id",
        {"id": record_id},
        "matching immutable governance event",
    )
    _expect_db_rejection(
        """
          INSERT INTO capability_evidence_records (
            id, readiness_record_id, tenant_id, company_id, capability_id,
            evidence_version, evidence_type, artifact_uri, sha256_checksum,
            environment, provider_account_class, product_version, source_commit_sha,
            observed_at, reviewed_at, expires_at, reviewed_by, created_by
          ) VALUES (
            :id, :record_id, :tenant_id, :company_id, 'HR-C00', 'wrong-scope-v1',
            'implementation_test', 'evidence://wrong-scope', :checksum, 'test',
            'fixture', 'test', :commit_sha, :observed_at, :reviewed_at, :expires_at,
            'reviewer', 'fixture'
          )
        """,
        {
            "id": uuid.uuid4(),
            "record_id": record_id,
            "tenant_id": tenant_id,
            "company_id": company_id,
            "checksum": "d" * 64,
            "commit_sha": "e" * 40,
            "observed_at": datetime.now(UTC) - timedelta(hours=2),
            "reviewed_at": datetime.now(UTC) - timedelta(hours=1),
            "expires_at": datetime.now(UTC) + timedelta(days=1),
        },
        "does not match readiness scope",
    )
    _expect_db_rejection(
        """
          INSERT INTO capability_promotion_events (
            id, readiness_record_id, tenant_id, company_id, capability_id,
            sequence, event_type, from_internal_maturity, to_internal_maturity,
            from_release_gate, to_release_gate, from_public_availability,
            to_public_availability, from_claim_state, to_claim_state,
            scope_disposition_snapshot, gate_results_snapshot, evidence_snapshot,
            ownership_snapshot, traceability_snapshot, permitted_claim_ids, limitations,
            requested_by, approved_by, reason, policy_version, previous_event_hash, event_hash
          ) VALUES (
            :id, :record_id, :tenant_id, :company_id, 'HR-C94', 2, 'promoted',
            'Missing', 'Implemented', 'Blocked', 'NotAssessed', 'Unavailable',
            'Unavailable', 'Hidden', 'Hidden', 'Mandatory', '{}'::jsonb, '[]'::jsonb,
            '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '[]'::jsonb, 'fixture', 'fixture',
            'gap', 'test-policy', :previous_hash, :event_hash
          )
        """,
        {
            "id": uuid.uuid4(),
            "record_id": record_id,
            "tenant_id": tenant_id,
            "company_id": company_id,
            "previous_hash": "a" * 64,
            "event_hash": "f" * 64,
        },
        "not the next readiness sequence",
    )

    engine = create_engine(_SYNC_URL)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO capability_promotion_events (
                  id, readiness_record_id, tenant_id, company_id, capability_id,
                  sequence, event_type, from_internal_maturity, to_internal_maturity,
                  from_release_gate, to_release_gate, from_public_availability,
                  to_public_availability, from_claim_state, to_claim_state,
                  scope_disposition_snapshot, gate_results_snapshot, evidence_snapshot,
                  ownership_snapshot, traceability_snapshot, permitted_claim_ids, limitations,
                  requested_by, approved_by, reason, policy_version, previous_event_hash, event_hash
                ) VALUES (
                  :id, :record_id, :tenant_id, :company_id, 'HR-C94', 1, 'promoted',
                  'Missing', 'Implemented', 'Blocked', 'NotAssessed', 'Unavailable',
                  'Unavailable', 'Hidden', 'Hidden', 'Mandatory', '{}'::jsonb, '[]'::jsonb,
                  jsonb_build_object(
                    'owners', '{}'::jsonb,
                    'approver_ids', '[]'::jsonb,
                    'review_expires_at', (
                      SELECT review_expires_at::text
                      FROM capability_readiness_records WHERE id = :record_id
                    )
                  ), '{}'::jsonb, '[]'::jsonb, '[\"review required\"]'::jsonb,
                  'operator', 'fixture', 'valid transition', 'test-policy', :previous_hash, :event_hash
                )
            """),
            {
                "id": uuid.uuid4(),
                "record_id": record_id,
                "tenant_id": tenant_id,
                "company_id": company_id,
                "previous_hash": "a" * 64,
                "event_hash": "1" * 64,
            },
        )
        result = conn.execute(
            text("""
                UPDATE capability_readiness_records
                SET internal_maturity_state = 'Implemented', release_gate_state = 'NotAssessed',
                    limitations = '[\"review required\"]'::jsonb,
                    current_promotion_sequence = 1, updated_by = 'operator', updated_at = now()
                WHERE id = :id
                RETURNING current_promotion_sequence
            """),
            {"id": record_id},
        )
        assert result.scalar_one() == 1
    engine.dispose()


def test_readiness_rls_isolates_tenant_and_company_reads_and_writes():
    if not (_READINESS_TABLES <= _table_names()):
        command.upgrade(_alembic_cfg(), "head")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    company_a, company_b = uuid.uuid4(), uuid.uuid4()
    _seed_tenant_and_companies(tenant_a, [company_a, company_b])
    _seed_tenant_and_companies(tenant_b, [])
    engine = create_engine(_SYNC_URL)
    with engine.begin() as conn:
        for record_id, tenant_id, company_id, title in (
            (uuid.uuid4(), tenant_a, None, "tenant-a-global"),
            (uuid.uuid4(), tenant_a, company_a, "tenant-a-company-a"),
            (uuid.uuid4(), tenant_a, company_b, "tenant-a-company-b"),
            (uuid.uuid4(), tenant_b, None, "tenant-b-global"),
        ):
            _insert_readiness_record(
                conn,
                record_id=record_id,
                tenant_id=tenant_id,
                company_id=company_id,
                capability_id="HR-C96",
                title=title,
            )
        conn.execute(text("DROP ROLE IF EXISTS readiness_rls_probe"))
        conn.execute(text("CREATE ROLE readiness_rls_probe NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO readiness_rls_probe"))
        conn.execute(text("GRANT SELECT, INSERT ON capability_readiness_records TO readiness_rls_probe"))

    def visible_titles(tenant_id: uuid.UUID | None, company_id: uuid.UUID | None = None) -> set[str]:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE readiness_rls_probe"))
            if tenant_id is not None:
                conn.execute(
                    text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(tenant_id)},
                )
                conn.execute(
                    text("SELECT set_config('agenticorg.company_id', :company_id, true)"),
                    {"company_id": str(company_id) if company_id else ""},
                )
            return set(conn.execute(text("SELECT title FROM capability_readiness_records")).scalars())

    try:
        assert visible_titles(None) == set()
        assert visible_titles(tenant_a) == {"tenant-a-global"}
        assert visible_titles(tenant_a, company_a) == {"tenant-a-global", "tenant-a-company-a"}
        assert visible_titles(tenant_a, company_b) == {"tenant-a-global", "tenant-a-company-b"}
        assert visible_titles(tenant_b) == {"tenant-b-global"}

        conn = engine.connect()
        transaction = conn.begin()
        try:
            conn.execute(text("SET LOCAL ROLE readiness_rls_probe"))
            conn.execute(
                text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_a)},
            )
            conn.execute(text("SELECT set_config('agenticorg.company_id', '', true)"))
            with pytest.raises(DBAPIError, match="row-level security"):
                _insert_readiness_record(
                    conn,
                    record_id=uuid.uuid4(),
                    tenant_id=tenant_b,
                    company_id=None,
                    capability_id="HR-C97",
                    title="cross-tenant-write",
                )
        finally:
            transaction.rollback()
            conn.close()

        def scoped_write(
            context_company_id: uuid.UUID | None,
            row_company_id: uuid.UUID | None,
            *,
            capability_id: str,
            rejected: bool,
        ) -> None:
            scoped_conn = engine.connect()
            scoped_transaction = scoped_conn.begin()
            try:
                scoped_conn.execute(text("SET LOCAL ROLE readiness_rls_probe"))
                scoped_conn.execute(
                    text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(tenant_a)},
                )
                scoped_conn.execute(
                    text("SELECT set_config('agenticorg.company_id', :company_id, true)"),
                    {"company_id": str(context_company_id) if context_company_id else ""},
                )

                def insert() -> None:
                    _insert_readiness_record(
                        scoped_conn,
                        record_id=uuid.uuid4(),
                        tenant_id=tenant_a,
                        company_id=row_company_id,
                        capability_id=capability_id,
                        title=f"exact-write-{capability_id}",
                    )

                if rejected:
                    with pytest.raises(DBAPIError, match="row-level security"):
                        insert()
                else:
                    insert()
            finally:
                scoped_transaction.rollback()
                scoped_conn.close()

        scoped_write(company_a, None, capability_id="HR-C98", rejected=True)
        scoped_write(None, company_a, capability_id="HR-C99", rejected=True)
        scoped_write(company_a, company_a, capability_id="HR-C98", rejected=False)
        scoped_write(None, None, capability_id="HR-C99", rejected=False)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP OWNED BY readiness_rls_probe"))
            conn.execute(text("DROP ROLE readiness_rls_probe"))
        engine.dispose()


def test_connector_config_rls_isolates_exact_global_and_company_scopes():
    _assert_connector_config_controls()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    company_a, company_b = uuid.uuid4(), uuid.uuid4()
    _seed_tenant_and_companies(tenant_a, [company_a, company_b])
    _seed_tenant_and_companies(tenant_b, [])
    engine = create_engine(_SYNC_URL)

    def insert_config(
        conn,
        *,
        tenant_id: uuid.UUID,
        company_id: uuid.UUID | None,
        connector_name: str,
        display_name: str,
    ) -> None:
        conn.execute(
            text("""
                INSERT INTO connector_configs (
                    id, tenant_id, company_id, connector_name, display_name,
                    auth_type, status
                ) VALUES (
                    :id, :tenant_id, :company_id, :connector_name, :display_name,
                    'api_key', 'configured'
                )
            """),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "company_id": company_id,
                "connector_name": connector_name,
                "display_name": display_name,
            },
        )

    with engine.begin() as conn:
        insert_config(
            conn,
            tenant_id=tenant_a,
            company_id=None,
            connector_name="scope_probe",
            display_name="tenant-a-global",
        )
        insert_config(
            conn,
            tenant_id=tenant_a,
            company_id=company_a,
            connector_name="scope_probe",
            display_name="tenant-a-company-a",
        )
        insert_config(
            conn,
            tenant_id=tenant_a,
            company_id=company_b,
            connector_name="scope_probe",
            display_name="tenant-a-company-b",
        )
        insert_config(
            conn,
            tenant_id=tenant_b,
            company_id=None,
            connector_name="scope_probe",
            display_name="tenant-b-global",
        )
        conn.execute(text("DROP ROLE IF EXISTS connector_config_rls_probe"))
        conn.execute(
            text(
                "CREATE ROLE connector_config_rls_probe "
                "NOLOGIN NOSUPERUSER NOBYPASSRLS"
            )
        )
        conn.execute(text("GRANT USAGE ON SCHEMA public TO connector_config_rls_probe"))
        conn.execute(
            text(
                "GRANT SELECT, INSERT ON connector_configs "
                "TO connector_config_rls_probe"
            )
        )

    def visible_names(
        tenant_id: uuid.UUID | None,
        company_id: uuid.UUID | None = None,
    ) -> set[str]:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE connector_config_rls_probe"))
            if tenant_id is not None:
                conn.execute(
                    text(
                        "SELECT set_config("
                        "'agenticorg.tenant_id', :tenant_id, true)"
                    ),
                    {"tenant_id": str(tenant_id)},
                )
                conn.execute(
                    text(
                        "SELECT set_config("
                        "'agenticorg.company_id', :company_id, true)"
                    ),
                    {"company_id": str(company_id) if company_id else ""},
                )
            return set(
                conn.execute(
                    text("SELECT display_name FROM connector_configs")
                ).scalars()
            )

    try:
        assert visible_names(None) == set()
        assert visible_names(tenant_a) == {"tenant-a-global"}
        assert visible_names(tenant_a, company_a) == {"tenant-a-company-a"}
        assert visible_names(tenant_a, company_b) == {"tenant-a-company-b"}
        assert visible_names(tenant_b) == {"tenant-b-global"}

        def scoped_write(
            context_company_id: uuid.UUID | None,
            row_company_id: uuid.UUID | None,
            *,
            connector_name: str,
            rejected: bool,
        ) -> None:
            scoped_conn = engine.connect()
            transaction = scoped_conn.begin()
            try:
                scoped_conn.execute(text("SET LOCAL ROLE connector_config_rls_probe"))
                scoped_conn.execute(
                    text(
                        "SELECT set_config("
                        "'agenticorg.tenant_id', :tenant_id, true)"
                    ),
                    {"tenant_id": str(tenant_a)},
                )
                scoped_conn.execute(
                    text(
                        "SELECT set_config("
                        "'agenticorg.company_id', :company_id, true)"
                    ),
                    {
                        "company_id": (
                            str(context_company_id) if context_company_id else ""
                        )
                    },
                )

                def insert() -> None:
                    insert_config(
                        scoped_conn,
                        tenant_id=tenant_a,
                        company_id=row_company_id,
                        connector_name=connector_name,
                        display_name=connector_name,
                    )

                if rejected:
                    with pytest.raises(DBAPIError, match="row-level security"):
                        insert()
                else:
                    insert()
            finally:
                transaction.rollback()
                scoped_conn.close()

        scoped_write(
            company_a,
            None,
            connector_name="company-cannot-write-global",
            rejected=True,
        )
        scoped_write(
            None,
            company_a,
            connector_name="global-cannot-write-company",
            rejected=True,
        )
        scoped_write(
            company_a,
            company_a,
            connector_name="company-exact-write",
            rejected=False,
        )
        scoped_write(
            None,
            None,
            connector_name="global-exact-write",
            rejected=False,
        )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP OWNED BY connector_config_rls_probe"))
            conn.execute(text("DROP ROLE connector_config_rls_probe"))
        engine.dispose()
