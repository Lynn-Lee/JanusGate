"""
JanusGate — FastAPI 应用入口。
策略驱动的 PAM / 零信任访问网关。
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api import (
    accounts,
    acl,
    admin,
    asset_tree,
    assets,
    auth,
    automation,
    connectors,
    notification_deliveries,
    notification_rules,
    rbac,
    protocols,
    session_recordings,
    sessions,
    ssh_certificate_authorities,
    ssh_certificates,
    webhook_endpoints,
)
from app.api.audits.routes import router as audits_router
from app.api.tenancy.routes import router as tenancy_router
from app.api.workflows.routes import router as workflows_router
from app.core.config import settings
from app.core.database import engine
from app.core.exceptions import API_ERROR_RESPONSES, register_exception_handlers
from app.observability.metrics import metrics_response, prometheus_metrics_middleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await engine.dispose()


app = FastAPI(
    title="JanusGate",
    description="策略驱动的 PAM / 零信任访问网关",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.APP_ENV == "development" else None,
    redoc_url="/redoc" if settings.APP_ENV == "development" else None,
    responses=API_ERROR_RESPONSES,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.middleware("http")(prometheus_metrics_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

register_exception_handlers(app)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(auth.users_router, prefix="/api/v1")
app.include_router(accounts.router, prefix="/api/v1")
app.include_router(acl.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(automation.router, prefix="/api/v1")
app.include_router(assets.router, prefix="/api/v1")
app.include_router(protocols.router, prefix="/api/v1")
app.include_router(asset_tree.router, prefix="/api/v1")
app.include_router(rbac.router, prefix="/api/v1")
app.include_router(connectors.router, prefix="/api/v1")
app.include_router(notification_deliveries.router, prefix="/api/v1")
app.include_router(notification_rules.router, prefix="/api/v1")
app.include_router(session_recordings.router, prefix="/api/v1")
app.include_router(ssh_certificate_authorities.router, prefix="/api/v1")
app.include_router(ssh_certificates.router, prefix="/api/v1")
app.include_router(webhook_endpoints.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(tenancy_router, prefix="/api/v1")
app.include_router(audits_router)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return metrics_response()
