from functools import lru_cache
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.cache.backend import CacheBackend
from ticketing.core.config import Settings, get_settings
from ticketing.core.observability import ObservabilityMetrics
from ticketing.core.security import PasswordManager
from ticketing.db.dependencies import get_session
from ticketing.models.user import User
from ticketing.services.auth import (
    AuthService,
    AuthServiceError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    InvalidTokenError,
    PasswordUnchangedError,
)
from ticketing.services.background_job import BackgroundJobService
from ticketing.services.customer import CustomerService
from ticketing.services.rate_limit import LoginRateLimiter
from ticketing.services.rbac import PermissionCode, PermissionDeniedError, RBACService
from ticketing.services.tenant import (
    CurrentTenant,
    InvitationConflictError,
    InvitationNotPendingError,
    InvitedUserNotFoundError,
    MembershipNotActiveError,
    OwnerCannotLeaveError,
    TenantNotFoundError,
    TenantService,
    TenantServiceError,
    TenantSlugConflictError,
)
from ticketing.services.ticket import TicketService
from ticketing.services.ticket_collaboration import TicketCollaborationService
from ticketing.storage.local import LocalAttachmentStorage

bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def get_password_manager() -> PasswordManager:
    return PasswordManager()


def get_cache(request: Request) -> CacheBackend:
    return cast(CacheBackend, request.app.state.cache)


def get_metrics(request: Request) -> ObservabilityMetrics:
    return cast(ObservabilityMetrics, request.app.state.metrics)


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    password_manager: Annotated[PasswordManager, Depends(get_password_manager)],
) -> AuthService:
    return AuthService(session, settings, password_manager)


def get_tenant_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TenantService:
    return TenantService(
        session,
        cache,
        permission_cache_ttl_seconds=settings.permission_cache_ttl_seconds,
        permission_cache_ttl_jitter_seconds=settings.permission_cache_ttl_jitter_seconds,
    )


def get_rbac_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RBACService:
    return RBACService(
        session,
        cache,
        cache_ttl_seconds=settings.permission_cache_ttl_seconds,
        cache_ttl_jitter_seconds=settings.permission_cache_ttl_jitter_seconds,
    )


def get_customer_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CustomerService:
    return CustomerService(session)


def get_ticket_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TicketService:
    return TicketService(
        session,
        cache,
        permission_cache_ttl_seconds=settings.permission_cache_ttl_seconds,
        permission_cache_ttl_jitter_seconds=settings.permission_cache_ttl_jitter_seconds,
    )


def get_ticket_collaboration_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
) -> TicketCollaborationService:
    return TicketCollaborationService(
        session,
        LocalAttachmentStorage(settings.attachment_storage_path),
        max_attachment_size_bytes=settings.attachment_max_size_bytes,
        cache=cache,
        permission_cache_ttl_seconds=settings.permission_cache_ttl_seconds,
        permission_cache_ttl_jitter_seconds=settings.permission_cache_ttl_jitter_seconds,
    )


def get_background_job_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
) -> BackgroundJobService:
    return BackgroundJobService(session, settings, cache)


def get_login_rate_limiter(
    cache: Annotated[CacheBackend, Depends(get_cache)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginRateLimiter:
    return LoginRateLimiter(
        cache,
        limit=settings.login_rate_limit_requests,
        window_seconds=settings.login_rate_limit_window_seconds,
    )


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> User:
    if credentials is None:
        raise auth_http_exception(InvalidTokenError())
    try:
        return await service.authenticate_access_token(credentials.credentials)
    except AuthServiceError as exc:
        raise auth_http_exception(exc) from exc


def auth_http_exception(error: AuthServiceError) -> HTTPException:
    if isinstance(error, EmailAlreadyRegisteredError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "email_already_registered", "message": "Email is already registered"},
        )
    if isinstance(error, InvalidCurrentPasswordError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_current_password", "message": "Current password is invalid"},
        )
    if isinstance(error, PasswordUnchangedError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "password_unchanged", "message": "New password must be different"},
        )

    code = "invalid_credentials" if isinstance(error, InvalidCredentialsError) else "invalid_token"
    message = "Invalid email or password" if code == "invalid_credentials" else "Invalid token"
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_tenant(
    x_tenant_id: Annotated[UUID, Header()],
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> CurrentTenant:
    try:
        return await service.resolve_current(user, x_tenant_id)
    except TenantServiceError as exc:
        raise tenant_http_exception(exc) from exc


def tenant_http_exception(error: TenantServiceError) -> HTTPException:
    if isinstance(error, (TenantNotFoundError, MembershipNotActiveError)):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "tenant_not_found", "message": "Tenant not found"},
        )
    if isinstance(error, InvitedUserNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "user_not_found", "message": "Invited user not found"},
        )
    if isinstance(error, TenantSlugConflictError):
        code, message = "tenant_slug_conflict", "Tenant slug already exists"
    elif isinstance(error, InvitationConflictError):
        code, message = "invitation_conflict", "User already has a tenant membership"
    elif isinstance(error, InvitationNotPendingError):
        code, message = "invitation_not_pending", "Invitation is not pending"
    elif isinstance(error, OwnerCannotLeaveError):
        code, message = "owner_cannot_leave", "Tenant owner cannot leave"
    else:
        code, message = "tenant_conflict", "Tenant operation conflicts with current state"
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
    )


async def get_member_reader_tenant(
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[RBACService, Depends(get_rbac_service)],
) -> CurrentTenant:
    return await _require_permission(context, service, PermissionCode.MEMBERS_READ)


async def get_member_inviter_tenant(
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[RBACService, Depends(get_rbac_service)],
) -> CurrentTenant:
    return await _require_permission(context, service, PermissionCode.MEMBERS_INVITE)


async def get_role_manager_tenant(
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[RBACService, Depends(get_rbac_service)],
) -> CurrentTenant:
    return await _require_permission(context, service, PermissionCode.ROLES_MANAGE)


async def get_customer_reader_tenant(
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[RBACService, Depends(get_rbac_service)],
) -> CurrentTenant:
    return await _require_permission(context, service, PermissionCode.CUSTOMERS_READ)


async def get_customer_writer_tenant(
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[RBACService, Depends(get_rbac_service)],
) -> CurrentTenant:
    return await _require_permission(context, service, PermissionCode.CUSTOMERS_WRITE)


async def _require_permission(
    context: CurrentTenant,
    service: RBACService,
    permission: PermissionCode,
) -> CurrentTenant:
    try:
        await service.require(context.membership, permission)
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "permission_denied", "message": "Permission denied"},
        ) from exc
    return context


def ensure_path_tenant(tenant_id: UUID, context: CurrentTenant) -> None:
    if context.tenant.id != tenant_id:
        raise tenant_http_exception(TenantNotFoundError())
