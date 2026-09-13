from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ticketing.api.dependencies import (
    ensure_path_tenant,
    get_customer_reader_tenant,
    get_customer_service,
    get_customer_writer_tenant,
)
from ticketing.schemas.customer import (
    CustomerCreateRequest,
    CustomerResponse,
    CustomerUpdateRequest,
)
from ticketing.services.customer import (
    CustomerChanges,
    CustomerEmailConflictError,
    CustomerNotFoundError,
    CustomerService,
    CustomerServiceError,
)
from ticketing.services.tenant import CurrentTenant

router = APIRouter(tags=["customers"])


@router.post(
    "/tenants/{tenant_id}/customers",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_customer(
    tenant_id: UUID,
    request: CustomerCreateRequest,
    context: Annotated[CurrentTenant, Depends(get_customer_writer_tenant)],
    service: Annotated[CustomerService, Depends(get_customer_service)],
) -> CustomerResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        customer = await service.create(
            context,
            name=request.name,
            email=str(request.email),
            phone=request.phone,
        )
    except CustomerServiceError as exc:
        raise customer_http_exception(exc) from exc
    return CustomerResponse.model_validate(customer)


@router.get("/tenants/{tenant_id}/customers", response_model=list[CustomerResponse])
async def list_customers(
    tenant_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_customer_reader_tenant)],
    service: Annotated[CustomerService, Depends(get_customer_service)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[CustomerResponse]:
    ensure_path_tenant(tenant_id, context)
    customers = await service.list(context, offset=offset, limit=limit)
    return [CustomerResponse.model_validate(customer) for customer in customers]


@router.get(
    "/tenants/{tenant_id}/customers/{customer_id}",
    response_model=CustomerResponse,
)
async def get_customer(
    tenant_id: UUID,
    customer_id: UUID,
    context: Annotated[CurrentTenant, Depends(get_customer_reader_tenant)],
    service: Annotated[CustomerService, Depends(get_customer_service)],
) -> CustomerResponse:
    ensure_path_tenant(tenant_id, context)
    try:
        customer = await service.get(context, customer_id)
    except CustomerServiceError as exc:
        raise customer_http_exception(exc) from exc
    return CustomerResponse.model_validate(customer)


@router.patch(
    "/tenants/{tenant_id}/customers/{customer_id}",
    response_model=CustomerResponse,
)
async def update_customer(
    tenant_id: UUID,
    customer_id: UUID,
    request: CustomerUpdateRequest,
    context: Annotated[CurrentTenant, Depends(get_customer_writer_tenant)],
    service: Annotated[CustomerService, Depends(get_customer_service)],
) -> CustomerResponse:
    ensure_path_tenant(tenant_id, context)
    changes = CustomerChanges(
        fields=frozenset(request.model_fields_set),
        name=request.name,
        email=None if request.email is None else str(request.email),
        phone=request.phone,
        is_active=request.is_active,
    )
    try:
        customer = await service.update(context, customer_id, changes)
    except CustomerServiceError as exc:
        raise customer_http_exception(exc) from exc
    return CustomerResponse.model_validate(customer)


def customer_http_exception(error: CustomerServiceError) -> HTTPException:
    if isinstance(error, CustomerNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "customer_not_found", "message": "Customer not found"},
        )
    if isinstance(error, CustomerEmailConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "customer_email_conflict",
                "message": "Customer email already exists in this tenant",
            },
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "customer_conflict", "message": "Customer operation conflicts"},
    )
