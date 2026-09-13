import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ticketing.core.context import get_or_create_correlation_id

logger = logging.getLogger(__name__)


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


async def http_exception_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    detail = _normalized_http_detail(error.detail, error.status_code)
    request.state.error_type = detail["code"]
    return JSONResponse(
        status_code=error.status_code,
        content={
            "detail": jsonable_encoder(detail),
            "correlation_id": get_or_create_correlation_id(),
        },
        headers=error.headers,
    )


async def validation_exception_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    error_type = "request_validation_error"
    request.state.error_type = error_type
    safe_errors = [
        {
            "location": [str(part) for part in item.get("loc", ())],
            "message": str(item.get("msg", "Invalid value")),
            "type": str(item.get("type", "value_error")),
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": {
                "code": error_type,
                "message": "Request validation failed",
                "errors": safe_errors,
            },
            "correlation_id": get_or_create_correlation_id(),
        },
    )


async def unhandled_exception_handler(request: Request, error: Exception) -> JSONResponse:
    error_type = "internal_server_error"
    request.state.error_type = error_type
    logger.exception(
        "Unhandled request exception",
        exc_info=error,
        extra={"error_type": error.__class__.__name__},
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": {"code": error_type, "message": "Internal server error"},
            "correlation_id": get_or_create_correlation_id(),
        },
    )


def _normalized_http_detail(detail: Any, status_code: int) -> dict[str, Any]:
    if isinstance(detail, dict):
        normalized = dict(detail)
        code = normalized.get("code")
        message = normalized.get("message")
        normalized["code"] = code if isinstance(code, str) else f"http_{status_code}"
        normalized["message"] = message if isinstance(message, str) else "Request failed"
        return normalized
    return {
        "code": f"http_{status_code}",
        "message": detail if isinstance(detail, str) else "Request failed",
    }
