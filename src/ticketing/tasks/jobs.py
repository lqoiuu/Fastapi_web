import asyncio
import logging
from time import perf_counter
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]

from ticketing.core.config import get_settings
from ticketing.core.context import correlation_context
from ticketing.db.session import Database
from ticketing.tasks.celery_app import celery_app
from ticketing.tasks.email import email_sender_from_settings
from ticketing.tasks.execution import (
    BackgroundJobExecutor,
    JobLeaseActiveError,
    RetryableJobError,
)

logger = logging.getLogger(__name__)


async def run_background_job(job_id: UUID) -> None:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            executor = BackgroundJobExecutor(
                session,
                settings,
                email_sender_from_settings(settings),
            )
            await executor.execute(job_id)
    finally:
        await database.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="ticketing.execute_background_job",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
    soft_time_limit=get_settings().background_job_soft_time_limit_seconds,
    time_limit=get_settings().background_job_hard_time_limit_seconds,
)
def execute_background_job(self: Task, job_id: str, correlation_id: str | None = None) -> None:
    with correlation_context(correlation_id):
        started_at = perf_counter()
        logger.info("Background job started", extra={"job_id": job_id})
        try:
            asyncio.run(run_background_job(UUID(job_id)))
        except (JobLeaseActiveError, RetryableJobError) as exc:
            logger.warning(
                "Background job deferred",
                extra={
                    "job_id": job_id,
                    "retry_after_seconds": exc.retry_after_seconds,
                    "error_type": exc.__class__.__name__,
                },
            )
            raise self.retry(exc=exc, countdown=exc.retry_after_seconds, max_retries=None) from exc
        finally:
            logger.info(
                "Background job execution finished",
                extra={
                    "job_id": job_id,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
            )
