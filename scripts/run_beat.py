"""Celery beat entrypoint for the ``agenticorg-beat`` Cloud Run Service.

Same shape as ``run_worker.py`` (HTTP stub + foreground celery process)
but invokes ``celery beat`` instead of ``celery worker``. Beat is the
*scheduler* — it dispatches tasks defined in ``celery_app.beat_schedule``
to the broker on their cron / interval triggers. Workers (the
``agenticorg-worker`` service) consume from the broker and execute.

Critical operational note: beat MUST be a singleton. Two beat instances
running concurrently produce duplicate triggers (the same scheduled
task fires twice every interval). For Cloud Run that means deploying
this with ``--min-instances=1 --max-instances=1``.

Schedule state: Celery's default beat scheduler stores last-run times
in a local file (``celerybeat-schedule.db``). On Cloud Run with a fresh
container per revision, that state resets each deploy. For our workload
(periodic 5-15 min tasks + a few daily/monthly crons) the cost is at
most one duplicate fire per deploy, which the underlying tasks already
handle (idempotency on monthly invoices, etc.) or won't notice (health
snapshot, budget evaluator). When this becomes a problem, switch to
``--scheduler core.tasks.beat_redis_scheduler.RedisScheduler`` or
``celery-redbeat``.

Local smoke test::

    PORT=8080 python scripts/run_beat.py
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler convention
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, *_args, **_kwargs):
        return


def _serve_health() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)  # noqa: S104
    server.serve_forever()


def _run_celery_beat() -> int:
    # Eager import to surface task-registration errors in the container
    # log before Celery's own startup, matching run_worker.py.
    from core.tasks import celery_app  # noqa: F401, PLC0415

    from celery.__main__ import main as celery_main  # noqa: PLC0415

    sys.argv = [
        "celery",
        "-A",
        "core.tasks.celery_app",
        "beat",
        "--loglevel=info",
        # PersistentScheduler is the default — it keeps last-run state
        # in a local file. Across Cloud Run revision swaps the state
        # resets, but missed cron fires are not replayed (Celery beat
        # only schedules forward), so this is safe even for daily/
        # monthly tasks.
    ]
    return celery_main()


def main() -> int:
    threading.Thread(target=_serve_health, daemon=True).start()
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    return _run_celery_beat() or 0


if __name__ == "__main__":
    sys.exit(main())
