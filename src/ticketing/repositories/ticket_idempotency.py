from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.ticket_idempotency import TicketCreationKey


class TicketCreationKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, record: TicketCreationKey) -> None:
        self._session.add(record)

    async def get(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        key: str,
    ) -> TicketCreationKey | None:
        result = await self._session.execute(
            select(TicketCreationKey).where(
                TicketCreationKey.tenant_id == tenant_id,
                TicketCreationKey.membership_id == membership_id,
                TicketCreationKey.key == key,
            )
        )
        return result.scalar_one_or_none()
