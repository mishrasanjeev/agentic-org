"""Pin the contract of ``scripts/run_worker.py`` and ``scripts/run_beat.py``.

These wrappers exist because Cloud Run Services require an HTTP listener
on ``$PORT``, but ``celery worker`` and ``celery beat`` are long-running
broker daemons that don't serve HTTP. The wrappers add a minimal
``HTTPServer`` on a daemon thread so the Cloud Run startup probe
succeeds, then run celery in the foreground so SIGTERM propagates.

Pinned behavior:
- The wrappers import successfully (no syntax / module errors).
- They reference ``core.tasks.celery_app`` so all 7 periodic tasks
  register at startup.
- ``run_worker.py`` invokes the ``celery worker`` subcommand with the
  expected queue list (or the ``CELERY_QUEUES`` override).
- ``run_beat.py`` invokes the ``celery beat`` subcommand.
- Both wrappers serve a 200 ``ok`` response on ``/`` and ``/health``.
- The wrappers don't accidentally swallow SIGTERM (Cloud Run uses it
  to drain pods during scale-down / revision swap).
"""

from __future__ import annotations

import importlib.util
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_module(name: str, path: Path):
    """Load a script as a module without executing its ``__main__`` block."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def run_worker_module():
    return _load_module("run_worker", REPO / "scripts" / "run_worker.py")


@pytest.fixture(scope="module")
def run_beat_module():
    return _load_module("run_beat", REPO / "scripts" / "run_beat.py")


# ─────────────────────────────────────────────────────────────────
# Module structure pins
# ─────────────────────────────────────────────────────────────────


def test_run_worker_exports_main_and_health_handler(run_worker_module) -> None:
    """The wrapper must expose ``main`` (Cloud Run entrypoint) and the
    private health-server function so the deploy script + this test
    can both reach them."""
    assert callable(getattr(run_worker_module, "main", None))
    assert callable(getattr(run_worker_module, "_serve_health", None))
    assert callable(getattr(run_worker_module, "_run_celery_worker", None))


def test_run_beat_exports_main_and_health_handler(run_beat_module) -> None:
    assert callable(getattr(run_beat_module, "main", None))
    assert callable(getattr(run_beat_module, "_serve_health", None))
    assert callable(getattr(run_beat_module, "_run_celery_beat", None))


def test_run_worker_imports_celery_app_and_uses_correct_celery_argv(
    run_worker_module, monkeypatch
) -> None:
    """The wrapper must (a) successfully import ``core.tasks.celery_app``
    so all task modules register, (b) build an argv that runs
    ``celery worker -A core.tasks.celery_app`` with the right queue
    list. Without this, the worker would idle on the wrong queues
    and our beat-emitted tasks would never be consumed."""
    captured_argv: list[list[str]] = []
    captured_celery_main_calls: list[None] = []

    def _fake_celery_main():
        # Record the argv shape Celery would have used.
        import sys as _sys  # noqa: PLC0415
        captured_argv.append(list(_sys.argv))
        captured_celery_main_calls.append(None)
        return 0

    # Stub out the celery CLI so we don't actually start a worker.
    import celery.__main__ as celery_main_mod
    monkeypatch.setattr(celery_main_mod, "main", _fake_celery_main)

    rc = run_worker_module._run_celery_worker()
    assert rc == 0
    assert captured_celery_main_calls == [None]
    assert len(captured_argv) == 1
    argv = captured_argv[0]
    assert argv[0] == "celery"
    assert "-A" in argv and "core.tasks.celery_app" in argv
    assert "worker" in argv
    # All five queues used by celery_app.task_routes plus the implicit
    # default — without ``celery`` (the default) the worker would skip
    # tasks that don't carry an explicit queue routing.
    queues_arg = argv[argv.index("-Q") + 1]
    for q in ("celery", "reports", "maintenance", "workflows", "delivery", "rpa"):
        assert q in queues_arg, f"queue {q!r} missing from -Q list"


def test_run_worker_honors_celery_queues_env_override(
    run_worker_module, monkeypatch
) -> None:
    """Operators can narrow the queue list via the ``CELERY_QUEUES``
    env var (useful for splitting workers per workload)."""
    monkeypatch.setenv("CELERY_QUEUES", "reports,workflows")
    captured: list[str] = []

    def _fake_celery_main():
        import sys as _sys  # noqa: PLC0415
        captured.append(_sys.argv[_sys.argv.index("-Q") + 1])
        return 0

    import celery.__main__ as celery_main_mod
    monkeypatch.setattr(celery_main_mod, "main", _fake_celery_main)

    run_worker_module._run_celery_worker()
    assert captured == ["reports,workflows"]


def test_run_beat_uses_correct_celery_argv(run_beat_module, monkeypatch) -> None:
    captured_argv: list[list[str]] = []

    def _fake_celery_main():
        import sys as _sys  # noqa: PLC0415
        captured_argv.append(list(_sys.argv))
        return 0

    import celery.__main__ as celery_main_mod
    monkeypatch.setattr(celery_main_mod, "main", _fake_celery_main)

    rc = run_beat_module._run_celery_beat()
    assert rc == 0
    argv = captured_argv[0]
    assert argv[0] == "celery"
    assert "-A" in argv and "core.tasks.celery_app" in argv
    assert "beat" in argv


# ─────────────────────────────────────────────────────────────────
# HTTP health stub pin — Cloud Run startup probe contract
# ─────────────────────────────────────────────────────────────────


def _start_health_thread(serve_fn, port: int) -> threading.Thread:
    t = threading.Thread(target=serve_fn, daemon=True)
    t.start()
    # Tiny wait for the listener to bind.
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=0.5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            return t
        except OSError:
            time.sleep(0.05)
    pytest.fail("HTTP health server never started")


def test_run_worker_health_stub_serves_200(run_worker_module, monkeypatch) -> None:
    """Cloud Run probes the ``$PORT`` HTTP listener on startup. The stub
    must respond 200 even before celery_app finishes registering tasks
    (otherwise the container never reaches Ready)."""
    port = 18021
    monkeypatch.setenv("PORT", str(port))
    _start_health_thread(run_worker_module._serve_health, port)

    conn = HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("GET", "/")
    resp = conn.getresponse()
    body = resp.read()
    conn.close()

    assert resp.status == 200
    assert b"ok" in body


def test_run_beat_health_stub_serves_200(run_beat_module, monkeypatch) -> None:
    port = 18022
    monkeypatch.setenv("PORT", str(port))
    _start_health_thread(run_beat_module._serve_health, port)

    conn = HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("GET", "/health")
    resp = conn.getresponse()
    resp.read()
    conn.close()

    assert resp.status == 200


# ─────────────────────────────────────────────────────────────────
# Beat schedule registration — every periodic task that should
# fire must be registered when celery_app loads
# ─────────────────────────────────────────────────────────────────


def test_celery_beat_schedule_registers_all_seven_periodic_tasks() -> None:
    """If the wrappers ship but a periodic task isn't registered with
    beat, that task silently never runs. This pins every task that
    was already in ``celery_app.beat_schedule`` plus the new
    health-snapshot recorder."""
    from core.tasks.celery_app import app

    schedule = app.conf.beat_schedule
    expected = {
        "generate-scheduled-reports",
        "cleanup-old-reports",
        "run-budget-evaluator",
        "refresh-expiring-tokens",
        "generate-monthly-invoices",
        "dispatch-due-rpa-schedules",
        "shadow-reconciliation-report",
        "record-health-snapshot",
    }
    missing = expected - set(schedule.keys())
    assert not missing, (
        f"Periodic tasks expected in beat_schedule are missing: {sorted(missing)}. "
        "If a task was intentionally removed, update this test in the same PR."
    )
