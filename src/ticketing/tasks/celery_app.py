import logging

from celery import Celery  # type: ignore[import-untyped]
from celery.signals import setup_logging  # type: ignore[import-untyped]

from ticketing.core.config import get_settings
from ticketing.core.logging import configure_logging

settings = get_settings()

celery_app = Celery(
    "ticketing",
    broker=settings.celery_broker_url,
    include=["ticketing.tasks.jobs", "ticketing.tasks.outbox_tasks"],
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": max(
            settings.background_job_lease_seconds * 2,
            settings.background_job_hard_time_limit_seconds * 2,
        )
    },
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "dispatch-transactional-outbox": {
            "task": "ticketing.dispatch_outbox",
            "schedule": settings.outbox_poll_seconds,
        }
    },
)


@setup_logging.connect  # type: ignore[untyped-decorator]
def configure_worker_logging(loglevel: int | None = None, **kwargs: object) -> None:
    del kwargs
    level_name = logging.getLevelName(loglevel or logging.INFO)
    configure_logging(level_name if isinstance(level_name, str) else "INFO")
