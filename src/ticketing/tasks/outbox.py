import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from celery import Celery  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.core.config import Settings
from ticketing.models.background_job import OutboxStatus
from ticketing.repositories.background_job import BackgroundJobRepository


class TaskPublisher(Protocol):
    async def publish(
        self,
        task_name: str,
        payload: dict[str, object],
        *,
        message_id: UUID,
    ) -> None: ...


class CeleryTaskPublisher:
    def __init__(self, app: Celery) -> None:
        self._app = app

    async def publish(
        self,
        task_name: str,
        payload: dict[str, object],
        *,
        message_id: UUID,
    ) -> None:
        await asyncio.to_thread(
            self._app.send_task,
            task_name,
            kwargs=payload,
            task_id=str(message_id),
        )


class OutboxDispatcher:
    def __init__(
        self,
        session: AsyncSession,
        publisher: TaskPublisher,
        settings: Settings,
    ) -> None:
        self._session = session
        self._publisher = publisher
        self._settings = settings
        self._messages = BackgroundJobRepository(session)

    async def dispatch_once(self) -> int:
        now = datetime.now(UTC)
        messages = await self._messages.list_pending_outbox(
            now,
            limit=self._settings.outbox_batch_size,
        )
        published_count = 0
        for message in messages:
            message.publish_attempt_count += 1
            try:
                await self._publisher.publish(
                    message.task_name,
                    message.payload,
                    message_id=message.id,
                )
            except Exception as exc:
                message.last_error = self._safe_error(exc)
                message.available_at = now + timedelta(
                    seconds=self._retry_delay(message.publish_attempt_count)
                )
            else:
                message.status = OutboxStatus.PUBLISHED
                message.published_at = datetime.now(UTC)
                message.last_error = None
                published_count += 1
        await self._session.commit()
        return published_count

    def _retry_delay(self, attempt_count: int) -> int:
        delay = self._settings.background_job_retry_base_seconds * (1 << max(0, attempt_count - 1))
        return min(delay, self._settings.background_job_retry_max_seconds)

    @staticmethod
    def _safe_error(error: Exception) -> str:
        message = str(error).strip()
        return (message or error.__class__.__name__)[:2000]
