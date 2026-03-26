"""Mock server for all 42 connectors — catches all HTTP requests and returns realistic JSON."""

from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="AgentFlow Connector Mock Server")


def _build_response(path: str, body: dict) -> dict:
    """Build a realistic mock response based on the URL path verb."""
    parts = [p for p in path.strip("/").split("/") if p]
    verb = parts[0] if parts else "get"

    # Create/Send/Push/File verbs
    if verb in ("create", "send", "push", "file", "post", "initiate", "trigger", "schedule"):
        return {
            "status": "created",
            "id": f"mock-{verb}-{int(time.time())}",
            "message": f"{verb} completed successfully",
            "timestamp": time.time(),
            **{k: v for k, v in body.items() if k != "grant_type"},
        }

    # Get/Fetch/List/Check/Download verbs
    if verb in ("get", "fetch", "list", "check", "download", "search", "find", "query"):
        return {
            "status": "success",
            "data": {"id": "mock-001", "name": "Mock Data", **body},
            "count": 1,
            "timestamp": time.time(),
        }

    # Run/Generate/Export verbs
    if verb in ("run", "generate", "export", "analyze", "evaluate", "compare"):
        return {
            "status": "completed",
            "report_id": f"mock-report-{int(time.time())}",
            "data": [{"metric": "value", "count": 42}],
            "timestamp": time.time(),
        }

    # Update/Manage/Assign/Transition verbs
    if verb in ("update", "manage", "assign", "transition", "transfer", "move", "approve"):
        return {
            "status": "updated",
            "id": f"mock-{int(time.time())}",
            "message": f"{verb} completed",
            "timestamp": time.time(),
        }

    # Screen/Validate/Verify verbs
    if verb in ("screen", "validate", "verify"):
        return {
            "status": "clear",
            "match_count": 0,
            "risk_level": "low",
            "timestamp": time.time(),
        }

    # Delete/Remove/Cancel/Void/Reject verbs
    if verb in ("delete", "remove", "cancel", "void", "reject", "deactivate", "suspend", "terminate"):
        return {
            "status": "deleted",
            "id": f"mock-{int(time.time())}",
            "message": f"{verb} completed",
        }

    # Acknowledge/Reset/Add/Enrol/Define and other action verbs
    action_verbs = (
        "acknowledge", "reset", "add", "enrol", "define", "set", "merge",
        "escalate", "fulfil", "submit", "complete", "pay", "record", "sync",
        "reconcile", "provision", "book", "pause", "reallocate", "adjust",
        "identify", "copy",
    )
    if verb in action_verbs:
        return {
            "status": "success",
            "id": f"mock-{int(time.time())}",
            "message": f"{verb} completed",
        }

    # Fallback
    return {
        "status": "ok",
        "path": path,
        "mock": True,
        "timestamp": time.time(),
    }


@app.post("/{path:path}")
async def catch_all_post(path: str, request: Request):
    """Handle all POST requests — OAuth2 tokens + tool calls."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Error simulation
    if path.startswith("error/500"):
        return JSONResponse({"error": "Internal Server Error"}, status_code=500)
    if path.startswith("error/429"):
        return JSONResponse(
            {"error": "Rate Limited"}, status_code=429,
            headers={"Retry-After": "60"},
        )
    if path.startswith("error/timeout"):
        await asyncio.sleep(30)
        return JSONResponse({"error": "Slow response"})

    # OAuth2 token exchange
    if "grant_type" in body or "oauth2/token" in path or "token" in path.split("/")[-1:]:
        return JSONResponse({
            "access_token": "mock-access-token-harness",
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "mock-refresh-token",
        })

    return JSONResponse(_build_response(path, body))


@app.get("/{path:path}")
async def catch_all_get(path: str):
    """Handle all GET requests — health checks."""
    if path.startswith("error/500"):
        return JSONResponse({"error": "Internal Server Error"}, status_code=500)
    return JSONResponse({"status": "ok", "mock": True})


@app.put("/{path:path}")
async def catch_all_put(path: str, request: Request):
    """Handle PUT requests."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    return JSONResponse({"status": "updated", "mock": True, **body})


@app.delete("/{path:path}")
async def catch_all_delete(path: str):
    """Handle DELETE requests."""
    return JSONResponse({"status": "deleted", "mock": True})
