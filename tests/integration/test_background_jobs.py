import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from tests.support.api_factory import (
    build_test_settings as build_base_test_settings,
)
from tests.support.api_factory import (
    create_client as create_test_client,
)
from tests.support.api_factory import (
    create_tenant,
    create_ticket_with_customer,
    register_and_login,
)
from tests.support.api_factory import (
    with_tenant as tenant_headers,
)
from ticketing.core.config import Settings
from ticketing.db.session import Database
from ticketing.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
    NotificationDelivery,
    NotificationDeliveryStatus,
    OutboxMessage,
    OutboxStatus,
)
from ticketing.tasks.email import EmailMessage
from ticketing.tasks.execution import BackgroundJobExecutor, RetryableJobError
from ticketing.tasks.outbox import OutboxDispatcher

pytestmark = pytest.mark.integration


class MemoryEmailSender:
    backend_name = "memory"

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


class FailingEmailSender:
    backend_name = "failing"

    def __init__(self) -> None:
        self.calls = 0

    def send(self, message: EmailMessage) -> None:
        self.calls += 1
        raise OSError("temporary SMTP failure")


class MemoryPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[str, dict[str, object], UUID]] = []

    async def publish(
        self,
        task_name: str,
        payload: dict[str, object],
        *,
        message_id: UUID,
    ) -> None:
        if self.fail:
            raise OSError("broker unavailable")
        self.messages.append((task_name, payload, message_id))


def build_test_settings() -> Settings:
    return build_base_test_settings(
        background_job_retry_base_seconds=1,
        background_job_retry_max_seconds=2,
    )


def create_client() -> TestClient:
    return create_test_client(
        background_job_retry_base_seconds=1,
        background_job_retry_max_seconds=2,
    )


async def execute_job(job_id: str, sender: MemoryEmailSender | FailingEmailSender) -> None:
    settings = build_test_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            await BackgroundJobExecutor(session, settings, sender).execute(UUID(job_id))
    finally:
        await database.dispose()


async def job_counts() -> tuple[int, int]:
    database = Database.from_settings(build_test_settings())
    try:
        async with database.session() as session:
            jobs = await session.scalar(select(func.count()).select_from(BackgroundJob))
            messages = await session.scalar(select(func.count()).select_from(OutboxMessage))
            return int(jobs or 0), int(messages or 0)
    finally:
        await database.dispose()


def test_job_submission_is_transactional_idempotent_and_tenant_scoped() -> None:
    with create_client() as client:
        owner = register_and_login(client, "jobs-owner@example.com")
        other_owner = register_and_login(client, "jobs-other@example.com")
        tenant_id = create_tenant(client, owner, "jobs-owner")
        other_tenant_id = create_tenant(client, other_owner, "jobs-other")
        ticket_id = create_ticket_with_customer(
            client,
            owner,
            tenant_id,
            subject="Reliable notification",
        )
        headers = {
            **tenant_headers(owner, tenant_id),
            "Idempotency-Key": "notification-job-0001",
        }

        first = client.post(
            f"/api/v1/tenants/{tenant_id}/background-jobs/email-notifications",
            headers=headers,
            json={"ticket_id": ticket_id},
        )
        replay = client.post(
            f"/api/v1/tenants/{tenant_id}/background-jobs/email-notifications",
            headers=headers,
            json={"ticket_id": ticket_id},
        )

        assert first.status_code == replay.status_code == 202
        assert first.json()["id"] == replay.json()["id"]
        assert first.json()["status"] == "pending"
        assert first.headers["location"].endswith(first.json()["id"])
        assert "Idempotency-Replayed" not in first.headers
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert asyncio.run(job_counts()) == (1, 1)

        conflict = client.post(
            f"/api/v1/tenants/{tenant_id}/background-jobs/ticket-exports",
            headers=headers,
            json={},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_key_conflict"

        visible = client.get(
            first.headers["location"],
            headers=tenant_headers(owner, tenant_id),
        )
        assert visible.status_code == 200

        cross_tenant = client.get(
            first.headers["location"],
            headers=tenant_headers(other_owner, other_tenant_id),
        )
        assert cross_tenant.status_code == 404


def test_email_job_retries_known_failure_and_duplicate_execution_is_a_noop() -> None:
    with create_client() as client:
        owner = register_and_login(client, "email-worker-owner@example.com")
        tenant_id = create_tenant(client, owner, "email-worker")
        ticket_id = create_ticket_with_customer(
            client,
            owner,
            tenant_id,
            subject="Retry email",
        )
        submitted = client.post(
            f"/api/v1/tenants/{tenant_id}/background-jobs/email-notifications",
            headers={
                **tenant_headers(owner, tenant_id),
                "Idempotency-Key": "email-retry-job-0001",
            },
            json={"ticket_id": ticket_id},
        )
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]

        failing_sender = FailingEmailSender()
        with pytest.raises(RetryableJobError):
            asyncio.run(execute_job(job_id, failing_sender))
        assert failing_sender.calls == 1

        successful_sender = MemoryEmailSender()
        asyncio.run(execute_job(job_id, successful_sender))
        asyncio.run(execute_job(job_id, successful_sender))
        assert len(successful_sender.messages) == 1
        assert successful_sender.messages[0].message_id == f"<{job_id}@ticketing.local>"

        status_response = client.get(
            submitted.headers["location"],
            headers=tenant_headers(owner, tenant_id),
        )
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "succeeded"
        assert status_response.json()["attempt_count"] == 2
        assert status_response.json()["result"] == {"delivery": "sent", "backend": "memory"}


def test_email_retries_are_bounded_and_unknown_delivery_is_not_resent() -> None:
    with create_client() as client:
        owner = register_and_login(client, "bounded-email-owner@example.com")
        tenant_id = create_tenant(client, owner, "bounded-email")
        ticket_id = create_ticket_with_customer(
            client,
            owner,
            tenant_id,
            subject="Bound retry attempts",
        )

        def submit(idempotency_key: str) -> dict[str, object]:
            response = client.post(
                f"/api/v1/tenants/{tenant_id}/background-jobs/email-notifications",
                headers={
                    **tenant_headers(owner, tenant_id),
                    "Idempotency-Key": idempotency_key,
                },
                json={"ticket_id": ticket_id},
            )
            assert response.status_code == 202
            payload = response.json()
            assert isinstance(payload, dict)
            return payload

        bounded_job = submit("bounded-email-job-0001")
        bounded_job_id = str(bounded_job["id"])

        async def set_max_attempts_and_seed_ambiguous_delivery(job_id: str | None = None) -> None:
            database = Database.from_settings(build_test_settings())
            try:
                async with database.session() as session:
                    if job_id is None:
                        await session.execute(
                            update(BackgroundJob)
                            .where(BackgroundJob.id == UUID(bounded_job_id))
                            .values(max_attempts=2)
                        )
                    else:
                        job = (
                            await session.execute(
                                select(BackgroundJob).where(BackgroundJob.id == UUID(job_id))
                            )
                        ).scalar_one()
                        job.status = BackgroundJobStatus.RUNNING
                        job.attempt_count = 1
                        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                        session.add(
                            NotificationDelivery(
                                tenant_id=job.tenant_id,
                                job_id=job.id,
                                status=NotificationDeliveryStatus.SENDING,
                                recipient_email="customer@example.com",
                                message_id=f"<{job.id}@ticketing.local>",
                                attempt_count=1,
                            )
                        )
                    await session.commit()
            finally:
                await database.dispose()

        asyncio.run(set_max_attempts_and_seed_ambiguous_delivery())
        failing_sender = FailingEmailSender()
        with pytest.raises(RetryableJobError):
            asyncio.run(execute_job(bounded_job_id, failing_sender))
        asyncio.run(execute_job(bounded_job_id, failing_sender))
        bounded_status = client.get(
            f"/api/v1/tenants/{tenant_id}/background-jobs/{bounded_job_id}",
            headers=tenant_headers(owner, tenant_id),
        )
        assert bounded_status.json()["status"] == "failed"
        assert bounded_status.json()["attempt_count"] == 2
        assert bounded_status.json()["last_error_code"] == "background_job_execution_failed"

        ambiguous_job = submit("ambiguous-email-job-0001")
        ambiguous_job_id = str(ambiguous_job["id"])
        asyncio.run(set_max_attempts_and_seed_ambiguous_delivery(ambiguous_job_id))
        sender = MemoryEmailSender()
        asyncio.run(execute_job(ambiguous_job_id, sender))
        assert sender.messages == []
        ambiguous_status = client.get(
            f"/api/v1/tenants/{tenant_id}/background-jobs/{ambiguous_job_id}",
            headers=tenant_headers(owner, tenant_id),
        )
        assert ambiguous_status.json()["status"] == "failed"
        assert ambiguous_status.json()["last_error_code"] == "email_delivery_ambiguous"


def test_outbox_recovers_after_broker_failure_and_expired_worker_lease() -> None:
    with create_client() as client:
        owner = register_and_login(client, "export-worker-owner@example.com")
        tenant_id = create_tenant(client, owner, "export-worker")
        create_ticket_with_customer(
            client,
            owner,
            tenant_id,
            subject="=Export formula guard",
        )
        submitted = client.post(
            f"/api/v1/tenants/{tenant_id}/background-jobs/ticket-exports",
            headers={
                **tenant_headers(owner, tenant_id),
                "Idempotency-Key": "ticket-export-job-0001",
                "X-Correlation-ID": "export-correlation-123",
            },
            json={},
        )
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]

        async def exercise_outbox_and_expired_lease() -> MemoryPublisher:
            settings = build_test_settings()
            database = Database.from_settings(settings)
            try:
                async with database.session() as session:
                    failed_dispatch = OutboxDispatcher(
                        session,
                        MemoryPublisher(fail=True),
                        settings,
                    )
                    assert await failed_dispatch.dispatch_once() == 0
                    job_uuid = UUID(job_id)
                    message = (
                        await session.execute(
                            select(OutboxMessage).where(OutboxMessage.job_id == job_uuid)
                        )
                    ).scalar_one()
                    assert message.status == OutboxStatus.PENDING
                    assert message.publish_attempt_count == 1
                    await session.execute(
                        update(OutboxMessage)
                        .where(OutboxMessage.job_id == job_uuid)
                        .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
                    )
                    await session.execute(
                        update(BackgroundJob)
                        .where(BackgroundJob.id == job_uuid)
                        .values(
                            status=BackgroundJobStatus.RUNNING,
                            attempt_count=1,
                            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
                        )
                    )
                    await session.commit()

                    publisher = MemoryPublisher()
                    assert await OutboxDispatcher(session, publisher, settings).dispatch_once() == 1
                    return publisher
            finally:
                await database.dispose()

        publisher = asyncio.run(exercise_outbox_and_expired_lease())
        assert len(publisher.messages) == 1
        assert publisher.messages[0][0] == "ticketing.execute_background_job"
        published_payload = publisher.messages[0][1]
        assert published_payload["job_id"] == job_id
        assert published_payload["correlation_id"] == "export-correlation-123"

        asyncio.run(execute_job(job_id, MemoryEmailSender()))
        status_response = client.get(
            submitted.headers["location"],
            headers=tenant_headers(owner, tenant_id),
        )
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "succeeded"
        assert status_response.json()["attempt_count"] == 2
        assert status_response.json()["result"]["row_count"] == 1

        downloaded = client.get(
            f"{submitted.headers['location']}/result",
            headers=tenant_headers(owner, tenant_id),
        )
        assert downloaded.status_code == 200
        csv_text = downloaded.content.decode("utf-8-sig")
        assert "'=Export formula guard" in csv_text

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert (
            'ticketing_background_jobs{kind="ticket_export",status="succeeded"} 1.0' in metrics.text
        )
        assert 'ticketing_background_job_success_ratio{kind="ticket_export"} 1.0' in metrics.text
