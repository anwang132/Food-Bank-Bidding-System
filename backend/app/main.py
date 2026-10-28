"""Application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.api import admin, auth, chat, planning, rounds
from app.api.middleware import RequestContextMiddleware
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.errors import AppError
from app.logs import configure_logging, request_id_var
from app.realtime import ws
from app.realtime.bus import Bus
from app.realtime.hub import Hub

log = logging.getLogger("app")
BACKEND_DIR = Path(__file__).resolve().parent.parent


def migration_head() -> str | None:
    script = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))
    return script.get_current_head()


def _error_body(status: int, code: str, message: str, details: object = None) -> JSONResponse:
    error: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": request_id_var.get(),
    }
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status)


def create_app(settings: Settings | None = None, *, start_subscriber: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.instance_id)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings)
        hub = Hub(settings.ws_queue_size)
        bus = Bus(
            settings.redis_url, hub, settings.instance_id, settings.redis_publish_timeout_seconds
        )
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.hub = hub
        app.state.bus = bus
        app.state.migration_head = migration_head()
        subscriber = asyncio.create_task(bus.run_subscriber()) if start_subscriber else None
        log.info(
            "startup",
            extra={"environment": settings.environment, "origins": settings.allowed_origins},
        )
        try:
            yield
        finally:
            await bus.close()
            if subscriber:
                subscriber.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await subscriber
            await engine.dispose()

    app = FastAPI(
        title="Food Bank Allocation",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.environment != "production" else None,
        openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    )
    app.add_middleware(
        RequestContextMiddleware,
        allowed_origins=settings.allowed_origins,
        max_body_bytes=settings.max_body_bytes,
        max_upload_bytes=settings.max_upload_bytes,
        ip_rate_limit_per_minute=settings.ip_rate_limit_per_minute,
        instance_id=settings.instance_id,
    )

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        response = _error_body(exc.status_code, exc.code, exc.message, exc.details)
        for k, v in (exc.headers or {}).items():
            response.headers[k] = v
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
                "message": e["msg"],
            }
            for e in exc.errors()
        ]
        return _error_body(422, "VALIDATION_FAILED", "Some fields are invalid", details)

    @app.exception_handler(OperationalError)
    @app.exception_handler(DBAPIError)
    async def db_error(_: Request, exc: Exception) -> JSONResponse:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate in ("55P03", "40P01", "40001"):  # lock timeout, deadlock, serialization
            log.warning("database contention", extra={"sqlstate": sqlstate})
            return _error_body(503, "BUSY", "The system is busy. Please retry.")
        log.exception("database error", extra={"sqlstate": sqlstate})
        return _error_body(503, "DATABASE_UNAVAILABLE", "The database is unavailable")

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error_body(500, "INTERNAL_ERROR", "Something went wrong")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "instance": settings.instance_id}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> JSONResponse:
        """Ready iff Postgres is reachable and migrated to head. Redis problems are
        reported as degraded realtime but do not fail readiness (DECISIONS D9)."""
        body: dict[str, object] = {"instance": settings.instance_id}
        ready = True
        try:
            async with request.app.state.engine.connect() as conn:
                current = await conn.scalar(text("SELECT version_num FROM alembic_version"))
            body["database"] = "ok"
            if current != request.app.state.migration_head:
                ready = False
                body["migrations"] = {
                    "current": current,
                    "expected": request.app.state.migration_head,
                }
        except Exception as exc:
            ready = False
            body["database"] = f"unavailable: {type(exc).__name__}"
        bus: Bus = request.app.state.bus
        redis_ok = await bus.ping()
        body["redis"] = "ok" if redis_ok and bus.stats.subscriber_connected else "degraded"
        body["realtime"] = bus.stats.as_dict() | {
            "connections": len(request.app.state.hub.connections)
        }
        body["status"] = "ready" if ready else "not_ready"
        return JSONResponse(body, status_code=200 if ready else 503)

    app.include_router(auth.router)
    app.include_router(rounds.router)
    app.include_router(admin.router)
    app.include_router(chat.router)
    app.include_router(planning.router)
    app.include_router(ws.router)
    return app
