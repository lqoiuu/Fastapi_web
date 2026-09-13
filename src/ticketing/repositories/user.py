from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_many(self, user_ids: Sequence[UUID]) -> Sequence[User]:
        if not user_ids:
            return []
        result = await self._session.execute(select(User).where(User.id.in_(user_ids)))
        return result.scalars().all()

    def add(self, user: User) -> None:
        self._session.add(user)
