from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.membership import Membership, MembershipStatus


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, membership: Membership) -> None:
        self._session.add(membership)

    async def get_by_id(self, tenant_id: UUID, membership_id: UUID) -> Membership | None:
        result = await self._session.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.id == membership_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_user_and_tenant(
        self,
        user_id: UUID,
        tenant_id: UUID,
        *,
        for_update: bool = False,
    ) -> Membership | None:
        statement = select(Membership).where(
            Membership.user_id == user_id,
            Membership.tenant_id == tenant_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: UUID) -> Sequence[Membership]:
        result = await self._session.execute(
            select(Membership)
            .where(
                Membership.user_id == user_id,
                Membership.status != MembershipStatus.LEFT,
            )
            .order_by(Membership.created_at, Membership.id)
        )
        return result.scalars().all()

    async def list_for_tenant(self, tenant_id: UUID) -> Sequence[Membership]:
        result = await self._session.execute(
            select(Membership)
            .where(Membership.tenant_id == tenant_id)
            .order_by(Membership.created_at, Membership.id)
        )
        return result.scalars().all()
