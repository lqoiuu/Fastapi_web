from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from ticketing.api.dependencies import (
    ensure_path_tenant,
    get_current_tenant,
    get_ticket_collaboration_service,
)
from ticketing.schemas.ticket_collaboration import (
    TicketAttachmentResponse,
    TicketCommentCreateRequest,
    TicketCommentResponse,
)
from ticketing.services.tenant import CurrentTenant
from ticketing.services.ticket import TicketNotFoundError
from ticketing.services.ticket_collaboration import (
    AttachmentContentTypeInvalidError,
    AttachmentEmptyError,
    AttachmentFilenameInvalidError,
    AttachmentNotFoundError,
    AttachmentTooLargeError,
    InternalCommentForbiddenError,
    TicketCollaborationError,
    TicketCollaborationService,
)

router = APIRouter(tags=["ticket collaboration"])


@router.post(
    "/tenants/{tenant_id}/tickets/{ticket_id}/comments",
    response_model=TicketCommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ticket_comment(
    tenant_id: UUID,
    ticket_id: UUID,
    request: TicketCommentCreateRequest,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketCollaborationService, Depends(get_ticket_collaboration_service)],
) -> TicketCommentResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        comment = await service.create_comment(
            context,
            ticket_id,
            body=request.body,
            visibility=request.visibility,
        )
    except (TicketNotFoundError, TicketCollaborationError) as exc:
        raise collaboration_http_exception(exc) from exc
    return TicketCommentResponse.model_validate(comment)


@router.get(
    "/tenants/{tenant_id}/tickets/{ticket_id}/comments",
    response_model=list[TicketCommentResponse],
)
async def list_ticket_comments(
    tenant_id: UUID,
    ticket_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketCollaborationService, Depends(get_ticket_collaboration_service)],
) -> list[TicketCommentResponse]:
    ensure_path_tenant(tenant_id, context)
    try:
        comments = await service.list_comments(context, ticket_id)
    except TicketNotFoundError as exc:
        raise collaboration_http_exception(exc) from exc
    return [TicketCommentResponse.model_validate(comment) for comment in comments]


@router.post(
    "/tenants/{tenant_id}/tickets/{ticket_id}/attachments",
    response_model=TicketAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_ticket_attachment(
    tenant_id: UUID,
    ticket_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketCollaborationService, Depends(get_ticket_collaboration_service)],
    file: Annotated[UploadFile, File()],
) -> TicketAttachmentResponse:
    ensure_path_tenant(tenant_id, context)

    async def chunks() -> AsyncIterator[bytes]:
        while chunk := await file.read(64 * 1024):
            yield chunk

    try:
        attachment = await service.upload_attachment(
            context,
            ticket_id,
            filename=file.filename,
            content_type=file.content_type,
            chunks=chunks(),
        )
    except (TicketNotFoundError, TicketCollaborationError) as exc:
        raise collaboration_http_exception(exc) from exc
    finally:
        await file.close()
    return TicketAttachmentResponse.model_validate(attachment)


@router.get(
    "/tenants/{tenant_id}/tickets/{ticket_id}/attachments",
    response_model=list[TicketAttachmentResponse],
)
async def list_ticket_attachments(
    tenant_id: UUID,
    ticket_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketCollaborationService, Depends(get_ticket_collaboration_service)],
) -> list[TicketAttachmentResponse]:
    ensure_path_tenant(tenant_id, context)
    try:
        attachments = await service.list_attachments(context, ticket_id)
    except TicketNotFoundError as exc:
        raise collaboration_http_exception(exc) from exc
    return [TicketAttachmentResponse.model_validate(attachment) for attachment in attachments]


@router.get("/tenants/{tenant_id}/tickets/{ticket_id}/attachments/{attachment_id}")
async def download_ticket_attachment(
    tenant_id: UUID,
    ticket_id: UUID,
    attachment_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketCollaborationService, Depends(get_ticket_collaboration_service)],
) -> StreamingResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        download = await service.download_attachment(context, ticket_id, attachment_id)
    except (TicketNotFoundError, TicketCollaborationError) as exc:
        raise collaboration_http_exception(exc) from exc
    encoded_filename = quote(download.attachment.original_filename, safe="")
    return StreamingResponse(
        download.content,
        media_type=download.attachment.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


def collaboration_http_exception(
    error: TicketNotFoundError | TicketCollaborationError,
) -> HTTPException:
    if isinstance(error, TicketNotFoundError):
        code, message, status_code = (
            "ticket_not_found",
            "Ticket not found",
            status.HTTP_404_NOT_FOUND,
        )
    elif isinstance(error, AttachmentNotFoundError):
        code, message, status_code = (
            "attachment_not_found",
            "Attachment not found",
            status.HTTP_404_NOT_FOUND,
        )
    elif isinstance(error, InternalCommentForbiddenError):
        code, message, status_code = (
            "permission_denied",
            "Permission denied",
            status.HTTP_403_FORBIDDEN,
        )
    elif isinstance(error, AttachmentTooLargeError):
        code, message, status_code = (
            "attachment_too_large",
            "Attachment exceeds the configured size limit",
            status.HTTP_413_CONTENT_TOO_LARGE,
        )
    elif isinstance(error, AttachmentContentTypeInvalidError):
        code, message, status_code = (
            "attachment_content_type_invalid",
            "Attachment extension, media type, or content does not match",
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    elif isinstance(error, AttachmentFilenameInvalidError):
        code, message, status_code = (
            "attachment_filename_invalid",
            "Attachment filename is invalid",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    elif isinstance(error, AttachmentEmptyError):
        code, message, status_code = (
            "attachment_empty",
            "Attachment must not be empty",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    else:
        code, message, status_code = (
            "ticket_collaboration_conflict",
            "Ticket collaboration operation failed",
            status.HTTP_409_CONFLICT,
        )
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
