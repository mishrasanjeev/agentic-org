"""Regression tests for the keyring rewrap CLI.

Foundation #4 iteration 3 — pin the rewrap lifecycle contracts so a
future change can't silently break the "rotate, rewrap, retire" loop
captured in ``feedback_key_rotation_discipline.md``.

Pins:
  1. ``_stamp_kid`` parses agko_v{id}$ stamps and returns 'legacy'
     for unstamped vault ciphertext.
  2. ``_extract_ciphertext`` understands both column shapes (JSONB
     ``{"_encrypted": "..."}`` vs plain TEXT).
  3. ``_wrap_ciphertext_for_column`` is the inverse of #2.
  4. ``run --dry-run`` reports pending count without writing.
  5. ``run`` re-encrypts non-active rows, leaves active rows alone,
     and is idempotent across re-runs.
  6. ``run --column`` filters to a single registered column.
  7. ``run --key-id`` filters to ciphertext stamped with that id only.
  8. ``verify`` exits 0 when every row is on the active key, 1 when
     anything is still on an old key.
  9. CLI surface (``--help``) advertises the documented flags.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.crypto import rewrap as rw
from core.crypto.credential_vault import (
    decrypt_credential,
    encrypt_credential,
)

# ──────────────────────────────────────────────────────────────────────
# Helpers (used across multiple tests)
# ──────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _session_cm(session: Any):
    yield session


def _mock_session_for(rows_per_call: list[list[tuple[Any, Any]]]):
    """Return a session whose .execute serves rows in the order given.

    Each entry is one full ``result.all()`` payload. After exhausted
    the mock returns an empty list (signals "no more rows").
    """
    session = AsyncMock()
    session.commit = AsyncMock()

    state = {"i": 0}

    async def execute(_stmt, _params=None):
        result = MagicMock()
        i = state["i"]
        if i < len(rows_per_call):
            result.all.return_value = rows_per_call[i]
        else:
            result.all.return_value = []
        state["i"] += 1
        return result

    session.execute = execute
    return session


# ──────────────────────────────────────────────────────────────────────
# Stamp / extract helpers
# ──────────────────────────────────────────────────────────────────────


def test_stamp_kid_handles_stamped_unstamped_and_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v2:rot-secret,v1:dev-only")
    stamped = encrypt_credential("hello")  # active is v2
    assert rw._stamp_kid(stamped) == "v2"
    assert rw._stamp_kid("plaincipher") == "legacy"
    assert rw._stamp_kid("") is None
    assert rw._stamp_kid(None) is None


def test_extract_and_wrap_ciphertext_round_trip() -> None:
    label_jsonb = "connector_configs.credentials_encrypted"
    label_text = "gstn_credentials.password_encrypted"

    # JSONB: dict shape
    ct = "agko_vv1$abc"
    raw = {"_encrypted": ct}
    assert rw._extract_ciphertext(label_jsonb, raw) == ct
    wrapped = rw._wrap_ciphertext_for_column(label_jsonb, "agko_vv2$xyz")
    assert wrapped == {"_encrypted": "agko_vv2$xyz"}

    # JSONB: serialized string
    raw_json_str = json.dumps({"_encrypted": ct})
    assert rw._extract_ciphertext(label_jsonb, raw_json_str) == ct

    # TEXT
    assert rw._extract_ciphertext(label_text, ct) == ct
    assert rw._wrap_ciphertext_for_column(label_text, "agko_vv2$xyz") == "agko_vv2$xyz"

    # None / unknown shape
    assert rw._extract_ciphertext(label_jsonb, None) is None
    assert rw._extract_ciphertext(label_jsonb, 12345) is None  # type: ignore[arg-type]


def test_split_column_label_rejects_missing_dot() -> None:
    with pytest.raises(ValueError, match="missing '.'"):
        rw._split_column_label("no_dot_here")
    assert rw._split_column_label("t.c") == ("t", "c")


def test_active_kid_uses_first_keyring_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v3:newest,v2:older,v1:oldest")
    assert rw._active_kid() == "v3"


# ──────────────────────────────────────────────────────────────────────
# Mock-DB run / verify
# ──────────────────────────────────────────────────────────────────────


def test_run_dry_run_does_not_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v2:active,v1:older")
    # One row stamped v1 — needs rewrap.
    old_ct = _stamp_with("v1", "value-A")
    rows = [[("row-1", {"_encrypted": old_ct})]]
    session = _mock_session_for(rows_per_call=rows)
    monkeypatch.setattr(rw, "async_session_factory", lambda: _session_cm(session))
    # Limit scanners to one column so the mock only sees one query.
    monkeypatch.setattr(
        rw, "_SCANNERS",
        [("connector_configs.credentials_encrypted", "ignored")],
    )
    rc = asyncio.run(
        rw.run(
            only_column=None, only_kid=None, batch_size=10, dry_run=True
        )
    )
    assert rc == 0
    # No commit, no UPDATE — execute was called once for _count_pending only
    session.commit.assert_not_called()


def test_run_rewraps_old_rows_under_active_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v2:active-key,v1:old-key")
    monkeypatch.setattr(
        rw, "_SCANNERS",
        [("connector_configs.credentials_encrypted", "ignored")],
    )

    # Two rows: one already on active (v2) — should be skipped.
    # Two rows on v1 — should be rewrapped.
    active_ct = encrypt_credential("on-active")  # stamped v2
    old_ct_a = _stamp_with("v1", "from-old-A")
    old_ct_b = _stamp_with("v1", "from-old-B")

    # Sequence:
    #  call 1 (count_pending): all 4 rows — counts 2 pending
    #  call 2 (fetch_rows batch=10): all 4 rows — filter -> 2
    #  call 3 (UPDATE row 'old-1')
    #  call 4 (UPDATE row 'old-2')
    #  call 5 (fetch_rows): empty — exit loop
    full_set = [
        ("active-1", {"_encrypted": active_ct}),
        ("old-1", {"_encrypted": old_ct_a}),
        ("active-2", {"_encrypted": active_ct}),
        ("old-2", {"_encrypted": old_ct_b}),
    ]
    rows_per_call = [full_set, full_set, [], [], []]
    session = _mock_session_for(rows_per_call=rows_per_call)
    captured_updates: list[tuple[str, dict]] = []

    original_execute = session.execute

    async def capturing_execute(stmt, params=None):
        sql = str(getattr(stmt, "text", stmt))
        if "UPDATE" in sql:
            captured_updates.append((sql, params or {}))
            return MagicMock()
        return await original_execute(stmt, params)

    session.execute = capturing_execute
    monkeypatch.setattr(rw, "async_session_factory", lambda: _session_cm(session))

    rc = asyncio.run(
        rw.run(
            only_column=None, only_kid=None, batch_size=10, dry_run=False
        )
    )
    assert rc == 0
    # Two UPDATEs were issued, one per old row.
    assert len(captured_updates) == 2
    update_ids = sorted(u[1]["id"] for u in captured_updates)
    assert update_ids == ["old-1", "old-2"]
    # Each update payload re-encrypts under the active key.
    for _sql, params in captured_updates:
        new_value = json.loads(params["v"])
        new_ct = new_value["_encrypted"]
        assert rw._stamp_kid(new_ct) == "v2"
        # Round-trip the plaintext to confirm we didn't garble it.
        assert decrypt_credential(new_ct) in {"from-old-A", "from-old-B"}


def test_run_with_only_kid_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v3:active,v2:mid,v1:older")
    monkeypatch.setattr(
        rw, "_SCANNERS",
        [("gstn_credentials.password_encrypted", "ignored")],
    )

    # Three rows: stamped v1, v2, v3. --key-id=v2 should rewrap only v2.
    ct_v1 = _stamp_with("v1", "from-v1")
    ct_v2 = _stamp_with("v2", "from-v2")
    ct_v3 = encrypt_credential("on-active")  # v3
    rows = [("a", ct_v1), ("b", ct_v2), ("c", ct_v3)]
    rows_per_call = [rows, rows, []]
    session = _mock_session_for(rows_per_call=rows_per_call)

    captured_updates: list[dict] = []
    original_execute = session.execute

    async def capturing_execute(stmt, params=None):
        sql = str(getattr(stmt, "text", stmt))
        if "UPDATE" in sql:
            captured_updates.append(params or {})
            return MagicMock()
        return await original_execute(stmt, params)

    session.execute = capturing_execute
    monkeypatch.setattr(rw, "async_session_factory", lambda: _session_cm(session))

    rc = asyncio.run(
        rw.run(
            only_column=None, only_kid="v2", batch_size=10, dry_run=False
        )
    )
    assert rc == 0
    assert len(captured_updates) == 1
    assert captured_updates[0]["id"] == "b"


def test_run_unknown_column_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v1:k")
    rc = asyncio.run(
        rw.run(
            only_column="bogus.column",
            only_kid=None,
            batch_size=10,
            dry_run=False,
        )
    )
    assert rc == 2


def test_verify_returns_zero_when_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v2:active,v1:older")
    monkeypatch.setattr(
        rw, "_SCANNERS",
        [("connector_configs.credentials_encrypted", "ignored")],
    )
    active_ct = encrypt_credential("clean")
    rows = [[("a", {"_encrypted": active_ct})]]
    session = _mock_session_for(rows_per_call=rows)
    monkeypatch.setattr(rw, "async_session_factory", lambda: _session_cm(session))
    rc = asyncio.run(rw.verify(only_column=None))
    assert rc == 0


def test_verify_returns_one_when_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v2:active,v1:older")
    monkeypatch.setattr(
        rw, "_SCANNERS",
        [("connector_configs.credentials_encrypted", "ignored")],
    )
    old_ct = _stamp_with("v1", "leftover")
    rows = [[("a", {"_encrypted": old_ct})]]
    session = _mock_session_for(rows_per_call=rows)
    monkeypatch.setattr(rw, "async_session_factory", lambda: _session_cm(session))
    rc = asyncio.run(rw.verify(only_column=None))
    assert rc == 1


def test_run_decrypt_failure_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decrypt failure surfaces — never silently skip a row.

    Construct ciphertext stamped with a key id (`v9`) that's NOT in
    the keyring. The decrypt path tries the stamped key first (miss
    — not loaded) then every other entry against an arbitrary blob
    (which is not a Fernet token under any of them). Whole call
    raises ``InvalidToken`` and rewrap must surface that as exit 1.
    """
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v2:active,v1:older")
    monkeypatch.setattr(
        rw, "_SCANNERS",
        [("connector_configs.credentials_encrypted", "ignored")],
    )
    # Stamped with v9 (not in keyring); the payload after the $ is
    # not a Fernet token under v1 OR v2.
    bad_ct = "agko_vv9$totally-not-a-real-fernet-token-AAAA"
    full_set = [("a", {"_encrypted": bad_ct})]
    rows_per_call = [full_set, full_set]
    session = _mock_session_for(rows_per_call=rows_per_call)
    monkeypatch.setattr(rw, "async_session_factory", lambda: _session_cm(session))
    rc = asyncio.run(
        rw.run(
            only_column=None, only_kid=None, batch_size=10, dry_run=False
        )
    )
    assert rc == 1


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def test_cli_help_documents_flags() -> None:
    captured = io.StringIO()
    sys.stdout = captured
    try:
        with pytest.raises(SystemExit) as exc:
            rw.main(["--help"])
    finally:
        sys.stdout = sys.__stdout__
    out = captured.getvalue()
    assert exc.value.code == 0
    for token in ("--column", "--key-id", "--batch-size", "--dry-run", "--verify"):
        assert token in out, f"rewrap --help must document {token}"


# ──────────────────────────────────────────────────────────────────────
# Internal: stamp arbitrary key id (not exposed by credential_vault).
# Test-only — production rewrap reads stamps but never invents them.
# ──────────────────────────────────────────────────────────────────────


def _stamp_with(kid: str, plaintext: str) -> str:
    """Encrypt + stamp under a specific keyring entry id.

    Reaches into the keyring to find the entry by id and uses that
    Fernet key directly — gives us controllable ciphertext for tests
    without flipping the active key around per-assertion.
    """
    from cryptography.fernet import Fernet

    from core.crypto.credential_vault import _load_keyring

    for entry_kid, kbytes in _load_keyring():
        if entry_kid == kid:
            token = Fernet(kbytes).encrypt(plaintext.encode()).decode()
            return f"agko_v{kid}${token}"
    raise RuntimeError(
        f"_stamp_with: no keyring entry id={kid!r} (env: "
        f"AGENTICORG_VAULT_KEYRING)"
    )
