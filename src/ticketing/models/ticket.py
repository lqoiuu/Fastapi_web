from enum import StrEnum
from uuid import UUID

from sqlalchemy import Enum, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ticketing.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Ticket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant-scoped support request with workflow changes deferred to stage 7."""

    __tablename__ = "tickets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            name="fk_tickets_tenant_customer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_tickets_tenant_creator_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assignee_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_tickets_tenant_assignee_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_tickets_tenant_id_id"),
        Index("ix_tickets_tenant_created_id", "tenant_id", "created_at", "id"),
        Index(
            "ix_tickets_tenant_status_created_id",
            "tenant_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_tickets_tenant_assignee_created_id",
            "tenant_id",
            "assignee_membership_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_tickets_tenant_priority_created_id",
            "tenant_id",
            "priority",
            "created_at",
            "id",
        ),
        Index(
            "ix_tickets_tenant_creator_created_id",
            "tenant_id",
            "created_by_membership_id",
            "created_at",
            "id",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    created_by_membership_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, index=True
    )
    assignee_membership_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        Enum(
            TicketStatus,
            name="ticket_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=TicketStatus.OPEN,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(
            TicketPriority,
            name="ticket_priority",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=TicketPriority.NORMAL,
    )
