# SPDX-License-Identifier: Apache-2.0
"""The NOBYPASSRLS application role gets least-privilege grants.

``scripts/create_app_role.sql`` used to grant EXECUTE on every function and
row DML on every table to ``agenticorg_app``. That handed the runtime the
restricted functions migrations close with ``REVOKE ... FROM PUBLIC`` and
UPDATE/DELETE on tables whose triggers make them append-only. These checks
parse the script and the migrations so a new append-only trigger, a new
SECURITY DEFINER function or a widened grant fails CI until the script
classifies it. ``tests/integration/test_rls_nobypass_role.py`` proves the
same rules against a live database connected as the role.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "create_app_role.sql"
MIGRATIONS = ROOT / "migrations" / "versions"
ROLE = "agenticorg_app"

# Trigger functions that refuse a row change outright (or allow only a named
# transition). A trigger using one of them makes its table's refused
# operations a privilege the application role must not hold.
IMMUTABILITY_FUNCTIONS = {
    "audit_log_reject_mutation",
    "prevent_capability_ledger_mutation",
    "eval_dataset_versions_immutable",
}
# A function name that reads like one of the above must be classified here.
IMMUTABILITY_NAME = re.compile(r"immutable|append_only|reject_mutation|prevent_\w*mutation", re.I)
# Restricted (SECURITY DEFINER, non-trigger) functions: the ones the
# application calls, and the ones it must not (none yet). Every such function
# a migration defines must be in one of the two.
APP_CALLABLE_FUNCTIONS = {"gemini_daily_platform_spend"}
NOT_APP_CALLABLE_FUNCTIONS: set[str] = set()


def _statements() -> list[str]:
    sql = SCRIPT.read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"\$\$.*?\$\$", "$$DO$$", sql, flags=re.S)
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("\\"))
    return [" ".join(part.split()) for part in sql.split(";") if part.strip()]


def _do_blocks() -> str:
    return "\n".join(re.findall(r"\$\$(.*?)\$\$", SCRIPT.read_text(encoding="utf-8"), flags=re.S))


def _migrations_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.py")))


def _withheld(table: str) -> set[str]:
    """Operations the script revokes on ``table`` after the blanket grant."""
    out: set[str] = set()
    for statement in _statements():
        match = re.fullmatch(rf"REVOKE (.+?) ON {table} FROM {ROLE}", statement, flags=re.I)
        if match:
            privs = match.group(1).upper()
            out |= {"SELECT", "INSERT", "UPDATE", "DELETE"} if privs == "ALL" else {p.strip() for p in privs.split(",")}
    return out


def test_script_is_one_transaction_that_resets_before_granting():
    statements = _statements()
    assert statements[0] == "BEGIN"
    assert "COMMIT" in statements
    upper = [s.upper() for s in statements]
    for reset in (
        f"REVOKE ALL ON ALL TABLES IN SCHEMA PUBLIC FROM {ROLE.upper()}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA PUBLIC FROM {ROLE.upper()}",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA PUBLIC FROM {ROLE.upper()}",
    ):
        assert reset in upper, reset
    first_grant = next(i for i, s in enumerate(upper) if s.startswith("GRANT SELECT"))
    assert upper.index(f"REVOKE ALL ON ALL TABLES IN SCHEMA PUBLIC FROM {ROLE.upper()}") < first_grant


def test_no_grant_is_broader_than_row_dml():
    for statement in _statements():
        upper = statement.upper()
        if not upper.startswith("GRANT") and " GRANT " not in upper:
            continue
        assert "ALL PRIVILEGES" not in upper and not re.search(r"\bGRANT ALL\b", upper), statement
        for forbidden in ("TRUNCATE", "REFERENCES", "TRIGGER", "CREATE", "BYPASSRLS"):
            assert forbidden not in upper, statement
        assert "EXECUTE ON ALL FUNCTIONS" not in upper, statement
        if "ON TABLES" in upper or "ON ALL TABLES" in upper:
            privs = re.search(r"GRANT (.+?) ON (ALL )?TABLES", upper).group(1)
            assert {p.strip() for p in privs.split(",")} == {"SELECT", "INSERT", "UPDATE", "DELETE"}, statement
    assert any("REVOKE CREATE ON SCHEMA PUBLIC" in s.upper() for s in _statements())


def test_default_privileges_grant_no_function_execute():
    defaults = [s.upper() for s in _statements() if s.upper().startswith("ALTER DEFAULT PRIVILEGES")]
    assert any("REVOKE EXECUTE ON FUNCTIONS" in s for s in defaults)
    assert not any("GRANT" in s and "FUNCTIONS" in s for s in defaults)
    assert any("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES" in s for s in defaults)


def test_audit_log_update_is_scoped_to_the_seal_columns():
    from core.governance.audit_chain import SEAL_TRIGGER_SQL

    seal_columns = set(re.findall(r"'(\w+)'", re.search(r"ARRAY\[(.*?)\]", SEAL_TRIGGER_SQL).group(1)))
    assert seal_columns == {"chain_seq", "chain_prev", "chain_hash", "sealed_at"}
    assert {"UPDATE", "DELETE"} <= _withheld("audit_log")
    column_grants = [
        s for s in _statements() if re.fullmatch(rf"GRANT UPDATE \((.+)\) ON audit_log TO {ROLE}", s, flags=re.I)
    ]
    assert len(column_grants) == 1
    granted = {c.strip() for c in re.search(r"\((.+)\)", column_grants[0]).group(1).split(",")}
    assert granted == seal_columns


def test_every_append_only_trigger_is_mirrored_in_the_grants():
    # Join implicitly concatenated string literals so a statement split over
    # two source lines reads as one.
    text = re.sub(r"\"\s*\n\s*f?\"", "", _migrations_text())
    trigger = re.compile(
        r"CREATE TRIGGER\s+\S+\s+BEFORE\s+(.+?)\s+ON\s+(?:public\.)?(\{\w+\}|\w+)\s+FOR EACH ROW\s+"
        r"EXECUTE (?:FUNCTION|PROCEDURE)\s+(?:public\.)?(\w+)\(",
        flags=re.I | re.S,
    )
    found: dict[str, set[str]] = {}
    for match in trigger.finditer(text):
        ops, table, function = match.groups()
        if function not in IMMUTABILITY_FUNCTIONS:
            continue
        tables = [table]
        if table.startswith("{"):
            # An f-string over a loop: ``for table in ("a", "b"):`` just above.
            var = table.strip("{}")
            loops = re.findall(rf"for {var} in \(([^)]*)\)", text[max(0, match.start() - 1500) : match.start()])
            assert loops, f"cannot resolve {table} for trigger function {function}"
            tables = re.findall(r"[\"'](\w+)[\"']", loops[-1])
        refused = {op for op in ("UPDATE", "DELETE") if op in ops.upper()}
        for name in tables:
            found.setdefault(name, set()).update(refused)
    # The ledger, evaluation-version and audit triggers are all seen.
    assert {
        "audit_log",
        "capability_evidence_records",
        "capability_promotion_events",
        "capability_readiness_records",
        "eval_dataset_versions",
    } <= set(found)
    for table, refused in found.items():
        assert refused <= _withheld(table), f"{table}: trigger refuses {refused}, script withholds {_withheld(table)}"


def test_new_immutability_functions_are_classified():
    defined = set(re.findall(r"CREATE (?:OR REPLACE )?FUNCTION\s+(?:public\.)?(\w+)\s*\(", _migrations_text(), re.I))
    unclassified = {name for name in defined if IMMUTABILITY_NAME.search(name)} - IMMUTABILITY_FUNCTIONS
    assert not unclassified, (
        f"new append-only trigger functions {sorted(unclassified)}: add their tables to "
        "scripts/create_app_role.sql (REVOKE UPDATE, DELETE) and the function to IMMUTABILITY_FUNCTIONS"
    )


def test_only_the_platform_spend_function_is_granted_execute():
    text = _migrations_text()
    definer = set()
    header_re = r"CREATE (?:OR REPLACE )?FUNCTION\s+(?:public\.)?(\w+)\s*\([^$]*?\bAS\b"
    for match in re.finditer(header_re, text, re.I):
        header = " ".join(match.group(0).upper().split())
        if "SECURITY DEFINER" in header and "RETURNS TRIGGER" not in header:
            definer.add(match.group(1))
    assert definer == APP_CALLABLE_FUNCTIONS | NOT_APP_CALLABLE_FUNCTIONS, sorted(definer)
    granted = set(
        re.findall(
            r"GRANT EXECUTE ON FUNCTION\s+(?:public\.)?(\w+)\s*\(", _do_blocks() + "\n".join(_statements()), re.I
        )
    )
    assert granted == APP_CALLABLE_FUNCTIONS
    router = (ROOT / "core" / "llm" / "router.py").read_text(encoding="utf-8")
    for name in APP_CALLABLE_FUNCTIONS:
        assert f"public.{name}()" in router


def test_migration_owned_tables_are_not_writable():
    assert {"INSERT", "UPDATE", "DELETE"} <= _withheld("alembic_version")
    assert {"INSERT", "UPDATE", "DELETE"} <= _withheld("checkpoint_migrations")
    assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= _withheld("alembic_migration_progress")
    assert "DELETE" in _withheld("audit_chain_anchors")


def test_every_table_the_script_names_exists():
    text = _migrations_text()
    named = set()
    for statement in _statements():
        match = re.search(r"\bON (\w+) (?:FROM|TO) ", statement)
        if match and match.group(1).upper() not in {"SCHEMA", "DATABASE", "TABLES", "SEQUENCES", "FUNCTIONS"}:
            named.add(match.group(1))
    assert named
    for table in named:
        assert re.search(rf"\b{table}\b", text), f"{table} is not created by any migration"


def test_fail_closed_checks_cover_each_class():
    blocks = _do_blocks()
    for check in ("rolbypassrls", "'TRUNCATE'", "'CREATE'", "prosecdef", "gemini_daily_platform_spend"):
        assert check in blocks, check
    assert "Installer retained aggregate row access" in blocks
