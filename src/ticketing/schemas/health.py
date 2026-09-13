from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthStatus(StrEnum):
    OK = "ok"


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: HealthStatus
    service: str
    environment: Literal["local", "test", "staging", "production"]


class ApiVersionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_version: Literal["v1"]


class CacheHealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "degraded"]
    hits: int
    misses: int
    writes: int
    invalidations: int
    failures: int
    rate_limited: int
