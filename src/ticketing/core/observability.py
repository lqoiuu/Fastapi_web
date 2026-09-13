from collections.abc import Mapping
from typing import Protocol

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.background_job import BackgroundJob, BackgroundJobKind, BackgroundJobStatus

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


class DatabaseMetricsRecorder(Protocol):
    def observe_database_query(self, operation: str, duration_seconds: float) -> None: ...


class ObservabilityMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self._http_requests = Counter(
            "ticketing_http_requests_total",
            "HTTP requests completed by the API process.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self._http_duration = Histogram(
            "ticketing_http_request_duration_seconds",
            "HTTP request duration observed by the API process.",
            ("method", "route"),
            registry=self.registry,
        )
        self._http_errors = Counter(
            "ticketing_http_errors_total",
            "HTTP error responses grouped by stable error type.",
            ("error_type", "status"),
            registry=self.registry,
        )
        self._database_duration = Histogram(
            "ticketing_database_query_duration_seconds",
            "SQLAlchemy query duration observed in this process.",
            ("operation",),
            registry=self.registry,
        )
        self._background_jobs = Gauge(
            "ticketing_background_jobs",
            "Durable background jobs currently recorded in PostgreSQL.",
            ("kind", "status"),
            registry=self.registry,
        )
        self._background_job_success_ratio = Gauge(
            "ticketing_background_job_success_ratio",
            "Succeeded jobs divided by terminal jobs, grouped by kind.",
            ("kind",),
            registry=self.registry,
        )

    def observe_http_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
        error_type: str | None,
    ) -> None:
        status_label = str(status_code)
        self._http_requests.labels(method, route, status_label).inc()
        self._http_duration.labels(method, route).observe(duration_seconds)
        if error_type is not None:
            self._http_errors.labels(error_type, status_label).inc()

    def observe_database_query(self, operation: str, duration_seconds: float) -> None:
        self._database_duration.labels(operation).observe(duration_seconds)

    async def refresh_background_job_metrics(self, session: AsyncSession) -> None:
        result = await session.execute(
            select(BackgroundJob.kind, BackgroundJob.status, func.count())
            .group_by(BackgroundJob.kind, BackgroundJob.status)
            .order_by(BackgroundJob.kind, BackgroundJob.status)
        )
        counts: Mapping[tuple[BackgroundJobKind, BackgroundJobStatus], int] = {
            (kind, status): int(count) for kind, status, count in result.all()
        }
        for kind in BackgroundJobKind:
            for status in BackgroundJobStatus:
                self._background_jobs.labels(kind.value, status.value).set(
                    counts.get((kind, status), 0)
                )
            succeeded = counts.get((kind, BackgroundJobStatus.SUCCEEDED), 0)
            failed = counts.get((kind, BackgroundJobStatus.FAILED), 0)
            terminal = succeeded + failed
            ratio = 0.0 if terminal == 0 else succeeded / terminal
            self._background_job_success_ratio.labels(kind.value).set(ratio)

    def render(self) -> bytes:
        return generate_latest(self.registry)
