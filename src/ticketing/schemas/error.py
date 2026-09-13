from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    code: str
    message: str
    errors: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    detail: ErrorDetail
    correlation_id: str
