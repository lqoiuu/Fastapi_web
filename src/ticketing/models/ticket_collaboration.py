from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ticketing.db.base import Base, UUIDPrimaryKeyMixin


class CommentVisibility(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"


class TicketComment(UUIDPrimaryKeyMixin, Base):
    """A tenant-scoped ticket conversation entry."""

    __tablename__ = "ticket_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            ["tickets.tenant_id", "tickets.id"],
            name="fk_ticket_comments_tenant_ticket",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "author_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_comments_tenant_author_membership",
            ondelete="RESTRICT",
        ),
        Index("ix_ticket_comments_tenant_ticket_created", "tenant_id", "ticket_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    ticket_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    author_membership_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    visibility: Mapped[CommentVisibility] = mapped_column(
        Enum(
            CommentVisibility,
            name="comment_visibility",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TicketAttachment(UUIDPrimaryKeyMixin, Base):
    """Metadata for an attachment whose content is owned by a storage adapter."""

    __tablename__ = "ticket_attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            ["tickets.tenant_id", "tickets.id"],
            name="fk_ticket_attachments_tenant_ticket",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "uploader_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_attachments_tenant_uploader_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("size_bytes > 0", name="positive_size"),
        Index(
            "ix_ticket_attachments_tenant_ticket_created",
            "tenant_id",
            "ticket_id",
            "created_at",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    ticket_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    uploader_membership_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    object_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
