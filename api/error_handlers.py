"""Global error handlers mapping to E-series error envelope."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def register_error_handlers(app: FastAPI):
    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"error": {
            "code": "E2001", "name": "VALIDATION_ERROR", "message": str(exc),
            "severity": "error", "retryable": False, "timestamp": datetime.now(UTC).isoformat(),
        }})

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return JSONResponse(status_code=404, content={"error": {
            "code": "E1005", "name": "NOT_FOUND", "message": "Resource not found",
            "severity": "error", "retryable": False, "timestamp": datetime.now(UTC).isoformat(),
        }})

    @app.exception_handler(500)
    async def server_error(request: Request, exc):
        return JSONResponse(status_code=500, content={"error": {
            "code": "E1001", "name": "INTERNAL_ERROR", "message": "Internal server error",
            "severity": "error", "retryable": True, "timestamp": datetime.now(UTC).isoformat(),
        }})
