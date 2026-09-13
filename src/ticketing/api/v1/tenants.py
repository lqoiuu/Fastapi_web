from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ticketing.api.dependencies import (
    ensure_path_tenant,
    get_current_tenant,
    get_current_user,
    get_member_inviter_tenant,
    get_member_reader_tenant,
    get_rbac_service,
    get_role_manager_tenant,
    get_tenant_service,
    tenant_http_exception,
)
from ticketing.models.user import User
from ticketing.schemas.tenant import (
    CurrentTenantResponse,
    MembershipResponse,
    RoleAssignmentRequest,
    RoleResponse,
    TenantCreateRequest,
    TenantInvitationRequest,
    TenantListItem,
    TenantMemberResponse,
    TenantResponse,
)
from ticketing.services.rbac import OwnerAdminRoleRequiredError, RBACService, RoleNotFoundError
from ticketing.services.tenant import (
    CurrentTenant,
    TenantNotFoundError,
    TenantService,
    TenantServiceError,
)

router = APIRouter(tags=["tenants"])


@router.post("/tenants", response_model=CurrentTenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    request: TenantCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> CurrentTenantResponse:
    try:
        context = await service.create(user, request.name, request.slug)
    except TenantServiceError as exc:
        raise tenant_http_exception(exc) from exc
    return current_tenant_response(context)


@router.get("/tenants", response_model=list[TenantListItem])
async def list_tenants(
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> list[TenantListItem]:
    summaries = await service.list_for_user(user)
    return [
        TenantListItem(
            tenant=TenantResponse.model_validate(item.tenant),
            membership_status=item.membership_status,
        )
        for item in summaries
    ]


@router.post(
    "/tenants/{tenant_id}/invitations",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    tenant_id: UUID,
    request: TenantInvitationRequest,
    context: Annotated[CurrentTenant, Depends(get_member_inviter_tenant)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> MembershipResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        membership = await service.invite(context, request.email)
    except TenantServiceError as exc:
        raise tenant_http_exception(exc) from exc
    return MembershipResponse.model_validate(membership)


@router.get("/tenants/{tenant_id}/members", response_model=list[TenantMemberResponse])
async def list_members(
    tenant_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_member_reader_tenant)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> list[TenantMemberResponse]:
    ensure_path_tenant(tenant_id, context)
    try:
        members = await service.list_members(context)
    except TenantServiceError as exc:
        raise tenant_http_exception(exc) from exc
    return [
        TenantMemberResponse(
            membership_id=item.membership.id,
            user_id=item.user.id,
            email=item.user.email,
            status=item.membership.status,
            joined_at=item.membership.joined_at,
        )
        for item in members
    ]


@router.post(
    "/tenants/{tenant_id}/membership/accept",
    response_model=CurrentTenantResponse,
)
async def accept_invitation(
    tenant_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> CurrentTenantResponse:
    try:
        context = await service.accept_invitation(user, tenant_id)
    except TenantServiceError as exc:
        raise tenant_http_exception(exc) from exc
    return current_tenant_response(context)


@router.post(
    "/tenants/{tenant_id}/membership/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def leave_tenant(
    tenant_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TenantService, Depends(get_tenant_service)],
) -> Response:
    try:
        await service.leave(user, tenant_id)
    except TenantServiceError as exc:
        raise tenant_http_exception(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tenant-context", response_model=CurrentTenantResponse)
async def read_current_tenant(
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
) -> CurrentTenantResponse:
    return current_tenant_response(context)


def current_tenant_response(context: CurrentTenant) -> CurrentTenantResponse:
    return CurrentTenantResponse(
        tenant=TenantResponse.model_validate(context.tenant),
        membership_id=context.membership.id,
    )


@router.get("/tenants/{tenant_id}/roles", response_model=list[RoleResponse])
async def list_roles(
    tenant_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_role_manager_tenant)],
    service: Annotated[RBACService, Depends(get_rbac_service)],
) -> list[RoleResponse]:
    ensure_path_tenant(tenant_id, context)
    roles = await service.list_roles(tenant_id)
    return [
        RoleResponse(
            id=role.id,
            name=role.name,
            is_system=role.is_system,
            permissions=await service.permission_codes(role.id),
        )
        for role in roles
    ]


@router.put(
    "/tenants/{tenant_id}/members/{membership_id}/roles",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def assign_member_roles(
    tenant_id: UUID,
    membership_id: UUID,
    request: RoleAssignmentRequest,
    context: Annotated[CurrentTenant, Depends(get_role_manager_tenant)],
    service: Annotated[RBACService, Depends(get_rbac_service)],
) -> Response:
    ensure_path_tenant(tenant_id, context)
    try:
        await service.assign_roles(tenant_id, membership_id, request.role_ids)
    except OwnerAdminRoleRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "owner_admin_role_required",
                "message": "Tenant owner must retain the tenant_admin role",
            },
        ) from exc
    except RoleNotFoundError as exc:
        raise tenant_http_exception(TenantNotFoundError()) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
