"""Celery worker entrypoint for the ``agenticorg-worker`` Cloud Run Service.

Cloud Run Services require a process that listens on ``$PORT`` for HTTP.
A bare ``celery worker`` doesn't — it long-polls the broker. This script
serves a tiny ``/`` and ``/health`` HTTP endpoint on a background thread
purely to satisfy the Cloud Run startup probe, then runs ``celery worker``
in the foreground (so SIGTERM propagates cleanly during scale-down /
revision swap).

Why not a Cloud Run Job: workers are long-lived consumers of a broker
queue. Jobs have a 1-hour ceiling and are designed for one-shot batch
work. A Service with ``--min-instances=1 --no-cpu-throttling`` is the
right primitive — it stays warm and CPU-active even without HTTP traffic.

Queues consumed: every queue declared in
``core.tasks.celery_app:task_routes`` plus the implicit default queue.
Override with ``CELERY_QUEUES`` env var (comma-separated) for narrow
worker pools.

Local smoke test::

    PORT=8080 python scripts/run_worker.py
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_QUEUES = "celery,reports,maintenance,workflows,delivery,rpa"


# Celery's default pool is prefork: tasks run in forked children, and a counter a child
# increments is invisible to this process, which is the one the collector scrapes. In
# multiprocess mode every process writes its samples to PROMETHEUS_MULTIPROC_DIR and the exporter
# merges them, so the directory has to exist before any instrument is imported. Cloud Run gives
# the service an in-memory volume for it; locally it falls back to a temporary directory.
def _enable_multiprocess_metrics() -> None:
    from observability.metrics_export import enable_multiprocess  # noqa: PLC0415

    enable_multiprocess()


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal health probe — only ever returns 200 once the process is
    up. The worker's actual readiness (broker connection, task imports)
    is logged by Celery; we don't need to expose it on /health because
    Cloud Run revives the container if the process exits."""

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler convention
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, *_args, **_kwargs):
        # Suppress access log spam — Cloud Run probes hit / every few seconds.
        return


def _serve_health() -> None:
    port = int(os.environ.get("PORT", "8080"))
    # nosec B104 — Cloud Run requires the container to bind 0.0.0.0 so
    # the managed load balancer can reach the listening port. Container
    # is already isolated behind the Cloud Run network boundary; only
    # ingress that the platform routes can reach this socket.
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)  # noqa: S104  # nosec B104
    server.serve_forever()


def _run_celery_worker() -> int:
    queues = os.environ.get("CELERY_QUEUES", DEFAULT_QUEUES)

    # Importing the celery_app first makes any task-import error visible
    # in the container logs immediately, instead of after Celery's own
    # later registration step. Faster failure = faster rollback.
    from core.tasks import celery_app  # noqa: F401, PLC0415

    from celery.__main__ import main as celery_main  # noqa: PLC0415

    # Inject the equivalent CLI ``celery -A core.tasks.celery_app worker ...``.
    sys.argv = [
        "celery",
        "-A",
        "core.tasks.celery_app",
        "worker",
        "--loglevel=info",
        "--without-gossip",  # smaller mem footprint for single-replica setups
        "--without-mingle",
        "-Q",
        queues,
    ]
    return celery_main()


def _register_child_cleanup() -> None:
    """Drop a forked child's gauges when Celery retires it, so a dead child stops being reported."""
    from celery.signals import worker_process_shutdown  # noqa: PLC0415

    from observability.metrics_export import mark_process_dead  # noqa: PLC0415

    @worker_process_shutdown.connect
    def _on_child_exit(**_kwargs: object) -> None:
        mark_process_dead(os.getpid())


def _vault_key_problem() -> str | None:
    """Why the credential vault has no usable key, or None when it has one."""
    from core.crypto.credential_vault import assert_vault_key_configured  # noqa: PLC0415

    try:
        assert_vault_key_configured()
    except ValueError as exc:  # parse errors never quote key material
        return f"{type(exc).__name__}: {exc}"
    return None


def main() -> int:
    # Before the health server, so Cloud Run sees a failed start rather than
    # a healthy container whose tasks all fail on the vault.
    problem = _vault_key_problem()
    if problem is not None:
        print(f"Refusing to start the worker: {problem}", file=sys.stderr)
        return 1

    # Health server runs as a daemon thread so it dies cleanly when the
    # main worker process exits. Celery worker runs in the foreground so
    # signals (SIGTERM from Cloud Run scale-down) reach Celery directly
    # and it shuts down gracefully — partially-processed tasks are NACK'd
    # back to the broker.
    threading.Thread(target=_serve_health, daemon=True).start()

    # Metrics on their own port, scraped inside the instance and routed from nowhere.
    _enable_multiprocess_metrics()
    from observability.metrics_export import start_metrics_server  # noqa: PLC0415

    start_metrics_server()
    _register_child_cleanup()

    # Be explicit about signal handling so a misconfigured signal handler
    # in some imported module doesn't swallow SIGTERM. Default is fine
    # for celery — just don't override it.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    return _run_celery_worker() or 0


if __name__ == "__main__":
    sys.exit(main())
