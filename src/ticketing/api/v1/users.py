from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from ticketing.api.dependencies import (
    auth_http_exception,
    get_auth_service,
    get_current_user,
)
from ticketing.models.user import User
from ticketing.schemas.auth import PasswordChangeRequest, UserResponse
from ticketing.services.auth import AuthService, AuthServiceError

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
async def read_me(user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return UserResponse.model_validate(user)


@router.put("/me/password", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def change_password(
    request: PasswordChangeRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    try:
        await service.change_password(
            user,
            request.current_password.get_secret_value(),
            request.new_password.get_secret_value(),
        )
    except AuthServiceError as exc:
        raise auth_http_exception(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
