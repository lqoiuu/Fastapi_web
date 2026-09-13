import asyncio
import csv
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.core.config import Settings
from ticketing.models.background_job import (
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    NotificationDelivery,
    NotificationDeliveryStatus,
)
from ticketing.models.ticket import Ticket, TicketPriority, TicketStatus
from ticketing.repositories.background_job import BackgroundJobRepository
from ticketing.tasks.email import EmailMessage, EmailSender


class JobLeaseActiveError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Background job has an active worker lease")


class RetryableJobError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Background job will be retried")


class PermanentJobError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class BackgroundJobExecutor:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        email_sender: EmailSender,
    ) -> None:
        self._session = session
        self._settings = settings
        self._jobs = BackgroundJobRepository(session)
        self._email_sender = email_sender

    async def execute(self, job_id: UUID) -> None:
        job = await self._claim(job_id)
        if job is None:
            return
        try:
            if job.kind == BackgroundJobKind.EMAIL_NOTIFICATION:
                await self._execute_email(job)
            elif job.kind == BackgroundJobKind.TICKET_EXPORT:
                await self._execute_export(job)
            else:
                raise PermanentJobError("unsupported_job_kind", "Unsupported background job kind")
        except PermanentJobError as exc:
            await self._mark_failed(job, exc.code, str(exc))
        except Exception as exc:
            should_retry = await self._mark_retryable_failure(
                job,
                "background_job_execution_failed",
                str(exc),
            )
            if should_retry:
                raise RetryableJobError(self._retry_delay(job.attempt_count)) from exc

    async def _claim(self, job_id: UUID) -> BackgroundJob | None:
        now = datetime.now(UTC)
        job = await self._jobs.get_job_by_id(job_id, for_update=True)
        if job is None or job.status in {
            BackgroundJobStatus.SUCCEEDED,
            BackgroundJobStatus.FAILED,
        }:
            await self._session.rollback()
            return None
        if (
            job.status == BackgroundJobStatus.RUNNING
            and job.lease_expires_at is not None
            and job.lease_expires_at > now
        ):
            retry_after = max(1, int((job.lease_expires_at - now).total_seconds()) + 1)
            await self._session.rollback()
            raise JobLeaseActiveError(retry_after)
        if job.attempt_count >= job.max_attempts:
            job.status = BackgroundJobStatus.FAILED
            job.last_error_code = "background_job_attempts_exhausted"
            job.last_error_message = "Background job attempts were exhausted"
            job.completed_at = now
            job.lease_expires_at = None
            await self._session.commit()
            return None
        job.status = BackgroundJobStatus.RUNNING
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.lease_expires_at = now + timedelta(seconds=self._settings.background_job_lease_seconds)
        job.last_error_code = None
        job.last_error_message = None
        await self._session.commit()
        return job

    async def _execute_email(self, job: BackgroundJob) -> None:
        recipient = self._payload_string(job, "recipient_email")
        subject = self._payload_string(job, "subject")
        body = self._payload_string(job, "body")
        message_id = f"<{job.id}@ticketing.local>"
        delivery = await self._jobs.get_notification_delivery(job.id, for_update=True)
        if delivery is not None and delivery.status == NotificationDeliveryStatus.SENT:
            await self._mark_succeeded(
                job,
                {"delivery": "sent", "backend": self._email_sender.backend_name},
            )
            return
        if delivery is not None and delivery.status in {
            NotificationDeliveryStatus.SENDING,
            NotificationDeliveryStatus.AMBIGUOUS_FAILURE,
        }:
            delivery.status = NotificationDeliveryStatus.AMBIGUOUS_FAILURE
            delivery.last_error = "Previous worker stopped with an unknown delivery outcome"
            await self._session.commit()
            raise PermanentJobError(
                "email_delivery_ambiguous",
                "Email delivery outcome is ambiguous; automatic resend was suppressed",
            )
        if delivery is None:
            delivery = NotificationDelivery(
                id=uuid4(),
                tenant_id=job.tenant_id,
                job_id=job.id,
                status=NotificationDeliveryStatus.SENDING,
                recipient_email=recipient,
                message_id=message_id,
                attempt_count=1,
            )
            self._jobs.add_notification_delivery(delivery)
        else:
            delivery.status = NotificationDeliveryStatus.SENDING
            delivery.attempt_count += 1
            delivery.last_error = None
        await self._session.commit()

        try:
            await asyncio.to_thread(
                self._email_sender.send,
                EmailMessage(
                    recipient=recipient,
                    subject=subject,
                    body=body,
                    message_id=message_id,
                ),
            )
        except Exception as exc:
            delivery = await self._jobs.get_notification_delivery(job.id, for_update=True)
            if delivery is not None:
                delivery.status = NotificationDeliveryStatus.RETRYABLE_FAILURE
                delivery.last_error = self._safe_error(exc)
            await self._session.commit()
            raise

        delivery = await self._jobs.get_notification_delivery(job.id, for_update=True)
        if delivery is None:
            raise PermanentJobError(
                "email_delivery_record_missing",
                "Email delivery record disappeared during execution",
            )
        delivery.status = NotificationDeliveryStatus.SENT
        delivery.delivered_at = datetime.now(UTC)
        delivery.last_error = None
        await self._mark_succeeded(
            job,
            {"delivery": "sent", "backend": self._email_sender.backend_name},
        )

    async def _execute_export(self, job: BackgroundJob) -> None:
        statement = select(Ticket).where(Ticket.tenant_id == job.tenant_id)
        status_value = job.payload.get("status")
        priority_value = job.payload.get("priority")
        creator_value = job.payload.get("creator_membership_id")
        if status_value is not None:
            if not isinstance(status_value, str):
                raise PermanentJobError("invalid_job_payload", "Invalid export status")
            statement = statement.where(Ticket.status == TicketStatus(status_value))
        if priority_value is not None:
            if not isinstance(priority_value, str):
                raise PermanentJobError("invalid_job_payload", "Invalid export priority")
            statement = statement.where(Ticket.priority == TicketPriority(priority_value))
        if creator_value is not None:
            if not isinstance(creator_value, str):
                raise PermanentJobError("invalid_job_payload", "Invalid export creator")
            statement = statement.where(Ticket.created_by_membership_id == UUID(creator_value))
        statement = statement.order_by(Ticket.created_at, Ticket.id).limit(
            self._settings.export_max_rows + 1
        )
        tickets = list((await self._session.execute(statement)).scalars().all())
        if len(tickets) > self._settings.export_max_rows:
            raise PermanentJobError(
                "ticket_export_too_large",
                f"Ticket export exceeds the {self._settings.export_max_rows}-row limit",
            )
        object_key = f"{job.tenant_id}/{job.id}.csv"
        output_path = self._settings.export_storage_path.resolve() / object_key
        await asyncio.to_thread(self._write_export, output_path, tickets)
        await self._mark_succeeded(
            job,
            {"object_key": object_key, "row_count": len(tickets), "content_type": "text/csv"},
        )

    async def _mark_succeeded(self, job: BackgroundJob, result: dict[str, object]) -> None:
        job.status = BackgroundJobStatus.SUCCEEDED
        job.result = result
        job.last_error_code = None
        job.last_error_message = None
        job.completed_at = datetime.now(UTC)
        job.lease_expires_at = None
        await self._session.commit()

    async def _mark_failed(self, job: BackgroundJob, code: str, message: str) -> None:
        job.status = BackgroundJobStatus.FAILED
        job.last_error_code = code
        job.last_error_message = message[:2000]
        job.completed_at = datetime.now(UTC)
        job.lease_expires_at = None
        await self._session.commit()

    async def _mark_retryable_failure(
        self,
        job: BackgroundJob,
        code: str,
        message: str,
    ) -> bool:
        job.last_error_code = code
        job.last_error_message = message[:2000] or "Background job execution failed"
        job.lease_expires_at = None
        if job.attempt_count >= job.max_attempts:
            job.status = BackgroundJobStatus.FAILED
            job.completed_at = datetime.now(UTC)
            should_retry = False
        else:
            job.status = BackgroundJobStatus.RETRYING
            should_retry = True
        await self._session.commit()
        return should_retry

    def _retry_delay(self, attempt_count: int) -> int:
        delay = self._settings.background_job_retry_base_seconds * (1 << max(0, attempt_count - 1))
        return min(delay, self._settings.background_job_retry_max_seconds)

    @staticmethod
    def _payload_string(job: BackgroundJob, key: str) -> str:
        value = job.payload.get(key)
        if not isinstance(value, str) or not value:
            raise PermanentJobError("invalid_job_payload", f"Missing job payload field: {key}")
        return value

    @staticmethod
    def _safe_error(error: Exception) -> str:
        message = str(error).strip()
        return (message or error.__class__.__name__)[:2000]

    @classmethod
    def _write_export(cls, output_path: Path, tickets: list[Ticket]) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with temporary_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "id",
                        "customer_id",
                        "created_by_membership_id",
                        "assignee_membership_id",
                        "subject",
                        "description",
                        "status",
                        "priority",
                        "created_at",
                        "updated_at",
                    ]
                )
                for ticket in tickets:
                    writer.writerow(
                        [
                            ticket.id,
                            ticket.customer_id or "",
                            ticket.created_by_membership_id,
                            ticket.assignee_membership_id or "",
                            cls._safe_csv_cell(ticket.subject),
                            cls._safe_csv_cell(ticket.description),
                            ticket.status.value,
                            ticket.priority.value,
                            ticket.created_at.isoformat(),
                            ticket.updated_at.isoformat(),
                        ]
                    )
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _safe_csv_cell(value: str) -> str:
        return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value
