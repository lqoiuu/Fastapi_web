from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import FileResponse

from ticketing.api.dependencies import (
    ensure_path_tenant,
    get_background_job_service,
    get_current_tenant,
)
from ticketing.schemas.background_job import (
    BackgroundJobResponse,
    EmailNotificationJobRequest,
    TicketExportJobRequest,
)
from ticketing.services.background_job import (
    BackgroundJobIdempotencyConflictError,
    BackgroundJobNotFoundError,
    BackgroundJobResultNotReadyError,
    BackgroundJobService,
    BackgroundJobServiceError,
    TicketNotificationRecipientNotFoundError,
)
from ticketing.services.tenant import CurrentTenant
from ticketing.services.ticket import TicketNotFoundError

router = APIRouter(tags=["background jobs"])


@router.post(
    "/tenants/{tenant_id}/background-jobs/email-notifications",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_email_notification(
    tenant_id: UUID,
    request: EmailNotificationJobRequest,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ],
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[BackgroundJobService, Depends(get_background_job_service)],
) -> BackgroundJobResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        submission = await service.submit_email_notification(
            context,
            ticket_id=request.ticket_id,
            idempotency_key=idempotency_key,
        )
    except (BackgroundJobServiceError, TicketNotFoundError) as exc:
        raise background_job_http_exception(exc) from exc
    response.headers["Location"] = (
        f"/api/v1/tenants/{tenant_id}/background-jobs/{submission.job.id}"
    )
    if submission.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return BackgroundJobResponse.model_validate(submission.job)


@router.post(
    "/tenants/{tenant_id}/background-jobs/ticket-exports",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_ticket_export(
    tenant_id: UUID,
    request: TicketExportJobRequest,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ],
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[BackgroundJobService, Depends(get_background_job_service)],
) -> BackgroundJobResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        submission = await service.submit_ticket_export(
            context,
            status=request.status,
            priority=request.priority,
            idempotency_key=idempotency_key,
        )
    except BackgroundJobServiceError as exc:
        raise background_job_http_exception(exc) from exc
    response.headers["Location"] = (
        f"/api/v1/tenants/{tenant_id}/background-jobs/{submission.job.id}"
    )
    if submission.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return BackgroundJobResponse.model_validate(submission.job)


@router.get(
    "/tenants/{tenant_id}/background-jobs/{job_id}",
    response_model=BackgroundJobResponse,
)
async def get_background_job(
    tenant_id: UUID,
    job_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[BackgroundJobService, Depends(get_background_job_service)],
) -> BackgroundJobResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        job = await service.get(context, job_id)
    except BackgroundJobServiceError as exc:
        raise background_job_http_exception(exc) from exc
    return BackgroundJobResponse.model_validate(job)


@router.get("/tenants/{tenant_id}/background-jobs/{job_id}/result")
async def download_background_job_result(
    tenant_id: UUID,
    job_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[BackgroundJobService, Depends(get_background_job_service)],
) -> FileResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        result_path = await service.export_result_path(context, job_id)
    except BackgroundJobServiceError as exc:
        raise background_job_http_exception(exc) from exc
    return FileResponse(
        result_path,
        media_type="text/csv",
        filename=f"ticket-export-{job_id}.csv",
    )


def background_job_http_exception(
    error: BackgroundJobServiceError | TicketNotFoundError,
) -> HTTPException:
    if isinstance(error, TicketNotFoundError):
        code, message, status_code = (
            "ticket_not_found",
            "Ticket not found",
            status.HTTP_404_NOT_FOUND,
        )
    elif isinstance(error, BackgroundJobNotFoundError):
        code, message, status_code = (
            "background_job_not_found",
            "Background job not found",
            status.HTTP_404_NOT_FOUND,
        )
    elif isinstance(error, BackgroundJobIdempotencyConflictError):
        code, message, status_code = (
            "idempotency_key_conflict",
            "Idempotency key was already used with a different request",
            status.HTTP_409_CONFLICT,
        )
    elif isinstance(error, TicketNotificationRecipientNotFoundError):
        code, message, status_code = (
            "ticket_notification_recipient_not_found",
            "Ticket does not have an active customer email recipient",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    elif isinstance(error, BackgroundJobResultNotReadyError):
        code, message, status_code = (
            "background_job_result_not_ready",
            "Background job result is not ready",
            status.HTTP_409_CONFLICT,
        )
    else:
        code, message, status_code = (
            "background_job_conflict",
            "Background job operation conflicts with current state",
            status.HTTP_409_CONFLICT,
        )
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
