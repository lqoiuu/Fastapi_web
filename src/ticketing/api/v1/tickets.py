from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from ticketing.api.dependencies import ensure_path_tenant, get_current_tenant, get_ticket_service
from ticketing.schemas.ticket import (
    TicketActionRequest,
    TicketCreateRequest,
    TicketEventResponse,
    TicketListQuery,
    TicketPageResponse,
    TicketResponse,
    TicketTransferRequest,
    TicketUpdateRequest,
)
from ticketing.services.customer import CustomerNotFoundError
from ticketing.services.tenant import CurrentTenant
from ticketing.services.ticket import (
    IdempotencyKeyConflictError,
    TicketActionForbiddenError,
    TicketAssigneeNotFoundError,
    TicketAssignmentConflictError,
    TicketChanges,
    TicketListOptions,
    TicketNotFoundError,
    TicketService,
    TicketServiceError,
    TicketTransitionConflictError,
)
from ticketing.services.ticket_cursor import InvalidTicketCursorError

router = APIRouter(tags=["tickets"])


@router.post(
    "/tenants/{tenant_id}/tickets",
    response_model=TicketResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ticket(
    tenant_id: UUID,
    request: TicketCreateRequest,
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
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        result = await service.create(
            context,
            customer_id=request.customer_id,
            subject=request.subject,
            description=request.description,
            priority=request.priority,
            idempotency_key=idempotency_key,
        )
    except (TicketServiceError, CustomerNotFoundError) as exc:
        raise ticket_http_exception(exc) from exc
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return TicketResponse.model_validate(result.ticket)


@router.get("/tenants/{tenant_id}/tickets", response_model=TicketPageResponse)
async def list_tickets(
    tenant_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketService, Depends(get_ticket_service)],
    query: Annotated[TicketListQuery, Query()],
) -> TicketPageResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        page = await service.list(
            context,
            TicketListOptions(
                status=query.status,
                assignee_membership_id=query.assignee_membership_id,
                priority=query.priority,
                created_from=query.created_from,
                created_to=query.created_to,
                keyword=query.q,
                cursor=query.cursor,
                limit=query.limit,
            ),
        )
    except InvalidTicketCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_cursor", "message": "Invalid ticket cursor"},
        ) from exc
    return TicketPageResponse(
        items=[TicketResponse.model_validate(ticket) for ticket in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/tenants/{tenant_id}/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    tenant_id: UUID,
    ticket_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        ticket = await service.get(context, ticket_id)
    except TicketServiceError as exc:
        raise ticket_http_exception(exc) from exc
    return TicketResponse.model_validate(ticket)


@router.patch("/tenants/{tenant_id}/tickets/{ticket_id}", response_model=TicketResponse)
async def update_ticket(
    tenant_id: UUID,
    ticket_id: UUID,
    request: TicketUpdateRequest,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    ensure_path_tenant(tenant_id, context)
    changes = TicketChanges(
        fields=frozenset(request.model_fields_set),
        subject=request.subject,
        description=request.description,
        priority=request.priority,
    )
    try:
        ticket = await service.update(context, ticket_id, changes)
    except TicketServiceError as exc:
        raise ticket_http_exception(exc) from exc
    return TicketResponse.model_validate(ticket)


@router.get(
    "/tenants/{tenant_id}/tickets/{ticket_id}/events",
    response_model=list[TicketEventResponse],
)
async def list_ticket_events(
    tenant_id: UUID,
    ticket_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> list[TicketEventResponse]:
    ensure_path_tenant(tenant_id, context)
    try:
        events = await service.list_events(context, ticket_id)
    except TicketServiceError as exc:
        raise ticket_http_exception(exc) from exc
    return [TicketEventResponse.model_validate(event) for event in events]


@router.post(
    "/tenants/{tenant_id}/tickets/{ticket_id}/actions/claim",
    response_model=TicketResponse,
)
async def claim_ticket(
    tenant_id: UUID,
    ticket_id: UUID,
    request: TicketActionRequest,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        ticket = await service.claim(context, ticket_id, note=request.note)
    except TicketServiceError as exc:
        raise ticket_http_exception(exc) from exc
    return TicketResponse.model_validate(ticket)


@router.post(
    "/tenants/{tenant_id}/tickets/{ticket_id}/actions/transfer",
    response_model=TicketResponse,
)
async def transfer_ticket(
    tenant_id: UUID,
    ticket_id: UUID,
    request: TicketTransferRequest,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        ticket = await service.transfer(
            context,
            ticket_id,
            assignee_membership_id=request.assignee_membership_id,
            note=request.note,
        )
    except TicketServiceError as exc:
        raise ticket_http_exception(exc) from exc
    return TicketResponse.model_validate(ticket)


@router.post(
    "/tenants/{tenant_id}/tickets/{ticket_id}/actions/resolve",
    response_model=TicketResponse,
)
async def resolve_ticket(
    tenant_id: UUID,
    ticket_id: UUID,
    request: TicketActionRequest,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        ticket = await service.resolve(context, ticket_id, note=request.note)
    except TicketServiceError as exc:
        raise ticket_http_exception(exc) from exc
    return TicketResponse.model_validate(ticket)


@router.post(
    "/tenants/{tenant_id}/tickets/{ticket_id}/actions/reopen",
    response_model=TicketResponse,
)
async def reopen_ticket(
    tenant_id: UUID,
    ticket_id: UUID,
    request: TicketActionRequest,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        ticket = await service.reopen(context, ticket_id, note=request.note)
    except TicketServiceError as exc:
        raise ticket_http_exception(exc) from exc
    return TicketResponse.model_validate(ticket)


@router.post(
    "/tenants/{tenant_id}/tickets/{ticket_id}/actions/close",
    response_model=TicketResponse,
)
async def close_ticket(
    tenant_id: UUID,
    ticket_id: UUID,
    request: TicketActionRequest,
    context: Annotated[CurrentTenant, Depends(get_current_tenant)],
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        ticket = await service.close(context, ticket_id, note=request.note)
    except TicketServiceError as exc:
        raise ticket_http_exception(exc) from exc
    return TicketResponse.model_validate(ticket)


def ticket_http_exception(error: TicketServiceError | CustomerNotFoundError) -> HTTPException:
    if isinstance(error, CustomerNotFoundError):
        code, message = "customer_not_found", "Customer not found"
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, TicketNotFoundError):
        code, message = "ticket_not_found", "Ticket not found"
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, TicketAssigneeNotFoundError):
        code, message = "assignee_not_found", "Assignee not found"
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, TicketActionForbiddenError):
        code, message = "permission_denied", "Permission denied"
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, TicketTransitionConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_ticket_transition",
                "message": str(error),
                "action": error.action.value,
                "current_status": error.current_status.value,
            },
        )
    elif isinstance(error, TicketAssignmentConflictError):
        code, message = "ticket_assignment_conflict", "Ticket assignment conflicts"
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, IdempotencyKeyConflictError):
        code, message = (
            "idempotency_key_conflict",
            "Idempotency key was already used with a different request",
        )
        status_code = status.HTTP_409_CONFLICT
    else:
        code, message = "ticket_conflict", "Ticket operation conflicts"
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )
