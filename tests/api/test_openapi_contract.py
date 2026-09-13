from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.cache.backend import CacheMetrics, RateLimitDecision
from ticketing.core.config import Settings, get_settings
from ticketing.db.session import DatabaseProtocol
from ticketing.main import create_app
from ticketing.schemas.error import ErrorResponse


class ContractDatabase:
    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        yield cast(AsyncSession, object())

    async def ping(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


class ContractCache:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        return RateLimitDecision(True, limit, limit, 0)

    async def ping(self) -> None:
        return None

    def metrics_snapshot(self) -> CacheMetrics:
        return CacheMetrics(0, 0, 0, 0, 0, 0)

    async def close(self) -> None:
        return None


def build_contract_app() -> FastAPI:
    settings = Settings(
        environment="test",
        auth_secret_key=SecretStr("contract-test-secret-at-least-32-characters"),
    )
    database: DatabaseProtocol = ContractDatabase()
    app = create_app(settings, database=database, cache=ContractCache())
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/contract-conflict", include_in_schema=False)
    async def contract_conflict() -> None:
        raise HTTPException(
            status_code=409,
            detail={"code": "contract_conflict", "message": "Contract conflict"},
        )

    return app


def test_openapi_keeps_core_paths_security_and_headers_stable() -> None:
    schema = build_contract_app().openapi()
    paths = schema["paths"]

    expected_operations = {
        "/api/v1/auth/register": {"post"},
        "/api/v1/auth/login": {"post"},
        "/api/v1/users/me": {"get"},
        "/api/v1/users/me/password": {"put"},
        "/api/v1/tenants": {"get", "post"},
        "/api/v1/tenants/{tenant_id}/tickets": {"get", "post"},
        "/api/v1/tenants/{tenant_id}/background-jobs/ticket-exports": {"post"},
        "/api/v1/tenants/{tenant_id}/background-jobs/{job_id}": {"get"},
    }
    for path, methods in expected_operations.items():
        assert path in paths
        assert methods <= set(paths[path])

    security_schemes = schema["components"]["securitySchemes"]
    assert security_schemes["HTTPBearer"] == {"type": "http", "scheme": "bearer"}
    assert paths["/api/v1/users/me"]["get"]["security"] == [{"HTTPBearer": []}]

    create_ticket = paths["/api/v1/tenants/{tenant_id}/tickets"]["post"]
    header_parameters = {
        parameter["name"]: parameter
        for parameter in create_ticket["parameters"]
        if parameter["in"] == "header"
    }
    assert {"Idempotency-Key", "x-tenant-id"} <= set(header_parameters)
    idempotency_schema = header_parameters["Idempotency-Key"]["schema"]
    assert idempotency_schema["minLength"] == 8
    assert idempotency_schema["maxLength"] == 128


def test_openapi_keeps_key_success_response_models_stable() -> None:
    paths = build_contract_app().openapi()["paths"]

    contracts = {
        ("/api/v1/auth/login", "post", "200"): "TokenPairResponse",
        ("/api/v1/tenants/{tenant_id}/tickets", "post", "201"): "TicketResponse",
        ("/api/v1/tenants/{tenant_id}/tickets", "get", "200"): "TicketPageResponse",
        (
            "/api/v1/tenants/{tenant_id}/background-jobs/ticket-exports",
            "post",
            "202",
        ): "BackgroundJobResponse",
    }
    for (path, method, status_code), model_name in contracts.items():
        response_schema = paths[path][method]["responses"][status_code]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["$ref"] == f"#/components/schemas/{model_name}"


def test_key_error_responses_follow_the_uniform_contract() -> None:
    app = build_contract_app()
    with TestClient(app) as client:
        responses = [
            client.get("/api/v1/users/me"),
            client.get("/missing-contract-path"),
            client.get("/contract-conflict"),
            client.post(
                "/api/v1/auth/register",
                json={"email": "not-an-email", "password": "short"},
            ),
        ]

    assert [response.status_code for response in responses] == [401, 404, 409, 422]
    for response in responses:
        error = ErrorResponse.model_validate(response.json())
        assert error.detail.code
        assert error.detail.message
        assert error.correlation_id == response.headers["X-Correlation-ID"]
