from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ticketing.models.background_job import BackgroundJobKind, BackgroundJobStatus
from ticketing.models.ticket import TicketPriority, TicketStatus


class EmailNotificationJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: UUID


class TicketExportJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: TicketStatus | None = None
    priority: TicketPriority | None = None


class BackgroundJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    tenant_id: UUID
    requested_by_membership_id: UUID
    kind: BackgroundJobKind
    status: BackgroundJobStatus
    attempt_count: int
    max_attempts: int
    result: dict[str, object] | None
    last_error_code: str | None
    last_error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
