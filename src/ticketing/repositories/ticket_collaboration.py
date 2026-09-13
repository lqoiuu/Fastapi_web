from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.ticket_collaboration import (
    CommentVisibility,
    TicketAttachment,
    TicketComment,
)


class TicketCommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, comment: TicketComment) -> None:
        self._session.add(comment)

    async def list_for_ticket(
        self,
        tenant_id: UUID,
        ticket_id: UUID,
        *,
        include_internal: bool,
    ) -> Sequence[TicketComment]:
        statement = select(TicketComment).where(
            TicketComment.tenant_id == tenant_id,
            TicketComment.ticket_id == ticket_id,
        )
        if not include_internal:
            statement = statement.where(TicketComment.visibility == CommentVisibility.PUBLIC)
        result = await self._session.execute(
            statement.order_by(TicketComment.created_at, TicketComment.id)
        )
        return result.scalars().all()


class TicketAttachmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, attachment: TicketAttachment) -> None:
        self._session.add(attachment)

    async def get_by_id(
        self,
        tenant_id: UUID,
        ticket_id: UUID,
        attachment_id: UUID,
    ) -> TicketAttachment | None:
        result = await self._session.execute(
            select(TicketAttachment).where(
                TicketAttachment.tenant_id == tenant_id,
                TicketAttachment.ticket_id == ticket_id,
                TicketAttachment.id == attachment_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_ticket(
        self,
        tenant_id: UUID,
        ticket_id: UUID,
    ) -> Sequence[TicketAttachment]:
        result = await self._session.execute(
            select(TicketAttachment)
            .where(
                TicketAttachment.tenant_id == tenant_id,
                TicketAttachment.ticket_id == ticket_id,
            )
            .order_by(TicketAttachment.created_at, TicketAttachment.id)
        )
        return result.scalars().all()
