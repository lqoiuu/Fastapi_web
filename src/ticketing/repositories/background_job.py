from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.background_job import (
    BackgroundJob,
    NotificationDelivery,
    OutboxMessage,
    OutboxStatus,
)


class BackgroundJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_job(self, job: BackgroundJob) -> None:
        self._session.add(job)

    def add_outbox_message(self, message: OutboxMessage) -> None:
        self._session.add(message)

    def add_notification_delivery(self, delivery: NotificationDelivery) -> None:
        self._session.add(delivery)

    async def get_job(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        for_update: bool = False,
    ) -> BackgroundJob | None:
        statement = select(BackgroundJob).where(
            BackgroundJob.tenant_id == tenant_id,
            BackgroundJob.id == job_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_job_by_id(
        self,
        job_id: UUID,
        *,
        for_update: bool = False,
    ) -> BackgroundJob | None:
        statement = select(BackgroundJob).where(BackgroundJob.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        idempotency_key: str,
    ) -> BackgroundJob | None:
        result = await self._session.execute(
            select(BackgroundJob).where(
                BackgroundJob.tenant_id == tenant_id,
                BackgroundJob.requested_by_membership_id == membership_id,
                BackgroundJob.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_notification_delivery(
        self,
        job_id: UUID,
        *,
        for_update: bool = False,
    ) -> NotificationDelivery | None:
        statement = select(NotificationDelivery).where(NotificationDelivery.job_id == job_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_pending_outbox(
        self,
        now: datetime,
        *,
        limit: int,
    ) -> Sequence[OutboxMessage]:
        result = await self._session.execute(
            select(OutboxMessage)
            .where(
                OutboxMessage.status == OutboxStatus.PENDING,
                OutboxMessage.available_at <= now,
            )
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return result.scalars().all()
