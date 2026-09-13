from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, ForeignKeyConstraint, Index, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ticketing.db.base import Base, UUIDPrimaryKeyMixin
from ticketing.models.ticket import TicketStatus


class TicketEventType(StrEnum):
    CREATED = "created"
    CLAIMED = "claimed"
    TRANSFERRED = "transferred"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    CLOSED = "closed"


class TicketEvent(UUIDPrimaryKeyMixin, Base):
    """An append-only audit record written atomically with a ticket action."""

    __tablename__ = "ticket_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            ["tickets.tenant_id", "tickets.id"],
            name="fk_ticket_events_tenant_ticket",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_events_tenant_actor_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "from_assignee_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_events_tenant_from_assignee_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "to_assignee_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_events_tenant_to_assignee_membership",
            ondelete="RESTRICT",
        ),
        Index("ix_ticket_events_tenant_ticket_created", "tenant_id", "ticket_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    ticket_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_membership_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[TicketEventType] = mapped_column(
        Enum(
            TicketEventType,
            name="ticket_event_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    from_status: Mapped[TicketStatus | None] = mapped_column(
        Enum(
            TicketStatus,
            name="ticket_event_from_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        )
    )
    to_status: Mapped[TicketStatus] = mapped_column(
        Enum(
            TicketStatus,
            name="ticket_event_to_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    from_assignee_membership_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    to_assignee_membership_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
