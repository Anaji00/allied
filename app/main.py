"""FastAPI main application entrypoint, lifespan, CORS, and route mounting."""

import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_v1_router
from app.config import settings
from app.core.exceptions import (
    BridgeBaseException,
    ComplianceViolationError,
    EntityNotFoundError,
    )
from app.core.correlation import reset_correlation_id, set_correlation_id
from app.core.logging import logger
from app.services.entity_matcher import EntityMatcher


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager to initialize database, seed admin, and pre-warm caches."""
    logger.info("Initializing Allied Universal Bridge microservice...")

    # Fail before anything listens: default secrets make every token forgeable.
    settings.assert_production_secrets()

    # Initialize persistent database (SQLite WAL)
    from app.core.database import init_database
    init_database()
    logger.info("Database initialized (SQLite WAL mode)")

    # Seed admin account
    from app.core.security import seed_admin_user
    seed_admin_user()

    # Pre-warm entity matcher caches
    matcher = EntityMatcher()
    logger.info(
        f"Pre-warmed caches: {len(matcher.items_cache)} items, "
        f"{len(matcher.customers_cache)} customers, "
        f"{len(matcher.vendors_cache)} vendors, "
        f"{len(matcher.alias_dict)} aliases."
    )

    # Start watching the order mailbox. No-ops unless a mailbox is configured
    # and polling is switched on, so a default install is unchanged.
    from app.services.mailbox_poller import mailbox_poller
    mailbox_poller.start()

    from app.services.outbound_email_service import outbound_email_service
    logger.info(f"Outbound mail: {outbound_email_service.status()['detail']}")

    # Drains monday webhook retries and anything a restart interrupted.
    from app.services.monday_events import monday_inbox_worker
    monday_inbox_worker.start()

    # Reads the projects board, checks schedules, sends alerts and the
    # weekday summary. Every automation runs here; monday only holds data.
    from app.services.coordination.schedule import coordination_worker
    coordination_worker.start()

    yield

    coordination_worker.stop()
    monday_inbox_worker.stop()
    mailbox_poller.stop()
    logger.info("Shutting down Allied Universal Bridge microservice...")


app = FastAPI(
    title="Allied Welding Systems Universal Engine",
    description="High-performance bridge connecting Monday.com with on-premise Sage 50 US Accounting.",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS Configuration
# A wildcard origin combined with allow_credentials is rejected by browsers and
# would expose the API to any site once real tokens are in play, so the allowed
# origins are explicit and configurable.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Correlation-ID"],
    expose_headers=["X-Correlation-ID"],
)


# Body size limit: refuse an oversized request on its Content-Length, before any form
# parsing or login check reads it to disk.
@app.middleware("http")
async def body_size_limit(request: Request, call_next):
    limit = settings.MAX_REQUEST_MB * 1024 * 1024
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        return JSONResponse(status_code=413, content={"detail": f"Request is larger than {settings.MAX_REQUEST_MB} MB."})
    return await call_next(request)


# Correlation ID Middleware — injects X-Correlation-ID into every request/response
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex[:12]
    request.state.correlation_id = correlation_id
    # Published to a contextvar as well, so audit writes deep in the service
    # layer can pick it up without every function signature carrying it. The
    # audit_log table has had a correlation_id column all along that nothing
    # ever populated.
    token = set_correlation_id(correlation_id)
    try:
        response = await call_next(request)
    finally:
        reset_correlation_id(token)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# Global Exception Handlers
@app.exception_handler(EntityNotFoundError)
async def entity_not_found_handler(request: Request, exc: EntityNotFoundError) -> JSONResponse:
    logger.warning(f"Entity matching failure on {request.url.path}: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "EntityNotFound", "message": exc.message, "details": exc.details},
    )


@app.exception_handler(ComplianceViolationError)
async def compliance_error_handler(request: Request, exc: ComplianceViolationError) -> JSONResponse:
    logger.warning(f"Compliance violation on {request.url.path}: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "ComplianceViolation", "message": exc.message, "details": exc.details},
    )


@app.exception_handler(BridgeBaseException)
async def bridge_base_handler(request: Request, exc: BridgeBaseException) -> JSONResponse:
    logger.error(f"Bridge domain exception on {request.url.path}: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": exc.__class__.__name__, "message": exc.message, "details": exc.details},
    )


# Mount API Routers
app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/", tags=["Root"])
async def root():
    """Service landing endpoint with API links."""
    return {
        "service": settings.APP_NAME,
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
        "auth": "/api/v1/auth/login",
        "status": "online",
        "facilities": list(settings.FACILITIES.keys()),
        "currencies": settings.SUPPORTED_CURRENCIES,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=settings.DEBUG)
