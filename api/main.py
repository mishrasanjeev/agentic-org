"""FastAPI application — AgenticOrg."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.error_handlers import register_error_handlers
from api.v1 import (
    agent_teams,
    agents,
    approvals,
    audit,
    compliance,
    config,
    connectors,
    evals,
    health,
    prompt_templates,
    sales,
    schemas,
    workflows,
)
from api.v1 import (
    auth as v1_auth,
)
from api.v1 import demo as v1_demo
from api.v1 import org as v1_org
from api.websocket.feed import router as ws_feed_router
from auth.middleware import AuthMiddleware
from core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core.database import init_db

    await init_db()
    yield
    from core.database import close_db

    await close_db()


app = FastAPI(
    title="AgenticOrg",
    description="Enterprise Agent Swarm Platform — 24 agents, 42 connectors",
    version="2.1.0",
    lifespan=lifespan,
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
app.add_middleware(AuthMiddleware)

register_error_handlers(app)

app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(v1_auth.router, prefix="/api/v1", tags=["Auth"])
app.include_router(agents.router, prefix="/api/v1", tags=["Agents"])
app.include_router(prompt_templates.router, prefix="/api/v1", tags=["Prompt Templates"])
app.include_router(sales.router, prefix="/api/v1", tags=["Sales"])
app.include_router(agent_teams.router, prefix="/api/v1", tags=["Agent Teams"])
app.include_router(workflows.router, prefix="/api/v1", tags=["Workflows"])
app.include_router(approvals.router, prefix="/api/v1", tags=["Approvals"])
app.include_router(audit.router, prefix="/api/v1", tags=["Audit"])
app.include_router(schemas.router, prefix="/api/v1", tags=["Schemas"])
app.include_router(connectors.router, prefix="/api/v1", tags=["Connectors"])
app.include_router(compliance.router, prefix="/api/v1", tags=["Compliance"])
app.include_router(config.router, prefix="/api/v1", tags=["Config"])
app.include_router(v1_demo.router, prefix="/api/v1", tags=["Demo"])
app.include_router(v1_org.router, prefix="/api/v1", tags=["Organization"])
app.include_router(evals.router, prefix="/api/v1", tags=["Evals"])
app.include_router(ws_feed_router, prefix="/api/v1", tags=["WebSocket"])
