from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ticketing.models.ticket import TicketPriority, TicketStatus
from ticketing.models.ticket_event import TicketEventType


class TicketCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer_id: UUID | None = None
    subject: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=1, max_length=10_000)
    priority: TicketPriority = TicketPriority.NORMAL


class TicketUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=10_000)
    priority: TicketPriority | None = None

    @model_validator(mode="after")
    def require_update_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    tenant_id: UUID
    customer_id: UUID | None
    created_by_membership_id: UUID
    assignee_membership_id: UUID | None
    subject: str
    description: str
    status: TicketStatus
    priority: TicketPriority
    created_at: datetime
    updated_at: datetime


class TicketActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: str | None = Field(default=None, min_length=1, max_length=2_000)


class TicketTransferRequest(TicketActionRequest):
    assignee_membership_id: UUID


class TicketEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    tenant_id: UUID
    ticket_id: UUID
    actor_membership_id: UUID
    event_type: TicketEventType
    from_status: TicketStatus | None
    to_status: TicketStatus
    from_assignee_membership_id: UUID | None
    to_assignee_membership_id: UUID | None
    note: str | None
    created_at: datetime


class TicketListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: TicketStatus | None = None
    assignee_membership_id: UUID | None = None
    priority: TicketPriority | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    q: str | None = Field(default=None, min_length=1, max_length=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=500)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        for value in (self.created_from, self.created_to):
            if value is not None and value.utcoffset() is None:
                raise ValueError("created_from and created_to must include a timezone")
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("created_from must not be after created_to")
        return self


class TicketPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[TicketResponse]
    next_cursor: str | None
