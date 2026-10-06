# SPDX-License-Identifier: Apache-2.0
"""Replay Cloud Run's fresh-process boot, not pytest's already-imported registry."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("runtime", ["development", "production"])
def test_worker_initializes_metrics_before_vault_imports(tmp_path, runtime):
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("AGENTICORG_", "PROMETHEUS_", "OTEL_"))
    }
    env.update(
        {
            "AGENTICORG_ENV": runtime,
            "AGENTICORG_SECRET_KEY": "synthetic-worker-startup-signing-material-20261006",
            "AGENTICORG_VAULT_KEY": "synthetic-worker-startup-vault-material-20261006",
            "AGENTICORG_DB_URL": "postgresql+asyncpg://test:test@db.example.test/worker",
            "AGENTICORG_REDIS_URL": "redis://cache.example.test:6379/0",
            "TMPDIR": str(tmp_path),
            "TMP": str(tmp_path),
            "TEMP": str(tmp_path),
        }
    )
    code = """
import runpy
from pathlib import Path

worker = runpy.run_path("scripts/run_worker.py")
main = worker["main"]
namespace = main.__globals__
started = []

class HealthThread:
    def __init__(self, **kwargs):
        pass
    def start(self):
        started.append(True)

namespace["threading"].Thread = HealthThread

def run_cli():
    from celery.signals import worker_ready
    from prometheus_client import REGISTRY
    from observability.metrics_export import multiprocess_dir
    counter = REGISTRY._names_to_collectors["agenticorg_db_cross_loop_checkouts"]
    assert counter.labels(mode="warn")._value._multiprocess is True
    assert Path(multiprocess_dir()).is_dir()
    assert started == [], "health must wait for Celery readiness"
    worker_ready.send(sender=None)
    assert started == [True]
    return 0

namespace["_run_celery_worker"] = run_cli
# Only sockets/CLI are stubbed: metric initialization and vault imports are real.
import observability.metrics_export as metrics
metrics.start_metrics_server = lambda: None
assert main() == 0
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and literal test program
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "metrics_multiprocess_enabled_late" not in result.stdout + result.stderr


def test_metrics_failure_never_starts_health_or_reads_vault(monkeypatch):
    import runpy

    main = runpy.run_path(str(ROOT / "scripts/run_worker.py"))["main"]
    namespace = main.__globals__

    def fail_metrics():
        raise RuntimeError("synthetic metrics failure")

    def forbidden(*args, **kwargs):
        pytest.fail("vault and health must follow metric initialization")

    monkeypatch.setitem(namespace, "_enable_multiprocess_metrics", fail_metrics)
    monkeypatch.setitem(namespace, "_vault_key_problem", forbidden)
    monkeypatch.setattr(namespace["threading"], "Thread", forbidden)
    with pytest.raises(RuntimeError, match="synthetic metrics failure"):
        main()


def test_health_listener_is_wired_to_celery_readiness(monkeypatch):
    import runpy

    import celery.signals
    from celery.utils.dispatch import Signal

    from observability import metrics_export

    main = runpy.run_path(str(ROOT / "scripts/run_worker.py"))["main"]
    namespace = main.__globals__
    ready = Signal()
    events = []

    def vault_check():
        events.append("vault")
        return None

    class HealthThread:
        def __init__(self, *, target, daemon):
            assert target is namespace["_serve_health"]
            assert daemon is True

        def start(self):
            events.append("health")

    def run_cli():
        assert events == ["metrics", "vault", "exporter", "cleanup", "signal"]
        ready.send(sender=None)
        assert events[-1] == "health"
        return 0

    monkeypatch.setattr(celery.signals, "worker_ready", ready)
    # Fresh-process coverage above owns real initialization. This isolated
    # signal test measures the entrypoint wiring without reinitializing globals.
    monkeypatch.setitem(namespace, "_enable_multiprocess_metrics", lambda: events.append("metrics"))
    monkeypatch.setitem(namespace, "_vault_key_problem", vault_check)
    monkeypatch.setitem(namespace, "_register_child_cleanup", lambda: events.append("cleanup"))
    monkeypatch.setitem(namespace, "_run_celery_worker", run_cli)
    monkeypatch.setattr(metrics_export, "start_metrics_server", lambda: events.append("exporter"))
    monkeypatch.setattr(namespace["signal"], "signal", lambda *args: events.append("signal"))
    monkeypatch.setattr(namespace["threading"], "Thread", HealthThread)
    assert main() == 0
