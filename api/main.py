"""FastAPI application — AgenticOrg."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.error_handlers import register_error_handlers
from api.v1 import (
    a2a,
    agent_teams,
    agents,
    api_keys,
    approvals,
    audit,
    compliance,
    config,
    connectors,
    evals,
    health,
    mcp,
    prompt_templates,
    sales,
    schemas,
    sop,
    workflows,
)
from api.v1 import (
    auth as v1_auth,
)
from api.v1 import demo as v1_demo
from api.v1 import org as v1_org
from api.websocket.feed import router as ws_feed_router
from auth.grantex_middleware import GrantexAuthMiddleware
from core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core.database import init_db

    await init_db()

    # One-time cleanup: remove poisoned blacklist keys created by the old
    # token[:32] scheme.  All HS256 JWTs share the same header prefix, so a
    # single logout previously blocked every HS256 token.
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        cursor, keys = await r.scan(match="token_blacklist:*", count=500)
        if keys:
            await r.delete(*keys)
        while cursor:
            cursor, batch = await r.scan(cursor=cursor, match="token_blacklist:*", count=500)
            if batch:
                await r.delete(*batch)
        await r.aclose()
    except Exception as exc:
        import logging

        logging.getLogger(__name__).debug("Blacklist cleanup skipped: %s", exc)

    yield
    from core.database import close_db

    await close_db()


_is_production = settings.env == "production"

app = FastAPI(
    title="AgenticOrg",
    description="AI Virtual Employee Platform — 25 agents that reason AND act, 42 connectors (269 tools)",
    version="2.2.0",
    lifespan=lifespan,
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
)

# CORS: open in dev, restricted in production
_cors_origins = (
    ["*"]
    if settings.env == "development"
    else [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    if settings.cors_allowed_origins
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GrantexAuthMiddleware)

register_error_handlers(app)

app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(v1_auth.router, prefix="/api/v1", tags=["Auth"])
app.include_router(agents.router, prefix="/api/v1", tags=["Agents"])
app.include_router(prompt_templates.router, prefix="/api/v1", tags=["Prompt Templates"])
app.include_router(sales.router, prefix="/api/v1", tags=["Sales"])
app.include_router(agent_teams.router, prefix="/api/v1", tags=["Agent Teams"])
app.include_router(workflows.router, prefix="/api/v1", tags=["Workflows"])
app.include_router(sop.router, prefix="/api/v1", tags=["SOP"])
app.include_router(a2a.router, prefix="/api/v1", tags=["A2A"])
app.include_router(mcp.router, prefix="/api/v1", tags=["MCP"])
app.include_router(approvals.router, prefix="/api/v1", tags=["Approvals"])
app.include_router(audit.router, prefix="/api/v1", tags=["Audit"])
app.include_router(schemas.router, prefix="/api/v1", tags=["Schemas"])
app.include_router(connectors.router, prefix="/api/v1", tags=["Connectors"])
app.include_router(compliance.router, prefix="/api/v1", tags=["Compliance"])
app.include_router(config.router, prefix="/api/v1", tags=["Config"])
app.include_router(v1_demo.router, prefix="/api/v1", tags=["Demo"])
app.include_router(v1_org.router, prefix="/api/v1", tags=["Organization"])
app.include_router(api_keys.router, prefix="/api/v1", tags=["API Keys"])
app.include_router(evals.router, prefix="/api/v1", tags=["Evals"])
app.include_router(ws_feed_router, prefix="/api/v1", tags=["WebSocket"])
