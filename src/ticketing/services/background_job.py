import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.cache.backend import CacheBackend
from ticketing.core.config import Settings
from ticketing.core.context import get_or_create_correlation_id
from ticketing.models.background_job import (
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    OutboxMessage,
    OutboxStatus,
)
from ticketing.models.ticket import TicketPriority, TicketStatus
from ticketing.repositories.background_job import BackgroundJobRepository
from ticketing.repositories.customer import CustomerRepository
from ticketing.services.rbac import PermissionCode, RBACService
from ticketing.services.tenant import CurrentTenant
from ticketing.services.ticket import TicketNotFoundError, TicketService

EXECUTE_JOB_TASK = "ticketing.execute_background_job"


class BackgroundJobServiceError(Exception):
    pass


class BackgroundJobNotFoundError(BackgroundJobServiceError):
    pass


class BackgroundJobIdempotencyConflictError(BackgroundJobServiceError):
    pass


class TicketNotificationRecipientNotFoundError(BackgroundJobServiceError):
    pass


class BackgroundJobResultNotReadyError(BackgroundJobServiceError):
    pass


@dataclass(frozen=True, slots=True)
class BackgroundJobSubmission:
    job: BackgroundJob
    replayed: bool


class BackgroundJobService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        cache: CacheBackend | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._jobs = BackgroundJobRepository(session)
        self._customers = CustomerRepository(session)
        self._tickets = TicketService(
            session,
            cache,
            permission_cache_ttl_seconds=settings.permission_cache_ttl_seconds,
            permission_cache_ttl_jitter_seconds=settings.permission_cache_ttl_jitter_seconds,
        )
        self._rbac = RBACService(
            session,
            cache,
            cache_ttl_seconds=settings.permission_cache_ttl_seconds,
            cache_ttl_jitter_seconds=settings.permission_cache_ttl_jitter_seconds,
        )

    async def submit_email_notification(
        self,
        context: CurrentTenant,
        *,
        ticket_id: UUID,
        idempotency_key: str,
    ) -> BackgroundJobSubmission:
        ticket = await self._tickets.get(context, ticket_id)
        if ticket.customer_id is None:
            raise TicketNotificationRecipientNotFoundError
        customer = await self._customers.get_by_id(context.tenant.id, ticket.customer_id)
        if customer is None or not customer.is_active:
            raise TicketNotificationRecipientNotFoundError
        payload: dict[str, object] = {
            "ticket_id": str(ticket.id),
            "recipient_email": customer.email,
            "subject": f"Ticket update: {ticket.subject}",
            "body": (
                f"Your ticket '{ticket.subject}' is currently "
                f"{ticket.status.value.replace('_', ' ')}."
            ),
        }
        return await self._submit(
            context,
            kind=BackgroundJobKind.EMAIL_NOTIFICATION,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    async def submit_ticket_export(
        self,
        context: CurrentTenant,
        *,
        status: TicketStatus | None,
        priority: TicketPriority | None,
        idempotency_key: str,
    ) -> BackgroundJobSubmission:
        can_read_all = await self._rbac.has_permission(
            context.membership,
            PermissionCode.TICKETS_READ_ALL,
        )
        payload: dict[str, object] = {
            "status": None if status is None else status.value,
            "priority": None if priority is None else priority.value,
            "creator_membership_id": None if can_read_all else str(context.membership.id),
        }
        return await self._submit(
            context,
            kind=BackgroundJobKind.TICKET_EXPORT,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    async def get(self, context: CurrentTenant, job_id: UUID) -> BackgroundJob:
        job = await self._jobs.get_job(context.tenant.id, job_id)
        if job is None or job.requested_by_membership_id != context.membership.id:
            raise BackgroundJobNotFoundError
        return job

    async def export_result_path(self, context: CurrentTenant, job_id: UUID) -> Path:
        job = await self.get(context, job_id)
        if (
            job.kind != BackgroundJobKind.TICKET_EXPORT
            or job.status != BackgroundJobStatus.SUCCEEDED
            or job.result is None
        ):
            raise BackgroundJobResultNotReadyError
        object_key = job.result.get("object_key")
        if not isinstance(object_key, str):
            raise BackgroundJobResultNotReadyError
        root = self._settings.export_storage_path.resolve()
        candidate = (root / object_key).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise BackgroundJobResultNotReadyError
        return candidate

    async def _submit(
        self,
        context: CurrentTenant,
        *,
        kind: BackgroundJobKind,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> BackgroundJobSubmission:
        tenant_id = context.tenant.id
        membership_id = context.membership.id
        request_hash = self._request_hash(kind, payload)
        existing = await self._jobs.get_by_idempotency_key(
            tenant_id,
            membership_id,
            idempotency_key,
        )
        if existing is not None:
            return self._replay(existing, request_hash)

        job = BackgroundJob(
            id=uuid4(),
            tenant_id=tenant_id,
            requested_by_membership_id=membership_id,
            kind=kind,
            status=BackgroundJobStatus.PENDING,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            payload=payload,
            max_attempts=self._settings.background_job_max_attempts,
        )
        self._jobs.add_job(job)
        self._jobs.add_outbox_message(
            OutboxMessage(
                id=uuid4(),
                tenant_id=tenant_id,
                job_id=job.id,
                status=OutboxStatus.PENDING,
                task_name=EXECUTE_JOB_TASK,
                payload={
                    "job_id": str(job.id),
                    "correlation_id": get_or_create_correlation_id(),
                },
            )
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raced = await self._jobs.get_by_idempotency_key(
                tenant_id,
                membership_id,
                idempotency_key,
            )
            if raced is None:
                raise
            return self._replay(raced, request_hash)
        await self._session.refresh(job)
        return BackgroundJobSubmission(job=job, replayed=False)

    @staticmethod
    def _request_hash(kind: BackgroundJobKind, payload: dict[str, object]) -> str:
        canonical = json.dumps(
            {"kind": kind.value, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _replay(job: BackgroundJob, request_hash: str) -> BackgroundJobSubmission:
        if job.request_hash != request_hash:
            raise BackgroundJobIdempotencyConflictError
        return BackgroundJobSubmission(job=job, replayed=True)


__all__ = [
    "BackgroundJobIdempotencyConflictError",
    "BackgroundJobNotFoundError",
    "BackgroundJobResultNotReadyError",
    "BackgroundJobService",
    "BackgroundJobServiceError",
    "BackgroundJobSubmission",
    "EXECUTE_JOB_TASK",
    "TicketNotificationRecipientNotFoundError",
    "TicketNotFoundError",
]
