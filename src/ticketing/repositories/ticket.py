from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.ticket import Ticket, TicketPriority, TicketStatus


@dataclass(frozen=True, slots=True)
class TicketFilters:
    creator_membership_id: UUID | None
    status: TicketStatus | None
    assignee_membership_id: UUID | None
    priority: TicketPriority | None
    created_from: datetime | None
    created_to: datetime | None
    keyword: str | None
    cursor_created_at: datetime | None
    cursor_ticket_id: UUID | None


class TicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, ticket: Ticket) -> None:
        self._session.add(ticket)

    async def get_by_id(
        self,
        tenant_id: UUID,
        ticket_id: UUID,
        *,
        for_update: bool = False,
    ) -> Ticket | None:
        statement = select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.id == ticket_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self,
        tenant_id: UUID,
        *,
        filters: TicketFilters,
        limit: int,
    ) -> Sequence[Ticket]:
        statement = select(Ticket).where(Ticket.tenant_id == tenant_id)
        if filters.creator_membership_id is not None:
            statement = statement.where(
                Ticket.created_by_membership_id == filters.creator_membership_id
            )
        if filters.status is not None:
            statement = statement.where(Ticket.status == filters.status)
        if filters.assignee_membership_id is not None:
            statement = statement.where(
                Ticket.assignee_membership_id == filters.assignee_membership_id
            )
        if filters.priority is not None:
            statement = statement.where(Ticket.priority == filters.priority)
        if filters.created_from is not None:
            statement = statement.where(Ticket.created_at >= filters.created_from)
        if filters.created_to is not None:
            statement = statement.where(Ticket.created_at <= filters.created_to)
        if filters.keyword is not None:
            statement = statement.where(
                or_(
                    Ticket.subject.icontains(filters.keyword, autoescape=True),
                    Ticket.description.icontains(filters.keyword, autoescape=True),
                )
            )
        if filters.cursor_created_at is not None and filters.cursor_ticket_id is not None:
            statement = statement.where(
                or_(
                    Ticket.created_at < filters.cursor_created_at,
                    and_(
                        Ticket.created_at == filters.cursor_created_at,
                        Ticket.id < filters.cursor_ticket_id,
                    ),
                )
            )
        result = await self._session.execute(
            statement.order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(limit)
        )
        return result.scalars().all()
