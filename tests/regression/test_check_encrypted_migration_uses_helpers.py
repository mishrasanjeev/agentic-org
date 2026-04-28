"""Foundation #5 step 3 — regression tests for the CI lint guard.

Pinned behaviors:

- A new migration that touches ``*_encrypted`` and DOESN'T import
  the helpers fails the gate.
- A new migration that imports the helpers passes.
- A new migration that doesn't touch encrypted columns is ignored.
- The ``ENCRYPTED_MIGRATION_HELPER_EXEMPT`` marker bypasses the
  gate (used for pure DROP migrations etc.).

The script normally runs in a git context and diffs against
origin/main. These tests exercise the unit-level matchers so we
can drive the gate without setting up a fake repo.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_encrypted_migration_uses_helpers.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_encrypted_migration_uses_helpers", SCRIPT
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_encrypted_column_pattern_matches_realistic_names() -> None:
    mod = _load_module()
    pat = mod.ENCRYPTED_COLUMN
    for name in (
        "credentials_encrypted",
        "password_encrypted",
        "auth_token_encrypted",
        "tenant_secret_encrypted",
    ):
        assert pat.search(f"some.{name}"), f"should match {name}"


def test_helper_import_pattern_matches_both_styles() -> None:
    mod = _load_module()
    pat = mod.HELPER_IMPORT
    assert pat.search(
        "from core.crypto.migration_helpers import encrypted_migration"
    )
    assert pat.search("import core.crypto.migration_helpers")
    assert not pat.search("from core.crypto.rewrap import foo")


def test_exempt_marker_constant_is_documented_value() -> None:
    """The marker string is part of the public contract — changing
    it would silently break exemptions in existing migrations."""
    mod = _load_module()
    assert mod.EXEMPT_MARKER == "ENCRYPTED_MIGRATION_HELPER_EXEMPT"


def test_main_passes_when_no_new_migrations(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "_added_files", lambda *_: [])
    assert mod.main() == 0


def test_main_fails_when_encrypted_migration_skips_helpers(
    monkeypatch, tmp_path, capsys
) -> None:
    mod = _load_module()
    bad_migration = tmp_path / "v999_test.py"
    bad_migration.write_text(
        '"""Direct UPDATE of an encrypted column."""\n'
        'from alembic import op\n'
        'def upgrade():\n'
        '    op.execute("UPDATE foo SET credentials_encrypted = NULL")\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path.parent)
    rel = bad_migration.relative_to(tmp_path.parent).as_posix()
    monkeypatch.setattr(
        mod, "_added_files", lambda *_: [rel]
    )
    # The script reads via ``Path(rel)`` relative to cwd.
    monkeypatch.setattr(
        mod, "MIGRATION_DIR", tmp_path.name + "/"
    )

    rc = mod.main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "skipping the hardening helpers" in err
    assert "credentials_encrypted" in err


def test_main_passes_when_helpers_imported(
    monkeypatch, tmp_path
) -> None:
    mod = _load_module()
    good_migration = tmp_path / "v999_test.py"
    good_migration.write_text(
        '"""Wrapped rewrap."""\n'
        'from core.crypto.migration_helpers import encrypted_migration\n'
        'def upgrade():\n'
        '    with encrypted_migration(\n'
        '        revision="v999",\n'
        '        table="foo",\n'
        '        columns=["credentials_encrypted"],\n'
        '        rollback_doc="restore from backup",\n'
        '    ):\n'
        '        pass\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path.parent)
    rel = good_migration.relative_to(tmp_path.parent).as_posix()
    monkeypatch.setattr(mod, "_added_files", lambda *_: [rel])
    monkeypatch.setattr(mod, "MIGRATION_DIR", tmp_path.name + "/")

    assert mod.main() == 0


def test_exempt_marker_bypasses_the_gate(monkeypatch, tmp_path) -> None:
    mod = _load_module()
    drop_migration = tmp_path / "v999_drop.py"
    drop_migration.write_text(
        '"""Drop an encrypted column — no rewrap needed.\n'
        'ENCRYPTED_MIGRATION_HELPER_EXEMPT: pure column DROP, no '
        'reads or writes of ciphertext.\n'
        '"""\n'
        'from alembic import op\n'
        'def upgrade():\n'
        '    op.drop_column("foo", "credentials_encrypted")\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path.parent)
    rel = drop_migration.relative_to(tmp_path.parent).as_posix()
    monkeypatch.setattr(mod, "_added_files", lambda *_: [rel])
    monkeypatch.setattr(mod, "MIGRATION_DIR", tmp_path.name + "/")

    assert mod.main() == 0
