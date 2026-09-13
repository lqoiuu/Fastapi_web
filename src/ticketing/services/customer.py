from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.models.customer import Customer
from ticketing.repositories.customer import CustomerRepository
from ticketing.services.tenant import CurrentTenant


class CustomerServiceError(Exception):
    """Base class for expected customer-domain failures."""


class CustomerNotFoundError(CustomerServiceError):
    pass


class CustomerEmailConflictError(CustomerServiceError):
    pass


@dataclass(frozen=True, slots=True)
class CustomerChanges:
    fields: frozenset[str]
    name: str | None
    email: str | None
    phone: str | None
    is_active: bool | None


class CustomerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._customers = CustomerRepository(session)

    async def create(
        self,
        context: CurrentTenant,
        *,
        name: str,
        email: str,
        phone: str | None,
    ) -> Customer:
        normalized_email = email.strip().lower()
        if await self._customers.get_by_email(context.tenant.id, normalized_email) is not None:
            raise CustomerEmailConflictError

        customer = Customer(
            id=uuid4(),
            tenant_id=context.tenant.id,
            name=name.strip(),
            email=normalized_email,
            phone=self._normalize_phone(phone),
        )
        self._customers.add(customer)
        await self._commit_or_email_conflict()
        await self._session.refresh(customer)
        return customer

    async def get(self, context: CurrentTenant, customer_id: UUID) -> Customer:
        customer = await self._customers.get_by_id(context.tenant.id, customer_id)
        if customer is None:
            raise CustomerNotFoundError
        return customer

    async def list(self, context: CurrentTenant, *, offset: int, limit: int) -> Sequence[Customer]:
        return await self._customers.list_for_tenant(
            context.tenant.id,
            offset=offset,
            limit=limit,
        )

    async def update(
        self,
        context: CurrentTenant,
        customer_id: UUID,
        changes: CustomerChanges,
    ) -> Customer:
        customer = await self.get(context, customer_id)
        if "email" in changes.fields:
            assert changes.email is not None
            normalized_email = changes.email.strip().lower()
            existing = await self._customers.get_by_email(context.tenant.id, normalized_email)
            if existing is not None and existing.id != customer.id:
                raise CustomerEmailConflictError
            customer.email = normalized_email
        if "name" in changes.fields:
            assert changes.name is not None
            customer.name = changes.name.strip()
        if "phone" in changes.fields:
            customer.phone = self._normalize_phone(changes.phone)
        if "is_active" in changes.fields:
            assert changes.is_active is not None
            customer.is_active = changes.is_active

        await self._commit_or_email_conflict()
        await self._session.refresh(customer)
        return customer

    async def _commit_or_email_conflict(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise CustomerEmailConflictError from exc

    @staticmethod
    def _normalize_phone(phone: str | None) -> str | None:
        if phone is None:
            return None
        normalized = phone.strip()
        return normalized or None
