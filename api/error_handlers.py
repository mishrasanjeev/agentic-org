"""Global error handlers mapping to E-series error envelope."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI):
    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        # Uncaught ValueErrors may carry internal state (paths, SQL, connector
        # payloads). Log the detail server-side; return a fixed client message.
        logger.warning(
            "Unhandled ValueError on %s %s: %s",
            getattr(request, "method", "?"),
            getattr(getattr(request, "url", None), "path", "?"),
            exc,
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "E2001",
                    "name": "VALIDATION_ERROR",
                    "message": "Invalid request",
                    "severity": "error",
                    "retryable": False,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        # Route handlers that raise HTTPException(404, detail=...) own their
        # body (e.g. the MCP unknown-tool contract in api/v1/mcp.py). Only the
        # framework's bare "no route matched" 404 gets the generic envelope.
        if isinstance(exc, (HTTPException, StarletteHTTPException)) and exc.detail not in (None, "", "Not Found"):
            return JSONResponse(
                status_code=404,
                content={"detail": exc.detail},
                headers=getattr(exc, "headers", None),
            )
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "E1005",
                    "name": "NOT_FOUND",
                    "message": "Resource not found",
                    "severity": "error",
                    "retryable": False,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )

    @app.exception_handler(500)
    async def server_error(request: Request, exc):
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "E1001",
                    "name": "INTERNAL_ERROR",
                    "message": "Internal server error",
                    "severity": "error",
                    "retryable": True,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )
