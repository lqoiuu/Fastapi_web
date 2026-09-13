from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ticketing.models.membership import MembershipStatus


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    slug: str = Field(
        min_length=3,
        max_length=63,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )


class TenantInvitationRequest(BaseModel):
    email: EmailStr


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    name: str
    slug: str
    owner_user_id: UUID
    is_active: bool
    created_at: datetime


class TenantListItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant: TenantResponse
    membership_status: MembershipStatus


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    status: MembershipStatus
    joined_at: datetime | None


class TenantMemberResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    membership_id: UUID
    user_id: UUID
    email: EmailStr
    status: MembershipStatus
    joined_at: datetime | None


class CurrentTenantResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant: TenantResponse
    membership_id: UUID


class RoleAssignmentRequest(BaseModel):
    role_ids: list[UUID] = Field(min_length=1)


class RoleResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    is_system: bool
    permissions: list[str]
