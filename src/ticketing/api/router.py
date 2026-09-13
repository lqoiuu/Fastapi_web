import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.api.dependencies import get_cache, get_metrics
from ticketing.cache.backend import CacheBackend, CacheUnavailableError
from ticketing.core.config import Settings, get_settings
from ticketing.core.observability import PROMETHEUS_CONTENT_TYPE, ObservabilityMetrics
from ticketing.db.dependencies import get_database, get_session
from ticketing.db.session import DatabaseProtocol, DatabaseUnavailableError
from ticketing.schemas.health import CacheHealthResponse, HealthResponse, HealthStatus

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health/live", response_model=HealthResponse, tags=["health"])
async def liveness(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Report whether the application process can serve HTTP requests."""
    return HealthResponse(
        status=HealthStatus.OK,
        service=settings.app_name,
        environment=settings.environment,
    )


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Application not ready"}},
    tags=["health"],
)
async def readiness(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[DatabaseProtocol, Depends(get_database)],
) -> HealthResponse:
    """Report whether application startup has completed successfully."""
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "service_not_ready", "message": "Service is not ready"},
        )

    try:
        await database.ping()
    except DatabaseUnavailableError:
        logger.warning("Database readiness check failed")
        logger.debug("Database readiness failure details", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "database_unavailable", "message": "Database is unavailable"},
        ) from None

    return HealthResponse(
        status=HealthStatus.OK,
        service=settings.app_name,
        environment=settings.environment,
    )


@router.get("/health/cache", response_model=CacheHealthResponse, tags=["health"])
async def cache_health(
    cache: Annotated[CacheBackend, Depends(get_cache)],
) -> CacheHealthResponse:
    cache_status: Literal["ok", "degraded"] = "ok"
    try:
        await cache.ping()
    except CacheUnavailableError:
        cache_status = "degraded"
    metrics = cache.metrics_snapshot()
    return CacheHealthResponse(
        status=cache_status,
        hits=metrics.hits,
        misses=metrics.misses,
        writes=metrics.writes,
        invalidations=metrics.invalidations,
        failures=metrics.failures,
        rate_limited=metrics.rate_limited,
    )


@router.get("/metrics", include_in_schema=False, tags=["metrics"])
async def metrics(
    observer: Annotated[ObservabilityMetrics, Depends(get_metrics)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    await observer.refresh_background_job_metrics(session)
    return Response(content=observer.render(), media_type=PROMETHEUS_CONTENT_TYPE)


api_router = router
