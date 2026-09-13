from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ticketing.models.ticket_collaboration import CommentVisibility


class TicketCommentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    body: str = Field(min_length=1, max_length=5_000)
    visibility: CommentVisibility = CommentVisibility.PUBLIC


class TicketCommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    tenant_id: UUID
    ticket_id: UUID
    author_membership_id: UUID
    visibility: CommentVisibility
    body: str
    created_at: datetime


class TicketAttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    tenant_id: UUID
    ticket_id: UUID
    uploader_membership_id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
