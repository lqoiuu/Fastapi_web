import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import FastAPI, Request, Response

from ticketing.core.context import (
    accepted_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from ticketing.core.errors import unhandled_exception_handler
from ticketing.core.observability import ObservabilityMetrics

logger = logging.getLogger(__name__)


def install_observability_middleware(app: FastAPI, metrics: ObservabilityMetrics) -> None:
    @app.middleware("http")
    async def observe_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation_id = accepted_correlation_id(request.headers.get("X-Correlation-ID"))
        token = set_correlation_id(correlation_id)
        started_at = perf_counter()
        status_code = 500
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                response = await unhandled_exception_handler(request, exc)
            status_code = response.status_code
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            duration_seconds = perf_counter() - started_at
            route_object = request.scope.get("route")
            route_value = getattr(route_object, "path", None)
            route = route_value if isinstance(route_value, str) else "unmatched"
            error_type = getattr(request.state, "error_type", None)
            metrics.observe_http_request(
                method=request.method,
                route=str(route),
                status_code=status_code,
                duration_seconds=duration_seconds,
                error_type=error_type if isinstance(error_type, str) else None,
            )
            log_method = logger.warning if status_code >= 500 else logger.info
            log_method(
                "HTTP request completed",
                extra={
                    "http_method": request.method,
                    "http_route": str(route),
                    "http_path": request.url.path,
                    "http_status": status_code,
                    "duration_ms": round(duration_seconds * 1000, 3),
                    "error_type": error_type,
                },
            )
            reset_correlation_id(token)
