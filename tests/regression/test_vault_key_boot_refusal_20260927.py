# SPDX-License-Identifier: Apache-2.0
"""The API process refuses to start in a production runtime without a vault key.

``test_vault_key_fail_closed_20260925.py`` proves the check in-process and reads
the lifespan's source to see that it is called before ``init_db``. Neither runs
the server. These tests start ``uvicorn api.main:app``, the API image's command,
in a child process with ``AGENTICORG_ENV=production`` and no vault key in its
process environment, and require it to exit non-zero with the refusal before it
serves anything.

The child's settings come from a ``.env`` file only, which is the deployment
shape the refusal exists for: ``Settings`` reads ``.env``, the vault reads only
the process environment. Every value is synthetic, the database address refuses
connections, and the Redis host is under the reserved ``.invalid`` domain.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Invented for this test, long enough for the strict ``Settings`` checks.
_SETTINGS_SECRET = "example-boot-refusal-secret-0123456789abcdef"
_KEYRING_MATERIAL = "example-boot-refusal-vault-key-0123456789abcdef"

_DOTENV = (
    f"AGENTICORG_SECRET_KEY={_SETTINGS_SECRET}\n"
    # Port 9 (discard) is closed on the loopback interface: a connection is refused at once.
    "AGENTICORG_DB_URL=postgresql+asyncpg://boot_refusal:unused@127.0.0.1:9/agenticorg\n"
    "AGENTICORG_REDIS_URL=redis://cache.invalid:6379/0\n"
)

_REFUSAL = "No credential-vault key is configured"


def _start_api(workdir: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    """Run the API image's command in ``workdir`` with only ``extra_env`` as AgenticOrg settings."""
    (workdir / ".env").write_text(_DOTENV, encoding="utf-8")
    env = {
        name: value
        for name, value in os.environ.items()
        # Nothing from the test runner's own settings (conftest sets AGENTICORG_ENV=test) or a
        # multi-worker setting reaches the child.
        if not name.upper().startswith("AGENTICORG_") and name.upper() not in {"WEB_CONCURRENCY", "PYTHONPATH"}
    }
    env["PYTHONPATH"] = str(ROOT)
    env["AGENTICORG_ENV"] = "production"
    env.update(extra_env)
    return subprocess.run(  # noqa: S603 - literal argv
        [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "0"],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=150,
    )


@pytest.mark.timeout(180)
def test_api_process_exits_non_zero_without_a_vault_key_in_production(tmp_path: Path) -> None:
    run = _start_api(tmp_path)
    output = run.stdout + run.stderr

    assert run.returncode != 0, output
    assert "VaultKeyNotConfiguredError" in output, output
    assert _REFUSAL in output, output
    assert "AGENTICORG_VAULT_KEYRING" in output, output
    assert "AGENTICORG_ENV='production'" in output, output
    # Stopped in the lifespan: never served, never reached the database.
    assert "Application startup complete" not in output, output
    assert "Uvicorn running on" not in output, output
    assert "init_db" not in output, output
    assert _SETTINGS_SECRET not in output


@pytest.mark.timeout(180)
def test_api_process_gets_past_the_vault_check_with_a_keyring(tmp_path: Path) -> None:
    """Control: the same child with a keyring fails later, at the unreachable database."""
    run = _start_api(tmp_path, AGENTICORG_VAULT_KEYRING=f"v1:{_KEYRING_MATERIAL}")
    output = run.stdout + run.stderr

    assert run.returncode != 0, output
    assert _REFUSAL not in output, output
    assert "VaultKeyNotConfiguredError" not in output, output
    assert "init_db" in output, output
    assert _KEYRING_MATERIAL not in output
    assert _SETTINGS_SECRET not in output
