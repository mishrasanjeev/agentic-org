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


def main() -> int:
    # Health server runs as a daemon thread so it dies cleanly when the
    # main worker process exits. Celery worker runs in the foreground so
    # signals (SIGTERM from Cloud Run scale-down) reach Celery directly
    # and it shuts down gracefully — partially-processed tasks are NACK'd
    # back to the broker.
    threading.Thread(target=_serve_health, daemon=True).start()

    # Be explicit about signal handling so a misconfigured signal handler
    # in some imported module doesn't swallow SIGTERM. Default is fine
    # for celery — just don't override it.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    return _run_celery_worker() or 0


if __name__ == "__main__":
    sys.exit(main())
