from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ticketing.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BackgroundJobKind(StrEnum):
    EMAIL_NOTIFICATION = "email_notification"
    TICKET_EXPORT = "ticket_export"


class BackgroundJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"


class NotificationDeliveryStatus(StrEnum):
    SENDING = "sending"
    SENT = "sent"
    RETRYABLE_FAILURE = "retryable_failure"
    AMBIGUOUS_FAILURE = "ambiguous_failure"


def _enum(enum_type: type[StrEnum], name: str, *, length: int) -> Enum:
    return Enum(
        enum_type,
        name=name,
        length=length,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum: [item.value for item in enum],
    )


class BackgroundJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-scoped durable job state, independent from Celery's result backend."""

    __tablename__ = "background_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_background_jobs_tenant_requester_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_background_jobs_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "requested_by_membership_id",
            "idempotency_key",
            name="uq_background_jobs_scope_idempotency_key",
        ),
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempt_count"),
        CheckConstraint("max_attempts > 0", name="positive_max_attempts"),
        Index("ix_background_jobs_tenant_created", "tenant_id", "created_at", "id"),
        Index("ix_background_jobs_tenant_status", "tenant_id", "status", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_membership_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[BackgroundJobKind] = mapped_column(
        _enum(BackgroundJobKind, "background_job_kind", length=18), nullable=False
    )
    status: Mapped[BackgroundJobStatus] = mapped_column(
        _enum(BackgroundJobStatus, "background_job_status", length=9),
        nullable=False,
        default=BackgroundJobStatus.PENDING,
        server_default=text("'pending'"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A broker publication intent committed atomically with its background job."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_outbox_messages_tenant_job",
            ondelete="CASCADE",
        ),
        UniqueConstraint("job_id", name="uq_outbox_messages_job_id"),
        CheckConstraint("publish_attempt_count >= 0", name="nonnegative_publish_attempt_count"),
        Index("ix_outbox_messages_pending", "status", "available_at", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        _enum(OutboxStatus, "outbox_status", length=9),
        nullable=False,
        default=OutboxStatus.PENDING,
        server_default=text("'pending'"),
    )
    task_name: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    publish_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class NotificationDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A durable guard against uncontrolled duplicate email delivery attempts."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_notification_deliveries_tenant_job",
            ondelete="CASCADE",
        ),
        UniqueConstraint("job_id", name="uq_notification_deliveries_job_id"),
        UniqueConstraint("message_id", name="uq_notification_deliveries_message_id"),
        CheckConstraint("attempt_count > 0", name="positive_attempt_count"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        _enum(NotificationDeliveryStatus, "notification_delivery_status", length=18),
        nullable=False,
    )
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
