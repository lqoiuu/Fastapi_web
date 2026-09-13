from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ticketing.api.dependencies import (
    auth_http_exception,
    get_auth_service,
    get_login_rate_limiter,
)
from ticketing.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPairResponse,
    UserResponse,
)
from ticketing.services.auth import AuthService, AuthServiceError
from ticketing.services.rate_limit import LoginRateLimiter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    try:
        user = await service.register(request.email, request.password.get_secret_value())
    except AuthServiceError as exc:
        raise auth_http_exception(exc) from exc
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenPairResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    rate_limiter: Annotated[LoginRateLimiter, Depends(get_login_rate_limiter)],
) -> TokenPairResponse:
    client_host = http_request.client.host if http_request.client is not None else "unknown"
    rate_limit = await rate_limiter.check(email=str(request.email), client_host=client_host)
    response.headers["X-RateLimit-Limit"] = str(rate_limit.limit)
    response.headers["X-RateLimit-Remaining"] = str(rate_limit.remaining)
    if rate_limit.degraded:
        response.headers["X-RateLimit-Policy"] = "degraded"
    if not rate_limit.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limit_exceeded", "message": "Too many login attempts"},
            headers={
                "Retry-After": str(rate_limit.retry_after_seconds),
                "X-RateLimit-Limit": str(rate_limit.limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    try:
        pair = await service.login(request.email, request.password.get_secret_value())
    except AuthServiceError as exc:
        raise auth_http_exception(exc) from exc
    return TokenPairResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(
    request: RefreshRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenPairResponse:
    try:
        pair = await service.refresh(request.refresh_token)
    except AuthServiceError as exc:
        raise auth_http_exception(exc) from exc
    return TokenPairResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(
    request: LogoutRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    try:
        await service.logout(request.refresh_token)
    except AuthServiceError as exc:
        raise auth_http_exception(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
