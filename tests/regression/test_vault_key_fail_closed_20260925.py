# SPDX-License-Identifier: Apache-2.0
"""The credential vault never encrypts under a key published in this repository.

``core/crypto/credential_vault.py`` fell back to
``AGENTICORG_VAULT_KEY``, then ``AGENTICORG_SECRET_KEY``, then the literal
``"dev-only-vault-key"``. Nothing checked the runtime, and pydantic-settings
reads ``.env`` into ``Settings`` without exporting it to ``os.environ``, so an
operator who configured secrets in ``.env`` passed the production secret checks
and then sealed every connector credential and LangGraph checkpoint under the
literal.

Outside an explicitly local or test runtime the vault now needs
``AGENTICORG_VAULT_KEYRING`` or a non-blank key in the process environment that
is not a placeholder published in this repository. An unset or unknown
``AGENTICORG_ENV`` counts as strict. A keyring that is set but yields no usable
entry is refused everywhere. The API refuses to start, and so does a worker,
through Celery's real ``worker_init`` dispatch and the Cloud Run entrypoint.
Refusals and keyring parse errors never quote key material.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest
from cryptography.fernet import Fernet, InvalidToken

from core.config import PUBLISHED_PLACEHOLDER_SECRETS, Settings
from core.crypto import credential_vault as vault
from core.crypto.credential_vault import (
    VaultKeyNotConfiguredError,
    assert_vault_key_configured,
    decrypt_credential,
    encrypt_credential,
    verify_credential,
)

# Invented, clearly-not-real key material for these tests only.
_REAL_VAULT_KEY = "example-vault-key-for-tests-0123456789abcdef"
_REAL_SECRET_KEY = "example-secret-key-for-tests-0123456789abcdef"

_STRICT_ENVS = ["production", "prod", "staging", "preview", " Production ", "qa", "unknown-label"]
_RELAXED_ENVS = ["local", "dev", "development", "test", "ci", " TEST "]

_VAULT_VARS = ("AGENTICORG_VAULT_KEYRING", "AGENTICORG_VAULT_KEY", "AGENTICORG_SECRET_KEY")


@pytest.fixture
def clean_vault_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _VAULT_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _runtime(monkeypatch: pytest.MonkeyPatch, env: str | None) -> None:
    if env is None:
        monkeypatch.delenv("AGENTICORG_ENV", raising=False)
    else:
        monkeypatch.setenv("AGENTICORG_ENV", env)


def _literal_default_ciphertext() -> str:
    """Ciphertext sealed under the literal the old fallback used."""
    return Fernet(vault._derive_fernet_key("dev-only-vault-key")).encrypt(b"provider-token").decode()


# ---------------------------------------------------------------------------
# Strict runtimes refuse the code defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", [*_STRICT_ENVS, None])
def test_strict_runtime_with_no_key_refuses_to_encrypt(clean_vault_env, env):
    _runtime(clean_vault_env, env)
    with pytest.raises(VaultKeyNotConfiguredError) as info:
        encrypt_credential("provider-token")
    message = str(info.value)
    assert "AGENTICORG_VAULT_KEYRING" in message
    assert "AGENTICORG_ENV" in message


@pytest.mark.parametrize("env", [*_STRICT_ENVS, None])
def test_strict_runtime_with_no_key_refuses_to_decrypt_literal_default_ciphertext(clean_vault_env, env):
    _runtime(clean_vault_env, env)
    with pytest.raises(VaultKeyNotConfiguredError):
        decrypt_credential(_literal_default_ciphertext())
    assert verify_credential(_literal_default_ciphertext()) is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENTICORG_VAULT_KEY", "dev-only-vault-key"),
        ("AGENTICORG_VAULT_KEY", "dev-only-secret-key"),
        ("AGENTICORG_SECRET_KEY", "dev-only-secret-key"),
        ("AGENTICORG_SECRET_KEY", "dev-only-vault-key"),
        ("AGENTICORG_VAULT_KEY", ""),
        ("AGENTICORG_VAULT_KEY", "   "),
        ("AGENTICORG_SECRET_KEY", ""),
        ("AGENTICORG_SECRET_KEY", "  \t "),
    ],
)
def test_strict_runtime_refuses_a_default_or_blank_key(clean_vault_env, name, value):
    _runtime(clean_vault_env, "production")
    clean_vault_env.setenv(name, value)
    with pytest.raises(VaultKeyNotConfiguredError):
        encrypt_credential("provider-token")


def test_a_blank_vault_key_does_not_shadow_a_real_secret_key(clean_vault_env):
    _runtime(clean_vault_env, "production")
    clean_vault_env.setenv("AGENTICORG_VAULT_KEY", "  ")
    clean_vault_env.setenv("AGENTICORG_SECRET_KEY", _REAL_SECRET_KEY)
    token = encrypt_credential("provider-token")
    expected = Fernet(vault._derive_fernet_key(_REAL_SECRET_KEY))
    assert expected.decrypt(token.split("$", 1)[1].encode()) == b"provider-token"


def test_a_default_vault_key_is_refused_even_when_a_real_secret_key_is_set(clean_vault_env):
    """An explicit vault key that is a published default is a misconfiguration, not a miss."""
    _runtime(clean_vault_env, "production")
    clean_vault_env.setenv("AGENTICORG_VAULT_KEY", "dev-only-vault-key")
    clean_vault_env.setenv("AGENTICORG_SECRET_KEY", _REAL_SECRET_KEY)
    with pytest.raises(VaultKeyNotConfiguredError):
        encrypt_credential("provider-token")


# ---------------------------------------------------------------------------
# Strict runtimes still work with a real key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["production", None])
def test_strict_runtime_uses_an_explicit_vault_key(clean_vault_env, env):
    _runtime(clean_vault_env, env)
    clean_vault_env.setenv("AGENTICORG_VAULT_KEY", _REAL_VAULT_KEY)
    token = encrypt_credential("provider-token")
    assert token.startswith("agko_vlegacy$")
    assert decrypt_credential(token) == "provider-token"
    assert_vault_key_configured()


@pytest.mark.parametrize("env", ["production", None])
def test_strict_runtime_keeps_the_secret_key_fallback_for_existing_deployments(clean_vault_env, env):
    """Deployments that sealed rows under AGENTICORG_SECRET_KEY keep decrypting them."""
    _runtime(clean_vault_env, env)
    clean_vault_env.setenv("AGENTICORG_SECRET_KEY", _REAL_SECRET_KEY)
    sealed = Fernet(vault._derive_fernet_key(_REAL_SECRET_KEY)).encrypt(b"provider-token").decode()
    assert decrypt_credential(sealed) == "provider-token"
    assert_vault_key_configured()


@pytest.mark.parametrize("env", ["production", None])
def test_strict_runtime_uses_the_keyring(clean_vault_env, env):
    _runtime(clean_vault_env, env)
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", f"v2:{_REAL_VAULT_KEY},v1:{_REAL_SECRET_KEY}")
    token = encrypt_credential("provider-token")
    assert token.startswith("agko_vv2$")
    assert decrypt_credential(token) == "provider-token"
    assert_vault_key_configured()


# ---------------------------------------------------------------------------
# A keyring that is set but unusable is refused in every runtime
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["production", "test", "development"])
@pytest.mark.parametrize("spec", [",", " , , ", "\t"])
def test_a_keyring_with_no_entries_is_refused_not_ignored(clean_vault_env, env, spec):
    """Before, an empty keyring fell through to the single-key fallback silently."""
    _runtime(clean_vault_env, env)
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", spec)
    clean_vault_env.setenv("AGENTICORG_SECRET_KEY", _REAL_SECRET_KEY)
    if spec.strip() == "":
        # Whitespace-only is the same as unset: nothing was configured.
        encrypt_credential("provider-token")
        return
    with pytest.raises(VaultKeyNotConfiguredError, match="no usable entry"):
        encrypt_credential("provider-token")


@pytest.mark.parametrize("env", ["production", "test"])
@pytest.mark.parametrize("spec", ["v1:", "v1:   ", f"v2:{_REAL_VAULT_KEY},v1:"])
def test_a_keyring_entry_with_blank_key_material_is_refused(clean_vault_env, env, spec):
    """``v1:`` would derive the vault key from the empty string, which is public."""
    _runtime(clean_vault_env, env)
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", spec)
    with pytest.raises(VaultKeyNotConfiguredError) as info:
        encrypt_credential("provider-token")
    assert "'v1'" in str(info.value)


@pytest.mark.parametrize("env", ["production", "test"])
def test_a_keyring_entry_holding_a_code_default_is_refused(clean_vault_env, env):
    _runtime(clean_vault_env, env)
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", f"v2:{_REAL_VAULT_KEY},v1:dev-only-vault-key")
    if env == "test":
        # Local and test runtimes may use the defaults on purpose.
        assert decrypt_credential(encrypt_credential("provider-token")) == "provider-token"
        return
    with pytest.raises(VaultKeyNotConfiguredError) as info:
        encrypt_credential("provider-token")
    assert "'v1'" in str(info.value)


def test_refusal_messages_never_quote_key_material(clean_vault_env):
    _runtime(clean_vault_env, "production")
    secret_looking = "example-material-that-must-not-leak"
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", f"v2:{secret_looking},v1:")
    with pytest.raises(VaultKeyNotConfiguredError) as info:
        encrypt_credential("provider-token")
    assert secret_looking not in str(info.value)


# ---------------------------------------------------------------------------
# Local and test runtimes keep working with no configuration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", _RELAXED_ENVS)
def test_relaxed_runtime_still_has_the_development_fallback(clean_vault_env, env):
    _runtime(clean_vault_env, env)
    assert decrypt_credential(_literal_default_ciphertext()) == "provider-token"
    assert decrypt_credential(encrypt_credential("provider-token")) == "provider-token"
    assert_vault_key_configured()


def test_the_error_is_a_value_error_so_existing_handlers_stay_closed():
    """``verify_credential`` and the checkpointer already treat ``ValueError`` as a refusal."""
    assert issubclass(VaultKeyNotConfiguredError, ValueError)


# ---------------------------------------------------------------------------
# Startup refuses, and the checkpointer reports a missing key
# ---------------------------------------------------------------------------


def test_startup_check_refuses_a_strict_runtime_without_a_key(clean_vault_env):
    _runtime(clean_vault_env, None)
    with pytest.raises(VaultKeyNotConfiguredError):
        assert_vault_key_configured()


def test_checkpointer_reports_the_key_as_missing(clean_vault_env):
    from core.langgraph.checkpointer import CheckpointerUnavailableError, sealed_serializer

    _runtime(clean_vault_env, "production")
    with pytest.raises(CheckpointerUnavailableError) as info:
        sealed_serializer()
    assert info.value.reason == "checkpoint_encryption_key_missing"


def test_worker_vault_check_is_connected_to_worker_init():
    from core.tasks import celery_app

    tree = ast.parse(inspect.getsource(celery_app))
    handlers = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(ast.unparse(d) == "worker_init.connect" for d in node.decorator_list)
    ]
    assert handlers == ["_refuse_worker_without_vault_key"]


def test_celery_dispatch_of_worker_init_stops_a_worker_without_a_key(clean_vault_env):
    """Celery's Signal.send swallows Exception; the refusal must survive a real dispatch."""
    from celery.signals import worker_init

    import core.tasks.celery_app  # noqa: F401  registers the handler

    _runtime(clean_vault_env, None)
    with pytest.raises(SystemExit) as info:
        worker_init.send(sender=None)
    assert "AGENTICORG_VAULT_KEYRING" in str(info.value.code)


def test_celery_dispatch_of_worker_init_does_not_leak_a_malformed_keyring(clean_vault_env):
    from celery.signals import worker_init

    import core.tasks.celery_app  # noqa: F401

    _runtime(clean_vault_env, "production")
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", "example-material-with-no-id")
    with pytest.raises(SystemExit) as info:
        worker_init.send(sender=None)
    assert "example-material-with-no-id" not in str(info.value.code)
    assert "entry 1" in str(info.value.code)


def test_celery_dispatch_of_worker_init_accepts_a_configured_key(clean_vault_env):
    from celery.signals import worker_init

    import core.tasks.celery_app  # noqa: F401

    _runtime(clean_vault_env, "production")
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", f"v1:{_REAL_VAULT_KEY}")
    for _receiver, response in worker_init.send(sender=None):
        assert not isinstance(response, BaseException)


def _load_run_worker():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "run_worker.py"
    spec = importlib.util.spec_from_file_location("run_worker_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cloud_run_worker_entrypoint_exits_before_its_health_server(clean_vault_env, monkeypatch, capsys):
    """A healthy probe on a worker that cannot open the vault would hide the failure."""
    run_worker = _load_run_worker()

    def _no_threads(*_args, **_kwargs):
        raise AssertionError("the health server must not start without a vault key")

    monkeypatch.setattr(run_worker.threading, "Thread", _no_threads)
    _runtime(clean_vault_env, None)
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", "example-material-with-no-id")
    assert run_worker.main() == 1
    err = capsys.readouterr().err
    assert "Refusing to start the worker" in err
    assert "example-material-with-no-id" not in err


def test_cloud_run_worker_entrypoint_reports_no_problem_with_a_key(clean_vault_env):
    run_worker = _load_run_worker()
    _runtime(clean_vault_env, "production")
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", f"v1:{_REAL_VAULT_KEY}")
    assert run_worker._vault_key_problem() is None


# ---------------------------------------------------------------------------
# Every placeholder published in the repository is refused, however padded
# ---------------------------------------------------------------------------


def _variants(value: str) -> list[str]:
    return [value, f" {value}", f"{value}\n", f"\t{value} ", value.upper()]


@pytest.mark.parametrize("placeholder", sorted(PUBLISHED_PLACEHOLDER_SECRETS))
@pytest.mark.parametrize("name", ["AGENTICORG_VAULT_KEY", "AGENTICORG_SECRET_KEY", "AGENTICORG_VAULT_KEYRING"])
def test_strict_runtime_refuses_every_published_placeholder(clean_vault_env, placeholder, name):
    _runtime(clean_vault_env, "production")
    for value in _variants(placeholder):
        clean_vault_env.setenv(name, f"v1:{value}" if name == "AGENTICORG_VAULT_KEYRING" else value)
        with pytest.raises(VaultKeyNotConfiguredError) as info:
            assert_vault_key_configured()
        assert placeholder not in str(info.value).lower()


def test_the_placeholder_list_covers_the_values_written_in_the_repository():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    written = {
        ".env.example": "change-me-to-32-char-random-string",
        "docker-compose.yml": "agenticorg-dev-only-do-not-use-in-production",
        "Makefile": "ci-test-secret-key-minimum-16",
        "scripts/local_e2e.sh": "dev-secret-key-change-in-production-32chars",
    }
    for relative, value in written.items():
        assert value in (root / relative).read_text(encoding="utf-8"), relative
        assert value in PUBLISHED_PLACEHOLDER_SECRETS


@pytest.mark.parametrize("placeholder", sorted(PUBLISHED_PLACEHOLDER_SECRETS))
def test_strict_settings_refuse_every_published_placeholder_as_the_secret_key(placeholder):
    with pytest.raises(ValueError, match="AGENTICORG_SECRET_KEY"):
        Settings(
            env="production",
            secret_key=f" {placeholder.upper()} ",
            db_url="postgresql+asyncpg://u:p@db.example.com:5432/db",
            redis_url="redis://cache.example.com:6379/0",
        )


# ---------------------------------------------------------------------------
# Keyring parse errors never quote the entry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("example-material-with-no-id", "entry 1 has no 'id:' prefix"),
        (f"v2:{_REAL_VAULT_KEY},example-material-with-no-id", "entry 2 has no 'id:' prefix"),
        (":example-material-with-no-id", "entry 1 has an empty id"),
    ],
)
@pytest.mark.parametrize("env", ["production", "test"])
def test_keyring_parse_errors_never_quote_the_entry(clean_vault_env, env, spec, expected):
    _runtime(clean_vault_env, env)
    clean_vault_env.setenv("AGENTICORG_VAULT_KEYRING", spec)
    with pytest.raises(ValueError) as info:
        assert_vault_key_configured()
    assert expected in str(info.value)
    assert "example-material-with-no-id" not in str(info.value)
    assert _REAL_VAULT_KEY not in str(info.value)


def test_api_lifespan_checks_the_vault_key_before_touching_the_database():
    from api import main

    tree = ast.parse(textwrap.dedent(inspect.getsource(main.lifespan)))
    calls = [
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ]
    assert "assert_vault_key_configured" in calls
    assert calls.index("assert_vault_key_configured") < calls.index("init_db")


def test_decrypt_with_a_configured_key_still_rejects_literal_default_ciphertext(clean_vault_env):
    """With a real key, rows sealed under the literal are unreadable rather than silently accepted."""
    _runtime(clean_vault_env, "production")
    clean_vault_env.setenv("AGENTICORG_VAULT_KEY", _REAL_VAULT_KEY)
    with pytest.raises(InvalidToken):
        decrypt_credential(_literal_default_ciphertext())
