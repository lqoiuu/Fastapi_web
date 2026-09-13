from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_update(self, token_id: UUID) -> RefreshToken | None:
        statement = select(RefreshToken).where(RefreshToken.id == token_id).with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    def add(self, token: RefreshToken) -> None:
        self._session.add(token)

    async def revoke_all_active(self, user_id: UUID, revoked_at: datetime) -> None:
        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        await self._session.execute(statement)
