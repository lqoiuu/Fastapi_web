from fastapi import APIRouter

from ticketing.api.v1.auth import router as auth_router
from ticketing.api.v1.background_jobs import router as background_jobs_router
from ticketing.api.v1.customers import router as customers_router
from ticketing.api.v1.tenants import router as tenants_router
from ticketing.api.v1.ticket_collaboration import router as ticket_collaboration_router
from ticketing.api.v1.tickets import router as tickets_router
from ticketing.api.v1.users import router as users_router
from ticketing.schemas.health import ApiVersionResponse

router = APIRouter()


@router.get("/status", response_model=ApiVersionResponse, tags=["system"])
async def api_status() -> ApiVersionResponse:
    """Expose the active public API version."""
    return ApiVersionResponse(api_version="v1")


router.include_router(auth_router)
router.include_router(background_jobs_router)
router.include_router(customers_router)
router.include_router(ticket_collaboration_router)
router.include_router(tenants_router)
router.include_router(tickets_router)
router.include_router(users_router)
