from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.cache.backend import CacheBackend
from ticketing.models.membership import Membership, MembershipStatus
from ticketing.models.tenant import Tenant
from ticketing.models.user import User
from ticketing.repositories.membership import MembershipRepository
from ticketing.repositories.tenant import TenantRepository
from ticketing.repositories.user import UserRepository
from ticketing.services.rbac import RBACService


class TenantServiceError(Exception):
    """Base class for expected tenant-domain failures."""


class TenantNotFoundError(TenantServiceError):
    pass


class TenantSlugConflictError(TenantServiceError):
    pass


class InvitationConflictError(TenantServiceError):
    pass


class InvitationNotPendingError(TenantServiceError):
    pass


class MembershipNotActiveError(TenantServiceError):
    pass


class OwnerCannotLeaveError(TenantServiceError):
    pass


class InvitedUserNotFoundError(TenantServiceError):
    pass


@dataclass(frozen=True, slots=True)
class TenantSummary:
    tenant: Tenant
    membership_status: MembershipStatus


@dataclass(frozen=True, slots=True)
class MemberSummary:
    membership: Membership
    user: User


@dataclass(frozen=True, slots=True)
class CurrentTenant:
    tenant: Tenant
    membership: Membership


class TenantService:
    """Own tenant and membership lifecycle rules and transaction boundaries."""

    def __init__(
        self,
        session: AsyncSession,
        cache: CacheBackend | None = None,
        *,
        permission_cache_ttl_seconds: int = 300,
        permission_cache_ttl_jitter_seconds: int = 30,
    ) -> None:
        self._session = session
        self._tenants = TenantRepository(session)
        self._memberships = MembershipRepository(session)
        self._users = UserRepository(session)
        self._rbac = RBACService(
            session,
            cache,
            cache_ttl_seconds=permission_cache_ttl_seconds,
            cache_ttl_jitter_seconds=permission_cache_ttl_jitter_seconds,
        )

    async def create(self, owner: User, name: str, slug: str) -> CurrentTenant:
        normalized_slug = slug.strip().lower()
        if await self._tenants.get_by_slug(normalized_slug) is not None:
            raise TenantSlugConflictError

        now = datetime.now(UTC)
        tenant = Tenant(
            id=uuid4(),
            name=name.strip(),
            slug=normalized_slug,
            owner_user_id=owner.id,
        )
        self._tenants.add(tenant)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise TenantSlugConflictError from exc

        membership = Membership(
            id=uuid4(),
            tenant_id=tenant.id,
            user_id=owner.id,
            status=MembershipStatus.ACTIVE,
            joined_at=now,
        )
        self._memberships.add(membership)
        await self._session.flush()
        await self._rbac.provision_defaults(tenant, membership)
        await self._session.commit()
        await self._session.refresh(tenant)
        await self._session.refresh(membership)
        return CurrentTenant(tenant=tenant, membership=membership)

    async def list_for_user(self, user: User) -> list[TenantSummary]:
        memberships = await self._memberships.list_for_user(user.id)
        tenants = await self._tenants.get_many([item.tenant_id for item in memberships])
        tenant_by_id = {tenant.id: tenant for tenant in tenants if tenant.is_active}
        return [
            TenantSummary(tenant=tenant_by_id[item.tenant_id], membership_status=item.status)
            for item in memberships
            if item.tenant_id in tenant_by_id
        ]

    async def invite(self, context: CurrentTenant, email: str) -> Membership:
        tenant = context.tenant
        target_user = await self._users.get_by_email(email.strip().lower())
        if target_user is None:
            raise InvitedUserNotFoundError

        membership = await self._memberships.get_for_user_and_tenant(
            target_user.id,
            tenant.id,
            for_update=True,
        )
        if membership is None:
            membership = Membership(
                id=uuid4(),
                tenant_id=tenant.id,
                user_id=target_user.id,
                status=MembershipStatus.INVITED,
            )
            self._memberships.add(membership)
        elif membership.status == MembershipStatus.LEFT:
            membership.status = MembershipStatus.INVITED
            membership.joined_at = None
        else:
            raise InvitationConflictError

        await self._session.flush()
        await self._rbac.reset_to_requester(tenant.id, membership)
        await self._session.commit()
        await self._rbac.invalidate_permissions(tenant.id, membership.id)
        await self._session.refresh(membership)
        return membership

    async def accept_invitation(self, user: User, tenant_id: UUID) -> CurrentTenant:
        membership = await self._memberships.get_for_user_and_tenant(
            user.id,
            tenant_id,
            for_update=True,
        )
        if membership is None:
            raise TenantNotFoundError
        if membership.status != MembershipStatus.INVITED:
            raise InvitationNotPendingError

        tenant = await self._tenants.get_by_id(tenant_id)
        if tenant is None or not tenant.is_active:
            raise TenantNotFoundError
        membership.status = MembershipStatus.ACTIVE
        membership.joined_at = datetime.now(UTC)
        await self._session.commit()
        await self._rbac.invalidate_permissions(tenant_id, membership.id)
        await self._session.refresh(membership)
        return CurrentTenant(tenant=tenant, membership=membership)

    async def leave(self, user: User, tenant_id: UUID) -> None:
        membership = await self._memberships.get_for_user_and_tenant(
            user.id,
            tenant_id,
            for_update=True,
        )
        if membership is None or membership.status != MembershipStatus.ACTIVE:
            raise MembershipNotActiveError
        tenant = await self._tenants.get_by_id(tenant_id)
        if tenant is None or not tenant.is_active:
            raise TenantNotFoundError
        if tenant.owner_user_id == user.id:
            raise OwnerCannotLeaveError

        membership.status = MembershipStatus.LEFT
        await self._session.commit()
        await self._rbac.invalidate_permissions(tenant_id, membership.id)

    async def resolve_current(self, user: User, tenant_id: UUID) -> CurrentTenant:
        membership = await self._memberships.get_for_user_and_tenant(user.id, tenant_id)
        if membership is None or membership.status != MembershipStatus.ACTIVE:
            raise TenantNotFoundError
        tenant = await self._tenants.get_by_id(tenant_id)
        if tenant is None or not tenant.is_active:
            raise TenantNotFoundError
        return CurrentTenant(tenant=tenant, membership=membership)

    async def list_members(self, context: CurrentTenant) -> list[MemberSummary]:
        tenant = context.tenant
        memberships = await self._memberships.list_for_tenant(tenant.id)
        users = await self._users.get_many([item.user_id for item in memberships])
        user_by_id = {user.id: user for user in users}
        return [
            MemberSummary(membership=item, user=user_by_id[item.user_id])
            for item in memberships
            if item.user_id in user_by_id
        ]
