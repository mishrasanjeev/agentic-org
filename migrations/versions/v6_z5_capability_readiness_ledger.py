# ruff: noqa: S608
"""Add the tenant/company-scoped capability readiness/evidence ledger.

Revision ID: v6z5_readiness_ledger
Revises: v6z4_fk_index_coverage
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v6z5_readiness_ledger"
down_revision = "v6z4_fk_index_coverage"
branch_labels = None
depends_on = None

_SCOPE = "'Mandatory','Conditional','OutOfScope'"
_INTERNAL = "'Missing','Scaffolded','Implemented','Integrated','SandboxProven','ProductionProven','GA'"
_GATE = "'Blocked','InReview','Passed','Expired','NotAssessed'"
_PUBLIC = "'Unavailable','Preview','Beta','LimitedAvailability','GA','Deprecated'"
_CLAIM = "'Hidden','Illustrative','Qualified','EvidenceBacked'"
_ENV = "'local','test','integration','vendor_sandbox','staging','controlled_pilot','production'"


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _schema_object_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        str(item["name"])
        for item in (*inspector.get_indexes(table_name), *inspector.get_unique_constraints(table_name))
        if item.get("name")
    }


def _ensure_index(
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
    where: str | None = None,
) -> None:
    if index_name in _schema_object_names(table_name):
        return
    kwargs = {"postgresql_where": sa.text(where)} if where else {}
    op.create_index(index_name, table_name, columns, unique=unique, **kwargs)


def _ensure_check(table_name: str, constraint_name: str, condition: str, *, replace: bool = False) -> None:
    names = {
        str(item["name"]) for item in sa.inspect(op.get_bind()).get_check_constraints(table_name) if item.get("name")
    }
    if constraint_name in names:
        if not replace:
            return
        op.drop_constraint(constraint_name, table_name, type_="check")
    op.create_check_constraint(constraint_name, table_name, condition)


def upgrade() -> None:
    records_preexisting = _table_exists("capability_readiness_records")
    if not records_preexisting:
        op.create_table(
            "capability_readiness_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("capability_id", sa.String(160), nullable=False),
            sa.Column("domain", sa.String(100), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("scope_disposition", sa.String(32), nullable=False),
            sa.Column("scope_condition", sa.Text(), nullable=True),
            sa.Column("scope_details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("required_gate_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("internal_maturity_state", sa.String(32), nullable=False, server_default="Missing"),
            sa.Column("release_gate_state", sa.String(32), nullable=False, server_default="Blocked"),
            sa.Column("public_availability_state", sa.String(32), nullable=False, server_default="Unavailable"),
            sa.Column("claim_state", sa.String(32), nullable=False, server_default="Hidden"),
            sa.Column("gate_results", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("permitted_claim_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("owners", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("approver_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("traceability", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("limitations", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("feature_flag", sa.String(255), nullable=True),
            sa.Column("review_expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("current_promotion_sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.String(255), nullable=False),
            sa.Column("updated_by", sa.String(255), nullable=False),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
            sa.CheckConstraint(f"scope_disposition IN ({_SCOPE})", name="ck_capability_scope_disposition"),
            sa.CheckConstraint(f"internal_maturity_state IN ({_INTERNAL})", name="ck_capability_internal_maturity"),
            sa.CheckConstraint(f"release_gate_state IN ({_GATE})", name="ck_capability_release_gate"),
            sa.CheckConstraint(f"public_availability_state IN ({_PUBLIC})", name="ck_capability_public_availability"),
            sa.CheckConstraint(f"claim_state IN ({_CLAIM})", name="ck_capability_claim_state"),
            sa.CheckConstraint("jsonb_typeof(owners) = 'object'", name="ck_capability_owners_object"),
            sa.CheckConstraint("jsonb_typeof(traceability) = 'object'", name="ck_capability_traceability_object"),
            sa.CheckConstraint("review_expires_at > created_at", name="ck_capability_review_expiry"),
            sa.CheckConstraint(
                "current_promotion_sequence >= 0",
                name="ck_capability_current_sequence_nonnegative",
            ),
        )
    if records_preexisting:
        for column_name, default_sql in (
            ("scope_details", "'{}'::jsonb"),
            ("required_gate_ids", "'[]'::jsonb"),
            ("internal_maturity_state", "'Missing'"),
            ("release_gate_state", "'Blocked'"),
            ("public_availability_state", "'Unavailable'"),
            ("claim_state", "'Hidden'"),
            ("gate_results", "'{}'::jsonb"),
            ("permitted_claim_ids", "'[]'::jsonb"),
            ("owners", "'{}'::jsonb"),
            ("approver_ids", "'[]'::jsonb"),
            ("traceability", "'{}'::jsonb"),
            ("limitations", "'[]'::jsonb"),
            ("current_promotion_sequence", "0"),
        ):
            op.alter_column(
                "capability_readiness_records",
                column_name,
                server_default=sa.text(default_sql),
            )
        _ensure_check(
            "capability_readiness_records", "ck_capability_scope_disposition", f"scope_disposition IN ({_SCOPE})"
        )
        _ensure_check(
            "capability_readiness_records",
            "ck_capability_internal_maturity",
            f"internal_maturity_state IN ({_INTERNAL})",
        )
        _ensure_check("capability_readiness_records", "ck_capability_release_gate", f"release_gate_state IN ({_GATE})")
        _ensure_check(
            "capability_readiness_records",
            "ck_capability_public_availability",
            f"public_availability_state IN ({_PUBLIC})",
        )
        _ensure_check("capability_readiness_records", "ck_capability_claim_state", f"claim_state IN ({_CLAIM})")
        _ensure_check("capability_readiness_records", "ck_capability_owners_object", "jsonb_typeof(owners) = 'object'")
        _ensure_check(
            "capability_readiness_records", "ck_capability_traceability_object", "jsonb_typeof(traceability) = 'object'"
        )
        _ensure_check("capability_readiness_records", "ck_capability_review_expiry", "review_expires_at > created_at")
        _ensure_check(
            "capability_readiness_records",
            "ck_capability_current_sequence_nonnegative",
            "current_promotion_sequence >= 0",
        )
    _ensure_index("capability_readiness_records", "ix_capability_readiness_tenant_company", ["tenant_id", "company_id"])
    _ensure_index("capability_readiness_records", "ix_capability_readiness_company_id", ["company_id"])
    _ensure_index("capability_readiness_records", "ix_capability_readiness_capability_id", ["capability_id"])
    _ensure_index(
        "capability_readiness_records",
        "uq_capability_readiness_tenant_global",
        ["tenant_id", "capability_id"],
        unique=True,
        where="company_id IS NULL",
    )
    _ensure_index(
        "capability_readiness_records",
        "uq_capability_readiness_company",
        ["tenant_id", "company_id", "capability_id"],
        unique=True,
        where="company_id IS NOT NULL",
    )

    evidence_preexisting = _table_exists("capability_evidence_records")
    if not evidence_preexisting:
        op.create_table(
            "capability_evidence_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "readiness_record_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("capability_readiness_records.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("capability_id", sa.String(160), nullable=False),
            sa.Column("evidence_version", sa.String(100), nullable=False),
            sa.Column("evidence_type", sa.String(100), nullable=False),
            sa.Column("artifact_uri", sa.Text(), nullable=False),
            sa.Column("sha256_checksum", sa.String(64), nullable=False),
            sa.Column("environment", sa.String(32), nullable=False),
            sa.Column("provider_account_class", sa.String(100), nullable=False),
            sa.Column("product_version", sa.String(100), nullable=False),
            sa.Column("source_commit_sha", sa.String(64), nullable=False),
            sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("reviewed_by", sa.String(255), nullable=False),
            sa.Column("supports_gate_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("supports_claim_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("evidence_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_by", sa.String(255), nullable=False),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(f"environment IN ({_ENV})", name="ck_capability_evidence_environment"),
            sa.CheckConstraint("sha256_checksum ~ '^[0-9a-f]{64}$'", name="ck_capability_evidence_checksum"),
            sa.CheckConstraint("source_commit_sha ~ '^[0-9a-fA-F]{7,64}$'", name="ck_capability_evidence_commit"),
            sa.CheckConstraint(
                "observed_at <= reviewed_at AND reviewed_at < expires_at", name="ck_capability_evidence_times"
            ),
            sa.UniqueConstraint("readiness_record_id", "evidence_version", name="uq_capability_evidence_version"),
        )
    if evidence_preexisting:
        for column_name, default_sql in (
            ("supports_gate_ids", "'[]'::jsonb"),
            ("supports_claim_ids", "'[]'::jsonb"),
            ("evidence_metadata", "'{}'::jsonb"),
        ):
            op.alter_column(
                "capability_evidence_records",
                column_name,
                server_default=sa.text(default_sql),
            )
        _ensure_check("capability_evidence_records", "ck_capability_evidence_environment", f"environment IN ({_ENV})")
        _ensure_check(
            "capability_evidence_records",
            "ck_capability_evidence_checksum",
            "sha256_checksum ~ '^[0-9a-f]{64}$'",
            replace=True,
        )
        _ensure_check(
            "capability_evidence_records",
            "ck_capability_evidence_commit",
            "source_commit_sha ~ '^[0-9a-fA-F]{7,64}$'",
        )
        _ensure_check(
            "capability_evidence_records",
            "ck_capability_evidence_times",
            "observed_at <= reviewed_at AND reviewed_at < expires_at",
        )
    _ensure_index(
        "capability_evidence_records", "ix_capability_evidence_scope", ["tenant_id", "company_id", "capability_id"]
    )
    _ensure_index("capability_evidence_records", "ix_capability_evidence_company_id", ["company_id"])
    _ensure_index("capability_evidence_records", "ix_capability_evidence_expiry", ["expires_at"])
    _ensure_index(
        "capability_evidence_records",
        "uq_capability_evidence_version",
        ["readiness_record_id", "evidence_version"],
        unique=True,
    )

    promotion_preexisting = _table_exists("capability_promotion_events")
    if not promotion_preexisting:
        op.create_table(
            "capability_promotion_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "readiness_record_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("capability_readiness_records.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("capability_id", sa.String(160), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("from_internal_maturity", sa.String(32), nullable=True),
            sa.Column("to_internal_maturity", sa.String(32), nullable=False),
            sa.Column("from_release_gate", sa.String(32), nullable=True),
            sa.Column("to_release_gate", sa.String(32), nullable=False),
            sa.Column("from_public_availability", sa.String(32), nullable=True),
            sa.Column("to_public_availability", sa.String(32), nullable=False),
            sa.Column("from_claim_state", sa.String(32), nullable=True),
            sa.Column("to_claim_state", sa.String(32), nullable=False),
            sa.Column("scope_disposition_snapshot", sa.String(32), nullable=False),
            sa.Column("gate_results_snapshot", postgresql.JSONB(), nullable=False),
            sa.Column("evidence_snapshot", postgresql.JSONB(), nullable=False),
            sa.Column("ownership_snapshot", postgresql.JSONB(), nullable=False),
            sa.Column("traceability_snapshot", postgresql.JSONB(), nullable=False),
            sa.Column("permitted_claim_ids", postgresql.JSONB(), nullable=False),
            sa.Column("limitations", postgresql.JSONB(), nullable=False),
            sa.Column("requested_by", sa.String(255), nullable=False),
            sa.Column("approved_by", sa.String(255), nullable=False),
            sa.Column("approval_reference", sa.String(500), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("policy_version", sa.String(64), nullable=False),
            sa.Column("previous_event_hash", sa.String(64), nullable=True),
            sa.Column("event_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(
                "event_type IN ('registered','promoted','demoted','attested')",
                name="ck_capability_promotion_event_type",
            ),
            sa.UniqueConstraint("readiness_record_id", "sequence", name="uq_capability_promotion_sequence"),
            sa.CheckConstraint("sequence >= 0", name="ck_capability_promotion_sequence_nonnegative"),
            sa.CheckConstraint("event_hash ~ '^[0-9a-f]{64}$'", name="ck_capability_promotion_event_hash"),
            sa.CheckConstraint(
                "previous_event_hash IS NULL OR previous_event_hash ~ '^[0-9a-f]{64}$'",
                name="ck_capability_promotion_previous_hash",
            ),
        )
    if promotion_preexisting:
        _ensure_check(
            "capability_promotion_events",
            "ck_capability_promotion_event_type",
            "event_type IN ('registered','promoted','demoted','attested')",
        )
        _ensure_check("capability_promotion_events", "ck_capability_promotion_sequence_nonnegative", "sequence >= 0")
        _ensure_check(
            "capability_promotion_events",
            "ck_capability_promotion_event_hash",
            "event_hash ~ '^[0-9a-f]{64}$'",
            replace=True,
        )
        _ensure_check(
            "capability_promotion_events",
            "ck_capability_promotion_previous_hash",
            "previous_event_hash IS NULL OR previous_event_hash ~ '^[0-9a-f]{64}$'",
            replace=True,
        )
    _ensure_index(
        "capability_promotion_events", "ix_capability_promotion_scope", ["tenant_id", "company_id", "capability_id"]
    )
    _ensure_index("capability_promotion_events", "ix_capability_promotion_company_id", ["company_id"])
    _ensure_index(
        "capability_promotion_events",
        "uq_capability_promotion_sequence",
        ["readiness_record_id", "sequence"],
        unique=True,
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION guard_capability_ledger_scope() RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
          IF TG_TABLE_NAME = 'capability_readiness_records' THEN
            IF TG_OP = 'UPDATE' THEN
              IF ROW(NEW.id, NEW.tenant_id, NEW.company_id, NEW.capability_id)
                IS DISTINCT FROM ROW(OLD.id, OLD.tenant_id, OLD.company_id, OLD.capability_id)
              THEN RAISE EXCEPTION 'capability readiness identity is immutable'; END IF;
            END IF;
            IF NEW.company_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM public.companies c WHERE c.id = NEW.company_id AND c.tenant_id = NEW.tenant_id
            ) THEN RAISE EXCEPTION 'company is not in readiness tenant scope'; END IF;
          ELSIF NOT EXISTS (
            SELECT 1 FROM public.capability_readiness_records r
            WHERE r.id = NEW.readiness_record_id
              AND r.tenant_id = NEW.tenant_id
              AND r.company_id IS NOT DISTINCT FROM NEW.company_id
              AND r.capability_id = NEW.capability_id
          ) THEN RAISE EXCEPTION 'ledger child row does not match readiness scope';
          END IF;
          RETURN NEW;
        END; $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION guard_capability_ledger_scope() FROM PUBLIC")
    op.execute("DROP TRIGGER IF EXISTS capability_readiness_scope ON capability_readiness_records")
    op.execute(
        "CREATE TRIGGER capability_readiness_scope BEFORE INSERT OR UPDATE ON capability_readiness_records FOR EACH ROW EXECUTE FUNCTION guard_capability_ledger_scope()"
    )
    op.execute("DROP TRIGGER IF EXISTS capability_evidence_scope ON capability_evidence_records")
    op.execute(
        "CREATE TRIGGER capability_evidence_scope BEFORE INSERT OR UPDATE ON capability_evidence_records FOR EACH ROW EXECUTE FUNCTION guard_capability_ledger_scope()"
    )
    op.execute("DROP TRIGGER IF EXISTS capability_promotion_scope ON capability_promotion_events")

    op.execute("""
        CREATE OR REPLACE FUNCTION guard_capability_promotion_chain() RETURNS trigger AS $$
        DECLARE
          parent_sequence integer;
          parent_internal text;
          parent_gate text;
          parent_public text;
          parent_claim text;
          prior_hash text;
          prior_internal text;
          prior_gate text;
          prior_public text;
          prior_claim text;
        BEGIN
          IF NEW.event_hash !~ '^[0-9a-f]{64}$'
             OR (NEW.previous_event_hash IS NOT NULL AND NEW.previous_event_hash !~ '^[0-9a-f]{64}$')
          THEN RAISE EXCEPTION 'promotion event hashes must be lowercase sha256 values'; END IF;

          SELECT r.current_promotion_sequence, r.internal_maturity_state, r.release_gate_state,
                 r.public_availability_state, r.claim_state
          INTO parent_sequence, parent_internal, parent_gate, parent_public, parent_claim
          FROM capability_readiness_records r
          WHERE r.id = NEW.readiness_record_id
            AND r.tenant_id = NEW.tenant_id
            AND r.company_id IS NOT DISTINCT FROM NEW.company_id
            AND r.capability_id = NEW.capability_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'promotion event does not match readiness scope'; END IF;

          IF NEW.sequence = 0 THEN
            IF parent_sequence <> 0 OR NEW.event_type <> 'registered'
               OR NEW.previous_event_hash IS NOT NULL
               OR NEW.from_internal_maturity IS NOT NULL OR NEW.from_release_gate IS NOT NULL
               OR NEW.from_public_availability IS NOT NULL OR NEW.from_claim_state IS NOT NULL
               OR NEW.to_internal_maturity IS DISTINCT FROM parent_internal
               OR NEW.to_release_gate IS DISTINCT FROM parent_gate
               OR NEW.to_public_availability IS DISTINCT FROM parent_public
               OR NEW.to_claim_state IS DISTINCT FROM parent_claim
               OR EXISTS (
                 SELECT 1 FROM capability_promotion_events e
                 WHERE e.readiness_record_id = NEW.readiness_record_id
               )
            THEN RAISE EXCEPTION 'invalid initial promotion event'; END IF;
          ELSE
            IF NEW.sequence <> parent_sequence + 1
            THEN RAISE EXCEPTION 'promotion event sequence is not the next readiness sequence'; END IF;
            SELECT e.event_hash, e.to_internal_maturity, e.to_release_gate,
                   e.to_public_availability, e.to_claim_state
            INTO prior_hash, prior_internal, prior_gate, prior_public, prior_claim
            FROM capability_promotion_events e
            WHERE e.readiness_record_id = NEW.readiness_record_id
              AND e.sequence = NEW.sequence - 1;
            IF NOT FOUND OR NEW.previous_event_hash IS DISTINCT FROM prior_hash
               OR NEW.from_internal_maturity IS DISTINCT FROM prior_internal
               OR NEW.from_release_gate IS DISTINCT FROM prior_gate
               OR NEW.from_public_availability IS DISTINCT FROM prior_public
               OR NEW.from_claim_state IS DISTINCT FROM prior_claim
            THEN RAISE EXCEPTION 'promotion event predecessor does not match immutable history'; END IF;
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS capability_promotion_chain ON capability_promotion_events")
    op.execute(
        "CREATE TRIGGER capability_promotion_chain BEFORE INSERT ON capability_promotion_events FOR EACH ROW EXECUTE FUNCTION guard_capability_promotion_chain()"
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_capability_ledger_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'capability readiness evidence and history are append-only';
        END; $$ LANGUAGE plpgsql;
    """)
    for table in ("capability_evidence_records", "capability_promotion_events"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_capability_ledger_mutation()"
        )
    op.execute("DROP TRIGGER IF EXISTS capability_readiness_records_no_delete ON capability_readiness_records")
    op.execute(
        "CREATE TRIGGER capability_readiness_records_no_delete BEFORE DELETE ON capability_readiness_records FOR EACH ROW EXECUTE FUNCTION prevent_capability_ledger_mutation()"
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION guard_capability_readiness_transition() RETURNS trigger AS $$
        BEGIN
            IF ROW(NEW.domain, NEW.title, NEW.description, NEW.scope_disposition, NEW.scope_condition,
                   NEW.scope_details, NEW.required_gate_ids, NEW.owners, NEW.approver_ids,
                   NEW.traceability, NEW.feature_flag, NEW.review_expires_at, NEW.created_by, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.domain, OLD.title, OLD.description, OLD.scope_disposition, OLD.scope_condition,
                   OLD.scope_details, OLD.required_gate_ids, OLD.owners, OLD.approver_ids,
                   OLD.traceability, OLD.feature_flag, OLD.review_expires_at, OLD.created_by, OLD.created_at)
            THEN RAISE EXCEPTION 'registered capability metadata is immutable'; END IF;
            IF ROW(NEW.internal_maturity_state, NEW.release_gate_state, NEW.public_availability_state,
                   NEW.claim_state, NEW.gate_results, NEW.permitted_claim_ids, NEW.limitations,
                   NEW.current_promotion_sequence)
               IS DISTINCT FROM
               ROW(OLD.internal_maturity_state, OLD.release_gate_state, OLD.public_availability_state,
                   OLD.claim_state, OLD.gate_results, OLD.permitted_claim_ids, OLD.limitations,
                   OLD.current_promotion_sequence)
            THEN
                IF NEW.current_promotion_sequence <> OLD.current_promotion_sequence + 1 OR NOT EXISTS (
                    SELECT 1 FROM capability_promotion_events e
                    WHERE e.readiness_record_id = NEW.id
                      AND e.sequence = NEW.current_promotion_sequence
                      AND e.to_internal_maturity = NEW.internal_maturity_state
                      AND e.to_release_gate = NEW.release_gate_state
                      AND e.to_public_availability = NEW.public_availability_state
                      AND e.to_claim_state = NEW.claim_state
                      AND e.gate_results_snapshot = NEW.gate_results
                      AND e.permitted_claim_ids = NEW.permitted_claim_ids
                      AND e.limitations = NEW.limitations
                      AND e.requested_by = NEW.updated_by
                ) THEN
                    RAISE EXCEPTION 'readiness changes require a matching immutable promotion event';
                END IF;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS capability_readiness_transition_guard ON capability_readiness_records")
    op.execute(
        "CREATE TRIGGER capability_readiness_transition_guard BEFORE UPDATE ON capability_readiness_records FOR EACH ROW EXECUTE FUNCTION guard_capability_readiness_transition()"
    )

    for table in ("capability_readiness_records", "capability_evidence_records", "capability_promotion_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_scope_isolation ON {table}")
        op.execute(f"""
            CREATE POLICY {table}_scope_isolation ON {table}
            USING (
                tenant_id::text = current_setting('agenticorg.tenant_id', true)
                AND (
                    company_id IS NULL
                    OR (
                        COALESCE(current_setting('agenticorg.company_id', true), '') <> ''
                        AND company_id::text = current_setting('agenticorg.company_id', true)
                    )
                )
            )
            WITH CHECK (
                tenant_id::text = current_setting('agenticorg.tenant_id', true)
                AND (
                    company_id IS NULL
                    OR (
                        COALESCE(current_setting('agenticorg.company_id', true), '') <> ''
                        AND company_id::text = current_setting('agenticorg.company_id', true)
                    )
                )
            )
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS capability_readiness_transition_guard ON capability_readiness_records")
    op.execute("DROP TRIGGER IF EXISTS capability_readiness_records_no_delete ON capability_readiness_records")
    op.execute("DROP TRIGGER IF EXISTS capability_readiness_scope ON capability_readiness_records")
    op.execute("DROP TRIGGER IF EXISTS capability_evidence_records_immutable ON capability_evidence_records")
    op.execute("DROP TRIGGER IF EXISTS capability_evidence_scope ON capability_evidence_records")
    op.execute("DROP TRIGGER IF EXISTS capability_promotion_events_immutable ON capability_promotion_events")
    op.execute("DROP TRIGGER IF EXISTS capability_promotion_chain ON capability_promotion_events")
    op.execute("DROP FUNCTION IF EXISTS guard_capability_readiness_transition()")
    op.execute("DROP FUNCTION IF EXISTS prevent_capability_ledger_mutation()")
    op.execute("DROP FUNCTION IF EXISTS guard_capability_promotion_chain()")
    op.execute("DROP FUNCTION IF EXISTS guard_capability_ledger_scope()")
    op.drop_table("capability_promotion_events")
    op.drop_table("capability_evidence_records")
    op.drop_table("capability_readiness_records")
