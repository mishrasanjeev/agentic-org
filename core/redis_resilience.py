# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Opt-in Redis connection resilience: TCP keepalive and idle health checks.

Production reaches Memorystore through a Serverless VPC Access connector, and
idle pooled sockets are occasionally dropped on that path: the next command on
such a socket fails with ``ConnectionError`` (``Connection reset by peer``),
and the Celery broker can wait up to ``brpop_timeout + 2 * health_check_interval``
seconds before it notices. Keepalive probes keep idle connections from looking
idle, and a client ``health_check_interval`` makes redis-py PING a connection
that has been idle longer than the interval before reusing it.

Everything here is off by default. The API and other in-process clients read the
values through ``core.config.Settings`` (``redis_connection_resilience_kwargs``);
the Celery app is configured at import time from the same environment variables,
like its broker URL, so it parses them here without importing the settings.

This module stays free of heavy imports so ``core.tasks.celery_app`` can use it.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from typing import Any

KEEPALIVE_ENV = "AGENTICORG_REDIS_SOCKET_KEEPALIVE"
HEALTH_CHECK_INTERVAL_ENV = "AGENTICORG_REDIS_HEALTH_CHECK_INTERVAL_SECONDS"
PROBE_RETRY_ENV = "AGENTICORG_REDIS_HEALTH_PROBE_RETRY"

# Upper bound for the idle health-check interval, in seconds.
MAX_HEALTH_CHECK_INTERVAL_SECONDS = 300

# With keepalive on, an idle socket sends its first probe after 30 s, then every
# 10 s, and is declared dead after 3 unanswered probes. The OS defaults (first
# probe after 2 hours on Linux) would not keep an idle flow alive on the
# connector path.
_KEEPALIVE_SETTINGS = (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3))

# The same spellings pydantic accepts for a bool setting, so the Celery app and
# ``Settings`` agree on every value.
_TRUE = frozenset({"1", "on", "t", "true", "y", "yes"})
_FALSE = frozenset({"", "0", "off", "f", "false", "n", "no"})


def keepalive_options() -> dict[int, int]:
    """TCP keepalive timings for redis-py, limited to what this platform supports."""
    return {getattr(socket, name): value for name, value in _KEEPALIVE_SETTINGS if hasattr(socket, name)}


def connection_kwargs(*, keepalive: bool, health_check_interval: int) -> dict[str, Any]:
    """Extra redis-py client kwargs; empty when both options are off."""
    kwargs: dict[str, Any] = {}
    if keepalive:
        kwargs["socket_keepalive"] = True
        options = keepalive_options()
        if options:
            kwargs["socket_keepalive_options"] = options
    if health_check_interval > 0:
        kwargs["health_check_interval"] = health_check_interval
    return kwargs


def _env_bool(environ: Mapping[str, str], name: str) -> bool:
    raw = environ.get(name, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean (true/false), got {raw!r}")


def _env_interval(environ: Mapping[str, str], name: str) -> int:
    raw = environ.get(name, "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a whole number of seconds, got {raw!r}") from exc
    if not 0 <= value <= MAX_HEALTH_CHECK_INTERVAL_SECONDS:
        raise ValueError(f"{name} must be between 0 and {MAX_HEALTH_CHECK_INTERVAL_SECONDS}, got {value}")
    return value


def celery_redis_options(environ: Mapping[str, str]) -> dict[str, Any]:
    """Celery configuration for the Redis broker and result backend.

    Returns ``{}`` when the options are off, so Celery and kombu keep their own
    defaults (kombu already health-checks broker connections every 25 s). When
    keepalive is on and no interval is set, the broker keeps kombu's interval.
    """
    keepalive = _env_bool(environ, KEEPALIVE_ENV)
    interval = _env_interval(environ, HEALTH_CHECK_INTERVAL_ENV)
    if not keepalive and interval == 0:
        return {}
    options: dict[str, Any] = {
        "broker_transport_options": connection_kwargs(keepalive=keepalive, health_check_interval=interval),
    }
    if keepalive:
        options["redis_socket_keepalive"] = True
    if interval:
        options["redis_backend_health_check_interval"] = interval
    return options
