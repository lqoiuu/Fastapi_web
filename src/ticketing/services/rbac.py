import json
import random
from collections.abc import Sequence
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.cache.backend import CacheBackend
from ticketing.cache.keys import permission_snapshot_key
from ticketing.models.membership import Membership
from ticketing.models.rbac import MembershipRole, Permission, Role, RolePermission
from ticketing.models.tenant import Tenant


class PermissionCode(StrEnum):
    MEMBERS_READ = "members.read"
    MEMBERS_INVITE = "members.invite"
    ROLES_MANAGE = "roles.manage"
    CUSTOMERS_READ = "customers.read"
    CUSTOMERS_WRITE = "customers.write"
    TICKETS_READ_ALL = "tickets.read.all"
    TICKETS_UPDATE_ALL = "tickets.update.all"
    TICKETS_MANAGE_WORKFLOW = "tickets.manage.workflow"
    TICKETS_COMMENT_INTERNAL = "tickets.comment.internal"


class PermissionDeniedError(Exception):
    pass


class RoleNotFoundError(Exception):
    pass


class OwnerAdminRoleRequiredError(Exception):
    pass


class RBACService:
    def __init__(
        self,
        session: AsyncSession,
        cache: CacheBackend | None = None,
        *,
        cache_ttl_seconds: int = 300,
        cache_ttl_jitter_seconds: int = 30,
    ) -> None:
        self._session = session
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_ttl_jitter_seconds = cache_ttl_jitter_seconds

    async def provision_defaults(self, tenant: Tenant, owner_membership: Membership) -> None:
        permissions = await self._permissions_by_code()
        roles = {
            "tenant_admin": Role(id=uuid4(), tenant_id=tenant.id, name="tenant_admin"),
            "agent": Role(id=uuid4(), tenant_id=tenant.id, name="agent"),
            "requester": Role(id=uuid4(), tenant_id=tenant.id, name="requester"),
        }
        self._session.add_all(roles.values())
        await self._session.flush()
        for code in PermissionCode:
            self._session.add(
                RolePermission(
                    id=uuid4(),
                    role_id=roles["tenant_admin"].id,
                    permission_id=permissions[code].id,
                )
            )
        for code in (
            PermissionCode.MEMBERS_READ,
            PermissionCode.CUSTOMERS_READ,
            PermissionCode.CUSTOMERS_WRITE,
            PermissionCode.TICKETS_READ_ALL,
            PermissionCode.TICKETS_UPDATE_ALL,
            PermissionCode.TICKETS_MANAGE_WORKFLOW,
            PermissionCode.TICKETS_COMMENT_INTERNAL,
        ):
            self._session.add(
                RolePermission(
                    id=uuid4(),
                    role_id=roles["agent"].id,
                    permission_id=permissions[code].id,
                )
            )
        self._session.add(
            MembershipRole(
                id=uuid4(), membership_id=owner_membership.id, role_id=roles["tenant_admin"].id
            )
        )

    async def reset_to_requester(self, tenant_id: UUID, membership: Membership) -> None:
        role = await self._role_by_name(tenant_id, "requester")
        if role is None:
            raise RoleNotFoundError
        await self._session.execute(
            delete(MembershipRole).where(MembershipRole.membership_id == membership.id)
        )
        self._session.add(MembershipRole(id=uuid4(), membership_id=membership.id, role_id=role.id))

    async def require(self, membership: Membership, permission: PermissionCode) -> None:
        if not await self.has_permission(membership, permission):
            raise PermissionDeniedError

    async def has_permission(self, membership: Membership, permission: PermissionCode) -> bool:
        return permission in await self._membership_permissions(membership)

    async def _membership_permissions(
        self,
        membership: Membership,
    ) -> frozenset[PermissionCode]:
        cache_key = permission_snapshot_key(membership.tenant_id, membership.id)
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                try:
                    payload: object = json.loads(cached)
                    if not isinstance(payload, list) or not all(
                        isinstance(item, str) for item in payload
                    ):
                        raise ValueError("Invalid permission cache payload")
                    return frozenset(PermissionCode(item) for item in payload)
                except ValueError:
                    await self._cache.delete(cache_key)

        statement = (
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(MembershipRole, MembershipRole.role_id == RolePermission.role_id)
            .join(Role, Role.id == MembershipRole.role_id)
            .where(
                MembershipRole.membership_id == membership.id,
                Role.tenant_id == membership.tenant_id,
            )
        )
        values = (await self._session.execute(statement)).scalars().all()
        permissions = frozenset(PermissionCode(value) for value in values)
        if self._cache is not None:
            ttl_seconds = self._cache_ttl_seconds + random.randint(
                0,
                self._cache_ttl_jitter_seconds,
            )
            await self._cache.set(
                cache_key,
                json.dumps(sorted(item.value for item in permissions), separators=(",", ":")),
                ttl_seconds=ttl_seconds,
            )
        return permissions

    async def invalidate_permissions(self, tenant_id: UUID, membership_id: UUID) -> None:
        if self._cache is not None:
            await self._cache.delete(permission_snapshot_key(tenant_id, membership_id))

    async def list_roles(self, tenant_id: UUID) -> Sequence[Role]:
        result = await self._session.execute(
            select(Role).where(Role.tenant_id == tenant_id).order_by(Role.name)
        )
        return result.scalars().all()

    async def permission_codes(self, role_id: UUID) -> list[str]:
        result = await self._session.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
            .order_by(Permission.code)
        )
        return list(result.scalars().all())

    async def assign_roles(
        self, tenant_id: UUID, membership_id: UUID, role_ids: Sequence[UUID]
    ) -> None:
        tenant = await self._session.get(Tenant, tenant_id)
        membership = await self._session.get(Membership, membership_id)
        if tenant is None or membership is None or membership.tenant_id != tenant_id:
            raise RoleNotFoundError
        result = await self._session.execute(
            select(Role).where(Role.tenant_id == tenant_id, Role.id.in_(role_ids))
        )
        roles = result.scalars().all()
        if len(roles) != len(set(role_ids)):
            raise RoleNotFoundError
        if membership.user_id == tenant.owner_user_id and not any(
            role.name == "tenant_admin" for role in roles
        ):
            raise OwnerAdminRoleRequiredError
        await self._session.execute(
            delete(MembershipRole).where(MembershipRole.membership_id == membership_id)
        )
        self._session.add_all(
            [
                MembershipRole(id=uuid4(), membership_id=membership_id, role_id=role.id)
                for role in roles
            ]
        )
        await self._session.commit()
        await self.invalidate_permissions(tenant_id, membership_id)

    async def _permissions_by_code(self) -> dict[PermissionCode, Permission]:
        result = await self._session.execute(select(Permission))
        by_code = {PermissionCode(item.code): item for item in result.scalars().all()}
        if set(by_code) != set(PermissionCode):
            raise RuntimeError("Required permissions have not been migrated")
        return by_code

    async def _role_by_name(self, tenant_id: UUID, name: str) -> Role | None:
        result = await self._session.execute(
            select(Role).where(Role.tenant_id == tenant_id, Role.name == name)
        )
        return result.scalar_one_or_none()
