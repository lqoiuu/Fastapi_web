from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ticketing.api.middleware import install_observability_middleware
from ticketing.api.router import api_router
from ticketing.api.v1.router import router as v1_router
from ticketing.cache.backend import CacheBackend
from ticketing.cache.redis import RedisCache
from ticketing.core.config import Settings, get_settings
from ticketing.core.errors import install_exception_handlers
from ticketing.core.logging import configure_logging
from ticketing.core.observability import ObservabilityMetrics
from ticketing.db.session import Database, DatabaseProtocol


def create_app(
    settings: Settings | None = None,
    database: DatabaseProtocol | None = None,
    cache: CacheBackend | None = None,
) -> FastAPI:
    """Create a configured FastAPI application instance."""
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    app_metrics = ObservabilityMetrics()
    app_database = database or Database.from_settings(app_settings, app_metrics)
    app_cache = cache or RedisCache.from_settings(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            try:
                await app_cache.close()
            finally:
                await app_database.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.database = app_database
    app.state.cache = app_cache
    app.state.metrics = app_metrics
    install_exception_handlers(app)
    install_observability_middleware(app, app_metrics)
    app.include_router(api_router)
    app.include_router(v1_router, prefix=app_settings.api_v1_prefix)
    return app


app = create_app()
