from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.ticket_event import TicketEvent


class TicketEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: TicketEvent) -> None:
        self._session.add(event)

    async def list_for_ticket(self, tenant_id: UUID, ticket_id: UUID) -> Sequence[TicketEvent]:
        result = await self._session.execute(
            select(TicketEvent)
            .where(
                TicketEvent.tenant_id == tenant_id,
                TicketEvent.ticket_id == ticket_id,
            )
            .order_by(TicketEvent.created_at, TicketEvent.id)
        )
        return result.scalars().all()
