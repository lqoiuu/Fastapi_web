from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.customer import Customer


class CustomerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, customer: Customer) -> None:
        self._session.add(customer)

    async def get_by_id(self, tenant_id: UUID, customer_id: UUID) -> Customer | None:
        result = await self._session.execute(
            select(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.id == customer_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, tenant_id: UUID, email: str) -> Customer | None:
        result = await self._session.execute(
            select(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.email == email,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self, tenant_id: UUID, *, offset: int, limit: int
    ) -> Sequence[Customer]:
        result = await self._session.execute(
            select(Customer)
            .where(Customer.tenant_id == tenant_id)
            .order_by(Customer.created_at.desc(), Customer.id)
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()
