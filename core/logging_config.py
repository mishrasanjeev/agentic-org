"""Process-wide structured logging (bug sheet 2026-09-14 #8).

Before this module existed nothing called ``structlog.configure`` or
``logging.dictConfig``: structlog fell back to its development
``ConsoleRenderer`` (multi-line tracebacks) and stdlib loggers (uvicorn,
SQLAlchemy, Celery) each rendered their own format, so container log
collectors saw multi-line records with no request correlation.

``configure_logging()`` routes structlog through stdlib ``logging`` with a
``ProcessorFormatter`` so every record from either API renders through the
same chain: contextvars (``request_id`` bound by
``api.middleware.request_id`` / the Celery signals in
``core.tasks.celery_app``) + level + ISO timestamp + exception text, then a
single-line JSON object (or the console renderer for ``env=test``).

Call it once per process: ``api/main.py`` at import, and the Celery
``setup_logging`` signal (Celery hijacks the root logger otherwise).
"""

from __future__ import annotations

import logging
import sys

import structlog

from core.config import normalize_env, settings

LOG_FORMATS = ("json", "console")
_CONSOLE_ENVS = frozenset({"test"})
_configured_format: str | None = None
_installed_handler: logging.Handler | None = None


def resolve_log_format() -> str:
    """Return the effective renderer: explicit setting wins, else env default."""
    explicit = "log_format" in settings.model_fields_set
    requested = str(settings.log_format or "").strip().lower()
    if not explicit and normalize_env(settings.env) in _CONSOLE_ENVS:
        return "console"
    return requested if requested in LOG_FORMATS else "json"


def _shared_processors() -> list[structlog.typing.Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]


_CLOUD_SEVERITY = {
    "debug": "DEBUG",
    "info": "INFO",
    "warning": "WARNING",
    "warn": "WARNING",
    "error": "ERROR",
    "exception": "ERROR",
    "critical": "CRITICAL",
}


def add_cloud_severity(_logger: object, _method: str, event_dict: dict) -> dict:
    """Mirror ``level`` into ``severity`` for Google Cloud Logging.

    Cloud Run parses single-line JSON stdout into ``jsonPayload`` but only
    classifies entries by a top-level ``severity`` field. Without it every
    ERROR lands as DEFAULT and severity-based filters/alerts never match.
    """
    level = str(event_dict.get("level", "")).lower()
    event_dict.setdefault("severity", _CLOUD_SEVERITY.get(level, "DEFAULT"))
    return event_dict


def build_formatter(log_format: str) -> logging.Formatter:
    """Build the stdlib formatter used for structlog and foreign records alike."""
    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer(colors=False)
        if log_format == "console"
        else structlog.processors.JSONRenderer(sort_keys=True)
    )
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            # Exceptions become a single ``exception`` string field so a
            # traceback never spans multiple log records/lines.
            structlog.processors.format_exc_info,
            *([add_cloud_severity] if log_format == "json" else []),
            renderer,
        ],
    )


def _resolve_level() -> int:
    level = logging.getLevelName(str(settings.log_level or "INFO").upper())
    return level if isinstance(level, int) else logging.INFO


def configure_logging() -> str:
    """Configure structlog + stdlib logging once; returns the format in use."""
    global _configured_format, _installed_handler
    log_format = resolve_log_format()
    if _configured_format == log_format:
        return log_format

    structlog.configure(
        processors=[
            *_shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Must stay False: ``structlog.testing.capture_logs`` swaps the
        # processor chain at runtime and cached loggers would ignore it.
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(build_formatter(log_format))
    root = logging.getLogger()
    # Only replace the handler this module installed: pytest's caplog and
    # report handlers also live on the root logger and must survive.
    if _installed_handler is not None:
        root.removeHandler(_installed_handler)
    root.addHandler(handler)
    root.setLevel(_resolve_level())
    _installed_handler = handler

    # uvicorn installs its own handlers with propagate=False before the app
    # module is imported; fold them into the shared root handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True

    _configured_format = log_format
    return log_format
