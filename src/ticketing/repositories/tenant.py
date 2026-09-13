from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.tenant import Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, tenant: Tenant) -> None:
        self._session.add(tenant)

    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        return await self._session.get(Tenant, tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        result = await self._session.execute(select(Tenant).where(Tenant.slug == slug))
        return result.scalar_one_or_none()

    async def get_many(self, tenant_ids: Sequence[UUID]) -> Sequence[Tenant]:
        if not tenant_ids:
            return []
        result = await self._session.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))
        return result.scalars().all()
