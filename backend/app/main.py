from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.api import (
    auth,
    kitchen,
    media,
    menu,
    orders,
    pin,
    problem_reports,
    public,
    restaurants,
    staff,
    tables,
    ws,
)
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.mailer import build_mailer
from app.core.media import MediaStorage
from app.core.pin import PinCrypto
from app.core.rate_limit import build_rate_limiter
from app.core.security import passwords
from app.core.usage import UsageCounters
from app.db.session import Database
from app.middleware.http import (
    BodySizeLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from app.realtime.bus import build_event_bus
from app.services import pin_service, retention_service, usage_service

logger = logging.getLogger("app")


async def verify_runtime_db_role(db: Database, settings: Settings) -> None:
    """RLS is only a wall if the runtime role cannot bypass it. Refuse to run otherwise."""
    async with db.engine.connect() as conn:
        role = (
            await conn.execute(
                text(
                    "SELECT r.rolsuper, r.rolbypassrls, "
                    "  (SELECT count(*) FROM pg_tables t "
                    "   WHERE t.schemaname = 'public' AND t.tableowner = current_user) AS owned "
                    "FROM pg_roles r WHERE r.rolname = current_user"
                )
            )
        ).one()
    problems = []
    if role.rolsuper:
        problems.append("is a superuser")
    if role.rolbypassrls:
        problems.append("has BYPASSRLS")
    if role.owned:
        problems.append("owns application tables")
    if problems:
        message = "runtime database role " + ", ".join(problems) + " — RLS would not apply"
        if settings.is_production:
            raise RuntimeError(message)
        logger.warning(message)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        passwords.configure(settings)
        app.state.db = Database(settings)
        app.state.rate_limiter = build_rate_limiter(settings)
        app.state.mailer = build_mailer(settings)
        app.state.bus = build_event_bus(settings)
        app.state.ws_connections = {}
        app.state.usage = UsageCounters()
        await app.state.bus.start()
        await verify_runtime_db_role(app.state.db, settings)
        background: list[asyncio.Task[None]] = []
        if settings.pin_scheduler_enabled:
            background.append(
                asyncio.create_task(
                    pin_service.run_scheduler(
                        app.state.db,
                        app.state.pin_crypto,
                        settings.pin_scheduler_interval_seconds,
                        app.state.bus,
                    )
                )
            )
        if settings.retention_enabled:
            background.append(
                asyncio.create_task(retention_service.run_retention(app.state.db, settings))
            )
        if settings.usage_counters_enabled:
            background.append(
                asyncio.create_task(
                    usage_service.run_flusher(
                        app.state.db, app.state.usage, settings.usage_flush_seconds
                    )
                )
            )
        try:
            yield
        finally:
            for task in background:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if settings.usage_counters_enabled:
                await usage_service.flush(app.state.db, app.state.usage)  # the last minute's
            await app.state.bus.close()
            await app.state.rate_limiter.close()
            await app.state.db.dispose()

    docs_enabled = not settings.is_production
    app = FastAPI(
        title="Restaurant Ordering API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings
    app.state.pin_crypto = PinCrypto(settings)
    app.state.media = MediaStorage(settings.media_root)
    install_error_handlers(app)

    # add_middleware prepends: the last added is the outermost.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
            max_age=600,
        )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_body_bytes,
        # Image uploads are the only large bodies (multipart overhead included).
        overrides=(
            (
                re.compile(r"/api/v1/menu/items/[^/]+/image"),
                settings.max_image_upload_bytes + 65_536,
            ),
        ),
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=settings.is_production,
        docs_paths=frozenset({"/docs", "/docs/oauth2-redirect"}) if docs_enabled else frozenset(),
    )
    app.add_middleware(RequestIdMiddleware)

    api = APIRouter(prefix="/api/v1")
    for module in (
        auth,
        restaurants,
        staff,
        tables,
        pin,
        menu,
        orders,
        kitchen,
        problem_reports,
        public,
        ws,
    ):
        api.include_router(module.router)
    app.include_router(api)
    app.include_router(media.build_router(settings.media_url_prefix))

    if docs_enabled:

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            return RedirectResponse("/docs")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> dict[str, str]:
        async with app.state.db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app
