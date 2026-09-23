# SPDX-License-Identifier: Apache-2.0
"""Serving the Prometheus registry to a collector that runs beside the process.

Every service in this deployment (the API, the Celery worker, the beat scheduler) increments
Prometheus instruments, and until now nothing read them: the counters lived and died inside a
container (FINDINGS A-56). This module is the read path.

**Why a second port and not a route.** The endpoint is served on ``METRICS_PORT``, bound to
loopback. A collector that runs in the same instance - a Cloud Run sidecar shares the network
namespace - reaches it over ``127.0.0.1``; nothing outside the instance can, whatever the platform
routes. So there is no external surface to authenticate and no credential to rotate or leak.
Serving it on the application port would mean an unauthenticated route (one careless entry in
the middleware's public-path list away from being public) or an IAM dance for a page that never
needs to leave the machine. Refusing to start when
``METRICS_PORT`` equals ``PORT`` keeps that property from being lost by a misconfiguration.

**Why per instance and not aggregated here.** Instances come and go with autoscaling. Each one
exports its own counters and the collector attaches the instance's identity; aggregation happens
in the query (``sum(rate(...))``), where a counter reset from an instance that went away is
handled per series. Nothing in this process tries to merge across instances.

**Multiprocess.** ``prometheus_client`` keeps its registry in process memory, so a value
incremented in a forked child is invisible to the parent that serves this endpoint. The Celery
worker runs the prefork pool, so it *must* run in multiprocess mode: with
``PROMETHEUS_MULTIPROC_DIR`` set, every process writes its samples to that directory and the
exporter merges them. The API runs one uvicorn process per container today and does not need it,
but the same switch is wired there so that adding ``--workers`` later cannot silently start
under-reporting instead.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client import REGISTRY as _DEFAULT_REGISTRY

logger = structlog.get_logger()

#: The port the in-instance collector scrapes. Never routed by Cloud Run.
DEFAULT_METRICS_PORT: Final = 9090

#: The path the collector is configured to scrape.
METRICS_PATH: Final = "/metrics"

_server_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None


class MetricsExportError(RuntimeError):
    """The endpoint cannot be served safely, so it is not served at all."""


def multiprocess_dir() -> str:
    """The directory sibling processes write their samples to, or ``""`` for single-process mode."""
    return os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip()


def enable_multiprocess(directory: str = "", *, allow_existing: bool = False) -> str:
    """Point this process, and the children it forks, at a shared samples directory.

    Call it *before* creating any instrument. ``prometheus_client`` chooses its value class when
    it is first imported, from this environment variable, and an instrument created under the
    in-process class keeps writing to process memory whatever is set afterwards. If the module is
    already imported by the time we get here - one import high up in a module chain is enough -
    the class is reselected.

    Reselecting is not a full repair, and the difference is worth knowing. A *labelled*
    instrument creates each child's value lazily on ``.labels()``, so it picks up the new class
    and aggregates correctly. An *unlabelled* one binds its value when it is constructed: it
    keeps the old class for ever, reads zero in the merged registry, and a bare gauge disappears
    from the exposition entirely - silently, which is the failure mode this module exists to
    avoid. ``agenticorg_case_push_dead_letter_backlog`` is such a gauge, and an alert reads it.

    So when instruments already exist, this says so loudly: it raises outside production, where a
    test or a developer run should fail on it, and logs an error in production, where refusing to
    start a worker over a metrics problem would be the worse trade. ``allow_existing`` is for the
    one caller that knows better - the probe, which creates its own instruments afterwards.
    """
    directory = directory or os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip()
    if not directory:
        directory = os.path.join(tempfile.gettempdir(), "agenticorg-metrics")
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = directory
    os.makedirs(directory, exist_ok=True)
    values = sys.modules.get("prometheus_client.values")
    if values is not None:
        if not allow_existing:
            _refuse_late_switch(_instruments_already_registered())
        values.ValueClass = values.MultiProcessValue()
    return directory


def _instruments_already_registered() -> list[str]:
    """Names of instruments *this codebase* built before multiprocess mode was switched on.

    ``prometheus_client`` registers its own process, platform and GC collectors on import. They
    are collectors rather than value-backed instruments, so they are not at risk and are not
    counted; if they were, this check would fire every single time and mean nothing.
    """
    builtin = {
        "prometheus_client.process_collector",
        "prometheus_client.platform_collector",
        "prometheus_client.gc_collector",
    }
    collectors = getattr(_DEFAULT_REGISTRY, "_collector_to_names", {})
    return sorted(
        {
            name
            for collector, names in collectors.items()
            if type(collector).__module__ not in builtin
            for name in names
        }
    )


def _refuse_late_switch(existing: list[str]) -> None:
    from core.config import settings

    if not existing:
        return
    detail = (
        "multiprocess metrics were switched on after instruments were created: an unlabelled "
        "instrument keeps the value class it was built with, so it will read zero in the merged "
        f"registry or vanish from the exposition. Built too early: {', '.join(existing[:10])}"
        f"{'...' if len(existing) > 10 else ''}. Call enable_multiprocess() before importing the "
        "modules that define instruments."
    )
    if str(getattr(settings, "env", "")).lower() in ("production", "prod", "staging"):
        # A worker that refuses to start over a metrics problem is worse than one that reports
        # some of its metrics, so production gets the message and keeps running.
        logger.error("metrics_multiprocess_enabled_late", detail=detail)
        return
    raise MetricsExportError(detail)


def registry() -> CollectorRegistry:
    """The registry to export: merged across processes when multiprocess mode is on."""
    directory = multiprocess_dir()
    if not directory:
        return _DEFAULT_REGISTRY
    from prometheus_client import multiprocess

    merged = CollectorRegistry()
    multiprocess.MultiProcessCollector(merged, path=directory)
    return merged


def mark_process_dead(pid: int) -> None:
    """Drop a forked child's gauges when it exits, so a dead worker stops being reported.

    Counters and histograms survive the child on purpose - the work it did still happened.
    """
    if not multiprocess_dir():
        return
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(pid)


def payload() -> tuple[bytes, str]:
    """The exposition text and its content type."""
    return generate_latest(registry()), CONTENT_TYPE_LATEST


def metrics_port(*, serving_port: int | None = None) -> int:
    """The port to serve on, refusing the one configuration that would expose it.

    ``serving_port`` is the port the service's own HTTP listener uses (``$PORT`` on Cloud Run).
    Sharing it would put the registry behind the routed, internet-facing listener, which is
    exactly what this module exists to avoid, so it is refused rather than quietly re-used.
    """
    raw = os.environ.get("METRICS_PORT", "").strip()
    port = int(raw) if raw else DEFAULT_METRICS_PORT
    if port <= 0:
        raise MetricsExportError(f"METRICS_PORT must be a port number, got {port!r}")
    routed = serving_port if serving_port is not None else _serving_port()
    if routed is not None and port == routed:
        raise MetricsExportError(
            f"METRICS_PORT ({port}) is the port this service serves traffic on: the metrics "
            "endpoint would be routed from outside the instance. Give it a port of its own."
        )
    return port


def _serving_port() -> int | None:
    raw = os.environ.get("PORT", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


class _Handler(BaseHTTPRequestHandler):
    """Serves the registry, and nothing else, to whatever is inside this instance."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's convention
        if self.path.split("?")[0] != METRICS_PATH:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body, content_type = payload()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        # The collector scrapes every few seconds; an access log line each time is noise.
        return


def start_metrics_server(*, serving_port: int | None = None) -> int | None:
    """Serve the registry on the metrics port in a background thread. Returns the port, or None.

    Never raises into a caller's startup path: an export that cannot start must not stop a service
    from serving traffic. It logs loudly instead, and the missing series is itself visible as the
    absence of the ``up`` metric for that instance.
    """
    global _server
    with _server_lock:
        if _server is not None:
            return _server.server_address[1]
        try:
            port = metrics_port(serving_port=serving_port)
            # Loopback, not every interface. A Cloud Run sidecar shares the instance's network
            # namespace, so the collector still reaches this over 127.0.0.1, and the endpoint then
            # has no external surface under compose, on GKE or on a plain VM either - rather than
            # depending entirely on Cloud Run routing only $PORT.
            server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        except (MetricsExportError, OSError, ValueError) as exc:
            logger.error("metrics_export_not_started", error=str(exc))
            return None
        thread = threading.Thread(target=server.serve_forever, name="metrics-export", daemon=True)
        thread.start()
        _server = server
        logger.info("metrics_export_started", port=port, path=METRICS_PATH, multiprocess=bool(multiprocess_dir()))
        return port


def stop_metrics_server() -> None:
    """Stop the exporter. Used by tests; a container exit does not need it."""
    global _server
    with _server_lock:
        if _server is None:
            return
        _server.shutdown()
        _server.server_close()
        _server = None
